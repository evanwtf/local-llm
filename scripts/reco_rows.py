#!/usr/bin/env python3
"""The numbers a RECOMMENDATIONS row quotes, regenerated from a ledger. #524

The per-machine RECOMMENDATIONS files are hand-written prose around a small
table, and their figures drift: on 2026-09-18 the DGX file still quoted the A3B
leader at 56/57 and a 35.0 s median from #335, while the ledger held 223 of its
trials. This prints, for each named backend, exactly the columns such a row
quotes -- computed the way `gen_tables.py` computes the generated tables, so a
row and the generated table can never disagree about what "median" means:

- **pass**: passed / usable trials, OpenCode, post-`--dir`-fix harness only
  (`gen_tables.valid_opencode`), dry runs and excluded rows dropped
  (`results.trials`).
- **median / worst**: wall seconds of the *excision* tasks that *passed*
  (`gen_tables._timed`). Script tasks and failures are left out, because a
  trial that dies early is quick (#142).
- **turns**: median agent turns over the same timed trials.

    uv run python scripts/reco_rows.py --results hardware/Cortex-X925-128GB-GB10/results.jsonl \\
        qwen36a3bnvfp4dgx qwen3827bsglangdsparknothinkdgx
"""

from __future__ import annotations

import argparse
import pathlib
import statistics
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import gen_tables
import results


def row(rows: list[dict], backend: str, client: tuple[str, ...] | None = None) -> dict:
    """The quoted columns for one backend on one client machine.

    `client` is a `results.client_identity`. Passing None keeps every row,
    which is only safe when the ledger holds a single client -- these numbers
    are pasted into RECOMMENDATIONS.md as headline figures with no caveat
    mechanism, so a pooled median here is the least visible way to be wrong
    (#562).
    """
    mine = [r for r in rows if r.get("backend") == backend]
    if client is not None:
        mine = [r for r in mine if results.client_identity(r) == client]
    timed = [
        r
        for r in gen_tables._excision(mine)
        if r.get("passed") and r.get("wall_seconds")
    ]
    walls = [r["wall_seconds"] for r in timed]
    turns = [r["num_turns"] for r in timed if r.get("num_turns") is not None]
    return {
        "backend": backend,
        "client": client,
        "passed": sum(1 for r in mine if r.get("passed")),
        "trials": len(mine),
        "median_s": round(statistics.median(walls), 1) if walls else None,
        "worst_s": round(max(walls), 1) if walls else None,
        "turns": statistics.median(turns) if turns else None,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("backends", nargs="+")
    p.add_argument("--results", type=pathlib.Path, default=None)
    args = p.parse_args(argv)
    rows = gen_tables.valid_opencode(gen_tables.load(args.results))
    wanted = [r for r in rows if r.get("backend") in set(args.backends)]
    clients = results.clients_in(wanted)
    # One column more only when it carries information. A ledger measured on
    # one machine prints exactly what it printed before this split existed.
    split = len(clients) > 1
    names = gen_tables.client_names(wanted)
    head = (
        "| backend | client | pass | median | worst | turns |"
        if split
        else ("| backend | pass | median | worst | turns |")
    )
    print(head)
    print("|---|---|---|---|---|---|" if split else "|---|---|---|---|---|")
    for name in args.backends:
        for client in clients if split else [None]:
            r = row(rows, name, client)
            if not r["trials"]:
                continue
            med = f"{r['median_s']} s" if r["median_s"] is not None else "—"
            worst = f"{r['worst_s']} s" if r["worst_s"] is not None else "—"
            turns = r["turns"] if r["turns"] is not None else "—"
            cell = f" {names.get(client, 'local')} |" if split else ""
            print(
                f"| {name} |{cell} {r['passed']}/{r['trials']} | "
                f"{med} | {worst} | {turns} |"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
