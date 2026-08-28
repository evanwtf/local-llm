"""The results schema: one place that decides what a trial row looks like.

Every row in results.jsonl is the only durable evidence that a trial ran. The
file is append-only and rows are never deleted -- a run taken under the wrong
conditions is *marked*, not removed, because the fact that it happened is itself
evidence.

That convention only works if there is exactly one way to mark a row. There were
four (`excluded`, `exclude_reason`, `excluded_reason`, `contaminated`,
`confound`), and an analysis that filtered on one of them silently counted the
other fifteen rows as good data. This module exists so no analysis ever hand-
rolls that filter again: read with `load()`, test with `is_excluded()`.

Schema v2 rules, for rows written from 2026-08-28:

  * `excluded: bool` and `exclusion_reason: str | None` are ALWAYS present.
    Absent is not the same as false.
  * The legacy keys are violations. They are still honoured on *read*, forever,
    because v1 rows are evidence and are not rewritten.
  * A row that fails validation is still written, stamped `schema_valid: false`
    with the specific violations. A trial costs up to half an hour; losing one
    to a schema bug is worse than storing a flagged row.
"""
from __future__ import annotations

import json
import logging
import pathlib
import time
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2

#: Keys that meant "do not trust this row" before v2. Honoured on read forever;
#: rejected on write so a sixth variant cannot appear.
LEGACY_EXCLUSION_KEYS: tuple[str, ...] = (
    "excluded",
    "exclude_reason",
    "excluded_reason",
    "contaminated",
    "confound",
)

#: Present on every row, whatever happened to the trial.
REQUIRED: dict[str, type | tuple[type, ...]] = {
    "schema_version": int,
    "task": str,
    "backend": str,
    "client": str,
    "trial": int,
    "started": str,
    "finished": str,
    "model": str,
    "context_tokens": int,
    "env": dict,
    "excluded": bool,
    "exclusion_reason": (str, type(None)),
}

