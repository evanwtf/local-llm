"""Validate EVERY committed hardware ledger, independent of the runner (#394).

The invariant tests keyed on `results.default_path()` resolve to the *runner's*
hardware, so the Linux CI runner -- which is neither the M5 nor the DGX -- never
checks the committed `hardware/*/results.jsonl` ledgers. A purely machine-local
break ships green (the 2026-09-14 red main was caught only because the archive
test happens not to be results-gated). This validator reads every committed
ledger by path and asserts the invariants regardless of which box it runs on, so
CI becomes the real cross-machine net; it is also fast enough to reuse in
pre-commit.

    uv run python scripts/validate_ledgers.py           # all committed ledgers
    uv run python scripts/validate_ledgers.py --files hardware/<dir>/results.jsonl

Exit 0 = every ledger holds; exit 1 = one or more violations, printed with the
ledger and (where meaningful) the offending row.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import archive_pre_dir_rows as apdr  # is_pre_dir + fixed_commits (unions #355 allowlist)
import machines

ARCHIVE = REPO / "docs" / "archive" / "results-opencode-pre-dir.jsonl"


def _key(row: dict) -> tuple:
    return (
        row.get("task"),
        row.get("backend"),
        row.get("client"),
        row.get("trial"),
        row.get("started"),
    )


def _parse(path: pathlib.Path) -> tuple[list[dict], list[str]]:
    """Rows and parse errors. A malformed line is a violation, not a crash."""
    rows: list[dict] = []
    errs: list[str] = []
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            errs.append(f"line {i}: malformed JSON ({e})")
    return rows, errs


def _archive_keys() -> set[tuple]:
    if not ARCHIVE.exists():
        return set()
    out = set()
    for line in ARCHIVE.read_text().splitlines():
        if line.strip():
            try:
                out.add(_key(json.loads(line)))
            except json.JSONDecodeError:
                pass
    return out


def validate_ledger(
    path: pathlib.Path, after: set[str], archive_keys: set[tuple]
) -> list[str]:
    """Every violation in one ledger. Empty means it holds."""
    v: list[str] = []
    directory = path.parent.name

    # 1. The directory must be a registered machine (#20/#394). A glob cannot
    #    prove a row belongs to a machine; the registry is the authority.
    machine = machines.by_directory(directory)
    if machine is None:
        v.append(
            f"unregistered ledger directory {directory!r}: not in scripts/machines.py"
        )

    rows, parse_errs = _parse(path)
    v.extend(parse_errs)

    seen: dict[tuple, int] = {}
    for n, row in enumerate(rows, start=1):
        # 2. Schema conformance is a WRITE-time concern (results.validate stamps
        #    `schema_valid` per row) with grandfathered legacy rows, so this
        #    validator does not re-run it -- doing so would fail CI on committed
        #    history. It checks the STRUCTURAL, cross-machine invariants the
        #    runner-gated tests miss. It does flag a row that CLAIMS to conform
        #    but carries the flag as false, which no committed row should.
        if row.get("schema_valid") is False:
            v.append(f"row {n} ({_key(row)}): committed with schema_valid=false")
        # 3. No duplicate rows.
        k = _key(row)
        if k in seen:
            v.append(f"row {n}: duplicate of row {seen[k]}: {k}")
        else:
            seen[k] = n
        # 4. No pre-dir OpenCode straggler left in the active ledger -- these
        #    belong in the archive; a straggler is what broke main on 2026-09-14.
        if apdr.is_pre_dir(json.dumps(row), after):
            v.append(f"row {n} ({k}): pre---dir OpenCode row belongs in the archive")
        # 5. No overlap with the archive.
        if k in archive_keys:
            v.append(f"row {n} ({k}): also present in the pre-dir archive (overlap)")
        # 6. Machine belonging: when a row records its arch, it must match the
        #    directory's registered machine. Older rows carry no arch -- a
        #    "when present, must match" check, so a Mac row in the DGX ledger is
        #    caught while pre-provenance rows are not punished for silence.
        if machine is not None:
            arch = (row.get("env") or {}).get("arch")
            if arch and machine.arch and arch != machine.arch:
                v.append(
                    f"row {n} ({k}): arch {arch!r} != {directory}'s {machine.arch!r} "
                    f"(cross-machine contamination)"
                )
    return v


def discover(repo: pathlib.Path) -> list[pathlib.Path]:
    return sorted((repo / "hardware").glob("*/results.jsonl"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--files",
        nargs="*",
        type=pathlib.Path,
        help="specific ledgers to check (default: every hardware/*/results.jsonl)",
    )
    args = p.parse_args(argv)

    ledgers = args.files if args.files else discover(REPO)
    if not ledgers:
        print("no ledgers found under hardware/*/results.jsonl", file=sys.stderr)
        return 0  # a checkout with no ledgers is not a failure (a fresh clone/CI)

    after = apdr.fixed_commits(REPO)
    archive_keys = _archive_keys()

    total = 0
    for path in ledgers:
        if not path.exists():
            print(f"MISSING {path}", file=sys.stderr)
            total += 1
            continue
        violations = validate_ledger(path, after, archive_keys)
        rel = path.relative_to(REPO) if path.is_absolute() else path
        if violations:
            total += len(violations)
            print(f"FAIL {rel}: {len(violations)} violation(s)")
            for viol in violations:
                print(f"  - {viol}")
        else:
            print(f"ok   {rel}")

    if total:
        print(f"\n{total} violation(s) across the committed ledgers", file=sys.stderr)
        return 1
    print("\nall committed ledgers hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
