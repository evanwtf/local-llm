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


def client_version(client: str, env: dict[str, Any]) -> str | None:
    """The version of the client this row actually used, or None.

    `env` holds a version for every client on the machine, not just the one
    that ran. The strings are whatever each tool prints -- "1.18.27" from
    OpenCode, "codex-cli 0.152.0", "aider 0.86.2", "2.1.260 (Claude Code)" --
    and they are stored unchanged. Normalising them here would invent a format
    and lose what the tool actually said, which is the thing a later reader
    needs in order to compare against a release note.
    """
    value = env.get(client)
    return value if isinstance(value, str) and value.strip() else None


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
    run_position: int | None = None,
    run_arms: int | None = None,
    target_layout: str = "legacy",
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
        # #131. The client version was always in `env`, but keyed by client
        # name alongside every other client's version -- so reading it back
        # meant joining `client` to `env` and knowing to do so. #104 measured
        # OpenCode 1.18.26 -> 1.18.27 roughly doubling median turns with
        # everything else held; a finding like that has to be applicable to one
        # row without a join. Not in REQUIRED: 979 existing rows predate it and
        # `validate` runs on read, so demanding it would retroactively condemn
        # them. Absent means "not established", never "same as now".
        "client_version": client_version(client, env),
        # #130. Throughput declines across a measurement window, so whichever
        # arm always runs last is penalised -- @adamlawi measured that bias on
        # antirez/ds4#952 as larger than three of the four effects being
        # compared. run.py now alternates the order between trials, but a row
        # that does not say where it sat cannot be checked for the bias
        # afterwards, and no existing row can be retro-corrected. None means
        # the order was not recorded, which is what every row before this is.
        "run_position": run_position,
        "run_arms": run_arms,
        # #146. Which checkout a row was built from: "legacy" parks the
        # operator's checkout and stands the export at the path the model
        # guesses; "sandbox" builds from the harness's own clone and denies
        # the guessed path instead. The two layouts give the agent different
        # answers to its guess, so their pass rates are not one cohort --
        # pooled rows would measure the harness, not the model. Default
        # "legacy" is what every row so far is. Not in REQUIRED: rows before
        # 2026-09-05 predate it.
        "target_layout": target_layout,
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
            errors.append(f"wrong type for {key}: {type(row[key]).__name__}")

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
    # #131: the clients are no longer pinned -- this machine is a daily
    # driver and runs the current version of everything -- so `client_version`
    # on the row is the ONLY thing that makes a comparison recoverable across
    # an update. That cannot rest on discipline, so it is enforced here rather
    # than described in a comment.
    #
    # Excluded, not refused. Losing an expensive trial to a missing field is
    # worse than storing one that can never enter an aggregate, which is the
    # same trade this function already makes for a schema violation. `validate`
    # runs on read as well, so the field stays out of REQUIRED: the 979 rows
    # that predate it are grandfathered and are never re-written.
    if not row.get("client_version"):
        logger.error(
            "%s-%s-%s: no client_version -- excluding the row. Nothing pins "
            "the client (#131), so a row that does not say which version ran "
            "cannot be compared with anything.",
            row.get("task"),
            row.get("backend"),
            row.get("trial"),
        )
        row["excluded"] = True
        if not row.get("exclusion_reason"):
            row["exclusion_reason"] = (
                "no client_version recorded; clients are not pinned (#131)"
            )
    errors = validate(row)
    row["schema_valid"] = not errors
    row["schema_errors"] = errors
    if errors:
        logger.error(
            "%s-%s-%s: row violates schema v%d: %s",
            row.get("task"),
            row.get("backend"),
            row.get("trial"),
            SCHEMA_VERSION,
            "; ".join(errors),
        )
    path = pathlib.Path(path)
    # A new machine's directory may exist without its results file, or not
    # exist at all. Creating it here means a per-machine run needs no setup
    # step that someone can forget (#85).
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def is_excluded(row: dict[str, Any]) -> bool:
    """True if this row should be kept out of any aggregate.

    Understands both v2 and every v1 variant. `error` is deliberately NOT an
    exclusion: a timeout is a real outcome and the trial genuinely failed.
    """
    if row.get("excluded"):
        return True
    if _client_never_ran(row):
        return True
    return any(row.get(k) for k in LEGACY_EXCLUSION_KEYS if k != "excluded")