#: Additionally required once the trial produced a verdict -- i.e. it was not a
#: dry run and did not die before pytest ran.
REQUIRED_WITH_VERDICT: dict[str, type | tuple[type, ...]] = {
    "passed": bool,
    "wall_seconds": (int, float),
    "pytest": str,
    "touched_tests": bool,
    "source_repo_intact": bool,
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def new_row(
    *,
    task: str,
    backend: str,
    client: str,
    trial: int,
    model: str,
    context_tokens: int,
    effort: str | None,
    env: dict[str, Any],
) -> dict[str, Any]:
    """Start a row. Both exclusion keys are set explicitly from birth."""
    return {
        "schema_version": SCHEMA_VERSION,
        "task": task,
        "backend": backend,
        "client": client,
        "trial": trial,
        "started": _now(),
        "model": model,
        "context_tokens": context_tokens,
        "effort": effort,
        "env": env,
        "excluded": False,
        "exclusion_reason": None,
    }


def _type_ok(value: Any, want: type | tuple[type, ...]) -> bool:
    # bool is a subclass of int; an int field must not silently accept True.
    if want is int and isinstance(value, bool):
        return False
    return isinstance(value, want)


def validate(row: dict[str, Any]) -> list[str]:
    """Return a list of violations. Empty means the row conforms to v2."""
    errors: list[str] = []

    for key, want in REQUIRED.items():
        if key not in row:
            errors.append(f"missing required field: {key}")
        elif not _type_ok(row[key], want):
            errors.append(
                f"wrong type for {key}: {type(row[key]).__name__}"
            )

    has_verdict = not row.get("dry_run") and "error" not in row
    if has_verdict:
        for key, want in REQUIRED_WITH_VERDICT.items():
            if key not in row:
                errors.append(f"missing required field: {key} (trial has a verdict)")
            elif not _type_ok(row[key], want):
                errors.append(f"wrong type for {key}: {type(row[key]).__name__}")

    for key in LEGACY_EXCLUSION_KEYS:
        if key == "excluded":
            continue
        if key in row:
            errors.append(
                f"legacy exclusion key {key}: use excluded + exclusion_reason"
            )

    return errors


def write_row(row: dict[str, Any], path: pathlib.Path) -> dict[str, Any]:
    """Append one row, stamped with its own validity. Never overwrites.

    A failing row is written anyway. Losing an expensive trial to a schema bug
    is worse than storing one that is loudly marked as broken.
    """
    errors = validate(row)
    row["schema_valid"] = not errors
    row["schema_errors"] = errors
    if errors:
        logger.error(
            "%s-%s-%s: row violates schema v%d: %s",
            row.get("task"), row.get("backend"), row.get("trial"),
            SCHEMA_VERSION, "; ".join(errors),
        )
    with pathlib.Path(path).open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def is_excluded(row: dict[str, Any]) -> bool:
    """True if this row should be kept out of any aggregate.

    Understands both v2 and every v1 variant. `error` is deliberately NOT an
    exclusion: a timeout is a real outcome and the trial genuinely failed.
    """
    if row.get("excluded"):
        return True
    return any(row.get(k) for k in LEGACY_EXCLUSION_KEYS if k != "excluded")


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    """Return a v1 or v2 row in v2 shape. Does not touch the file on disk."""
    out = dict(row)
    out.setdefault("schema_version", 1)
    # `client` was added once there was more than one; rows without it are older
    # than Codex and OpenCode ever being wired up.
    out.setdefault("client", "claude")

    excluded = is_excluded(out)
    out["excluded"] = excluded
    if excluded and not out.get("exclusion_reason"):
        for key in LEGACY_EXCLUSION_KEYS:
            if key == "excluded":
                continue
            reason = out.get(key)
            if isinstance(reason, str) and reason:
                out["exclusion_reason"] = reason
                break
        else:
            out.setdefault("exclusion_reason", "excluded (v1, no reason recorded)")
    else:
        out.setdefault("exclusion_reason", None)
    return out


def load(path: pathlib.Path) -> list[dict[str, Any]]:
    """Read every row, normalised to v2 shape in memory.

    This is the only supported way to read results.jsonl. Reading it by hand is
    how the four exclusion keys went unnoticed.
    """
    rows: list[dict[str, Any]] = []
    with pathlib.Path(path).open() as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(normalize(json.loads(line)))
            except json.JSONDecodeError:
                logger.error("%s line %d: unparseable, skipped", path, n)
    return rows


def usable(path: pathlib.Path) -> list[dict[str, Any]]:
    """Every row that belongs in an aggregate: normalised, minus exclusions."""
    return [r for r in load(path) if not r["excluded"]]


def verdict(row: dict[str, Any]) -> bool:
    """Did this trial actually restore the function? One place decides.

    A trial passes only if the oracle passed *and* every guard held. It fails if
    it timed out -- a timeout writes `error` and no `passed` key, and reading
    verdicts with `if "passed" in row` drops those rows rather than counting
    them. That is how a 13/16 backend reached a published table as 13/13.

    Raises ValueError for a dry run, which is a setup check and has no verdict.
    """
    if row.get("dry_run"):
        raise ValueError("a dry run has no verdict; filter it out with trials()")
    if not row.get("passed"):
        return False
    if row.get("touched_tests"):
        return False  # edited the oracle; the pass is meaningless
    if row.get("source_repo_intact") is False:
        return False  # escaped the sandbox
    if row.get("control_fails_as_expected") is False:  # noqa: SIM103
        return False  # the excision was invisible to the tests
    return True


def trials(path: pathlib.Path) -> list[dict[str, Any]]:
    """Every real benchmark trial: usable rows, minus dry runs.

    Timeouts are kept -- they are failures, not absences. Pair this with
    `verdict()`; do not test `row["passed"]` directly.
    """
    return [r for r in usable(path) if not r.get("dry_run")]
