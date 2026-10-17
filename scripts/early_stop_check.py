"""Would the #762 early stop end this run now? For a run already in flight.

**Why this exists.** ``run.py`` checks the early stop only for the run it
launched, and only when the stop is on. A run started with
``--no-early-stop`` (the #1086 re-checks, before the operator turned the stop
on for them on 2026-10-16) cannot switch it on mid-run. This applies the same
rule, ``screening.should_stop``, to the rows a run has written so far, so an
operator can stop it by hand when the rule says so.

The run is the latest one of ``--backend`` in ``--set`` (replay groups the
replay and hard tasks, as one launch runs both). The leader is
``--leader`` if given, else the machine's ``[early_stop.leader]`` pin in
``tasks.toml``, else the automatic choice. The planned tasks are the
leader's tasks in that set, and the planned trials ``--trials`` per task.

Exit status: 0 keep going, 3 the rule says stop, 1 no leader or no run.

    uv run python scripts/early_stop_check.py --backend <name> --set replay
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import tomllib
from collections.abc import Sequence
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "benchmarks" / "agent"))

import provenance
import results
import screening

logger = logging.getLogger(__name__)

Row = dict[str, Any]
STOP = 3


def latest_run(
    rows: Sequence[Row], backend: str, family: str, suites: dict[str, str]
) -> tuple[str, list[Row]] | None:
    """The newest run of ``backend`` in ``family``, as (key, rows)."""
    found = [
        (key, rr)
        for key, rr in screening.runs(rows, suites).items()
        if rr
        and rr[0].get("backend") == backend
        and screening._family(rr[0], suites) == family
    ]
    if not found:
        return None
    return max(found, key=lambda kv: min(str(r.get("started", "")) for r in kv[1]))


def check(
    rows: Sequence[Row],
    backend: str,
    family: str,
    suites: dict[str, str],
    pinned: str | None,
    client: str = "opencode",
    trials: int = 3,
    timeout: float = 1800.0,
) -> tuple[int, str]:
    """(exit status, the reason with its numbers)."""
    run = latest_run(rows, backend, family, suites)
    if run is None:
        return 1, f"no {family} run of {backend} in the ledger"
    key, mine = run
    ids = {id(r) for r in mine}
    history = [r for r in rows if id(r) not in ids]
    pool = screening.leader_pool(history, pinned, suites) if pinned else history
    family_rows = [r for r in pool if screening._family(r, suites) == family]
    tasks = sorted({r["task"] for r in family_rows if r.get("client") == client})
    lead = screening.pick_leader(pool, tasks, client, None, timeout, suites=suites)
    if lead is None:
        return 1, f"no leader ran all {len(tasks)} {family} tasks on {client}"
    stop, reason = screening.should_stop(
        mine, lead, tasks, len(tasks) * trials, timeout
    )
    return (STOP if stop else 0), f"{key}: {len(mine)} rows; {reason}"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--results", type=pathlib.Path, default=results.default_path())
    p.add_argument("--backend", required=True)
    p.add_argument("--set", choices=["standard", "replay"], default="replay")
    p.add_argument("--leader", help="batch, run key or backend; default the pin")
    p.add_argument("--client", default="opencode")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--timeout", type=float, default=1800.0)
    p.add_argument(
        "--tasks-file",
        type=pathlib.Path,
        default=HERE.parent / "benchmarks" / "agent" / "tasks.toml",
    )
    args = p.parse_args()

    provenance.configure()
    cfg = tomllib.loads(args.tasks_file.read_text())
    pinned = args.leader or screening.default_leader(cfg, args.results.parent.name)
    status, reason = check(
        results.trials(args.results),
        args.backend,
        args.set,
        screening.suite_map(cfg),
        pinned,
        args.client,
        args.trials,
        args.timeout,
    )
    log = logger.warning if status else logger.info
    log("%s (leader pin: %s)", reason, pinned or "none")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