def _client_never_ran(row: dict[str, Any]) -> bool:
    """The client crashed, so no model attempt was made.

    `agent_error` means the harness could not parse a result out of the client
    -- it died at config, crashed, or returned an error object. That is not a
    task failure and must not be counted as one.

    It was counted as one three times on 2026-08-31: 16 opus5 rows made the
    hosted reference read 28/44 (64%) when its record was 28/29, and an
    OpenCode row that died in 0.7s with `UnknownError: Unexpected server error`
    would have joined a genuine 0/9 as if it were a tenth failure.

    Guarded by `not passed`: if the client errored and the oracle passed
    anyway, the trial produced a real result and stays.
    """
    return bool(row.get("agent_error")) and not row.get("passed")


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    """Return a v1 or v2 row in v2 shape. Does not touch the file on disk."""
    out = dict(row)
    out.setdefault("schema_version", 1)
    # `client` was added once there was more than one; rows without it are older
    # than Codex and OpenCode ever being wired up.
    out.setdefault("client", "claude")

    excluded = is_excluded(out)
    out["excluded"] = excluded
    if excluded and not out.get("exclusion_reason") and _client_never_ran(out):
        out["exclusion_reason"] = (
            "agent_error: the client never ran, so no model attempt was made"
        )
    elif excluded and not out.get("exclusion_reason"):
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

    A missing file is an empty history, not an error. The first thing a new
    machine does is write to a results file that does not exist yet -- which is
    exactly what `--results hardware/<machine>/results.jsonl` creates (#85) --
    and raising there crashed the desktop's first real run after the smoke gate
    had already passed.
    """
    rows: list[dict[str, Any]] = []
    path = pathlib.Path(path)
    if not path.exists():
        return rows
    with path.open() as fh:
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


# --- one file, one machine (#20) ------------------------------------------

HARDWARE_KEYS = ("arch", "cpu")


def hardware_of(row: dict[str, Any]) -> tuple[str, ...] | None:
    """The hardware identity a row claims, or None when it does not say."""
    env = row.get("env") or {}
    got = tuple(str(env[k]) for k in HARDWARE_KEYS if env.get(k))
    return got if len(got) == len(HARDWARE_KEYS) else None


def foreign_hardware(rows: list[dict[str, Any]], facts: dict[str, object]) -> set:
    """Hardware identities already in this file that are not `facts`.

    This project's premise is one machine, so every comparison in
    results.jsonl shares a hardware baseline. #20 adds a second machine on
    purpose, and the first time the harness ran there it appended 13 rows to
    the tracked results.jsonl -- caught only because `git pull` refused to
    merge over them. Nothing in the harness objected.

    Mixing hardware does not corrupt a row; it corrupts every comparison drawn
    across the file, silently and after the fact.
    """
    mine = tuple(str(facts[k]) for k in HARDWARE_KEYS if facts.get(k))
    if len(mine) != len(HARDWARE_KEYS):
        return set()
    seen = {h for h in (hardware_of(r) for r in rows) if h is not None}
    return seen - {mine}


# --- where this machine's rows live (#85) -----------------------------------

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent


def machine_dir() -> pathlib.Path:
    """`hardware/<this machine>/`, derived rather than typed.

    The name comes from `scripts/hardware_id.py` so it cannot disagree with the
    hardware it claims to describe.
    """
    import sys

    sys.path.insert(0, str(_REPO / "scripts"))
    import hardware_id

    facts, platform = hardware_id.facts_for_this_machine()
    return _REPO / "hardware" / hardware_id.directory_name(facts, platform)


def default_path() -> pathlib.Path:
    """This machine's results file.

    One file, one hardware baseline (#20). Every comparison in a results file
    assumes a shared machine, so the path is derived from the machine rather
    than being a constant that a second machine would silently inherit.
    """
    return machine_dir() / "results.jsonl"
