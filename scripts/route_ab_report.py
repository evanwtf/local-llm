"""Attribute #149 route-A/B rows to sweep windows and evaluate the screens.

The two arms share one backend name, so nothing inside a row says which
route served it. Attribution comes from sweep-order.txt, which
route_agent_ab.sh writes after each sweep completes; the per-sweep route
evidence comes from the server logs the same script captured at startup.

A row that fits no completed window is a hole -- from an aborted or voided
sweep, or from a run whose windows were never written. It is counted, then
never pooled: screens are evaluated only on complete windows, and any
short or missing window voids the report. The screens themselves are the
ones pre-registered on #149; this script reports which fired and stops.
It does not choose between the issue's option (a) and option (b).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import re
import sys

logger = logging.getLogger(__name__)

EXPECTED_ROWS_PER_SWEEP = 15
TENSOR_LINE = "Metal 4 tensor API enabled for Tensor kernels"
WITHHOLD_LINE = "available but not enabled (numerics)"

Window = tuple[str, str, dt.datetime, dt.datetime]


def run_started(run_dir: pathlib.Path) -> dt.datetime | None:
    """The run-record started line anchors the row cut and window dates."""
    record = run_dir / "run-record.txt"
    if not record.exists():
        return None
    m = re.search(r"started (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", record.read_text())
    if m is None:
        return None
    return dt.datetime.fromisoformat(m.group(1))


def read_windows(
    run_dir: pathlib.Path, t_prefix: str, r_prefix: str, anchor: dt.datetime
) -> list[Window]:
    """(tag, arm, start, end) for every completed sweep.

    sweep-order.txt carries times of day only, as #138's did. A run past
    midnight shifts the date: a window starting earlier than the previous
    one starts on the next day, and so does an end earlier than its own
    start.
    """
    wins: list[Window] = []
    prev_start: dt.datetime | None = None
    order = run_dir / "sweep-order.txt"
    if not order.exists():
        return []
    for line in order.read_text().splitlines():
        m = re.match(
            rf"((?:{re.escape(t_prefix)}|{re.escape(r_prefix)})\S+)\s+"
            r"(\d\d:\d\d:\d\d)\s+(\d\d:\d\d:\d\d)$",
            line,
        )
        if not m:
            continue
        tag, s, e = m.group(1), m.group(2), m.group(3)
        arm = "t" if tag.startswith(t_prefix) else "r"
        start = dt.datetime.combine(anchor.date(), dt.time.fromisoformat(s))
        if prev_start is not None and start < prev_start:
            start += dt.timedelta(days=1)
        end = dt.datetime.combine(start.date(), dt.time.fromisoformat(e))
        if end < start:
            end += dt.timedelta(days=1)
        wins.append((tag, arm, start, end))
        prev_start = start
    return wins


def load_rows(
    ledger: pathlib.Path, backend: str, cut: dt.datetime
) -> tuple[list[dict], int]:
    """This run's usable rows for one backend, and how many were dropped.

    Excluded and dry rows are holes in n, not passes or fails; they are
    counted and dropped before attribution so the hole shows up as a short
    window instead.
    """
    rows: list[dict] = []
    dropped = 0
    for line in ledger.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("backend") != backend:
            continue
        raw = row.get("started")
        if not isinstance(raw, str):
            continue
        started = dt.datetime.fromisoformat(raw)
        if started < cut:
            continue
        if row.get("excluded") or row.get("dry_run"):
            dropped += 1
            continue
        rows.append(row)
    return rows, dropped


def assign(
    rows: list[dict], wins: list[Window]
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Rows per window tag, plus rows that fit no completed window."""
    per: dict[str, list[dict]] = {tag: [] for tag, _, _, _ in wins}
    leftover: list[dict] = []
    for row in rows:
        started = dt.datetime.fromisoformat(row["started"])
        for tag, _, start, end in wins:
            if start <= started <= end:
                per[tag].append(row)
                break
        else:
            leftover.append(row)
    return per, leftover


def route_evidence(run_dir: pathlib.Path, tag: str, arm: str) -> bool | None:
    """Whether the server log for a window shows the arm's route.

    None when the log is missing: an unknown is not a pass.
    """
    log = run_dir / f"server-{tag}.log"
    if not log.exists():
        return None
    text = log.read_text(errors="replace")
    enabled = TENSOR_LINE in text
    withheld = WITHHOLD_LINE in text
    if arm == "t":
        return enabled
    return withheld and not enabled


def _passed(row: dict) -> bool:
    return row.get("passed") is True


def _index(tag: str, prefix: str) -> int:
    return int(tag[len(prefix) :])


def evaluate(
    per: dict[str, list[dict]],
    wins: list[Window],
    leftover: list[dict],
    dropped: int,
    run_dir: pathlib.Path,
    t_prefix: str = "t-sweep",
    r_prefix: str = "r-sweep",
) -> list[str]:
    """The per-window table, the screens, and nothing else.

    Returns the report lines. Any short window, missing window, or
    unattributed row voids the screens: the report prints VOID and no
    screen verdicts rather than pool partial data.
    """
    lines: list[str] = []

    lines.append(f"dropped (excluded/dry) rows: {dropped}")
    if leftover:
        lines.append(
            f"VOID: {len(leftover)} row(s) fit no completed window; "
            "rows are listed, never pooled"
        )
        for row in leftover[:5]:
            lines.append(f"  leftover: {row.get('task')} started {row.get('started')}")

    void: list[str] = []
    for tag, arm, start, end in wins:
        n = len(per[tag])
        passes = sum(1 for r in per[tag] if _passed(r))
        wall = sum(float(r["wall_seconds"]) for r in per[tag])
        evidence = route_evidence(run_dir, tag, arm)
        note = "" if evidence else "  ROUTE EVIDENCE MISSING OR WRONG"
        lines.append(
            f"{tag} [{start:%H:%M:%S}-{end:%H:%M:%S}] arm={arm} n={n} "
            f"passes={passes} wall={wall:.0f}s{note}"
        )
        if n != EXPECTED_ROWS_PER_SWEEP:
            void.append(f"{tag}: {n} rows")

    if void or leftover:
        lines.append("VOID: " + "; ".join(void or ["unattributed rows"]))
        lines.append("screens are not evaluated on partial data")
        return lines

    # Paired per-sweep index: t-sweepN against r-sweepN, same position, so
    # the KV-warmth schedule and the machine's drift apply to both.
    t_by_idx = {_index(tag, t_prefix): tag for tag, arm, _, _ in wins if arm == "t"}
    r_by_idx = {_index(tag, r_prefix): tag for tag, arm, _, _ in wins if arm == "r"}
    shared = sorted(set(t_by_idx) & set(r_by_idx))
    ratios: list[float] = []
    lines.append("paired sweep walls (t/r):")
    for idx in shared:
        t_wall = sum(float(r["wall_seconds"]) for r in per[t_by_idx[idx]])
        r_wall = sum(float(r["wall_seconds"]) for r in per[r_by_idx[idx]])
        ratio = t_wall / r_wall if r_wall else float("inf")
        ratios.append(ratio)
        lines.append(f"  sweep{idx}: t={t_wall:.0f}s r={r_wall:.0f}s ratio={ratio:.3f}")
    pooled = sum(ratios) / len(ratios) if ratios else float("nan")
    lines.append(f"pooled mean ratio: {pooled:.3f}")

    t_pass = sum(sum(1 for r in per[tag] if _passed(r)) for tag in t_by_idx.values())
    r_pass = sum(sum(1 for r in per[tag] if _passed(r)) for tag in r_by_idx.values())
    lines.append(
        f"aggregate passes: t={t_pass} r={r_pass} (of {15 * len(t_by_idx)} each)"
    )

    # Per-task grid and screen 1. One row per task per window holds after
    # the VOID checks (15 rows, 15 tasks, trials=1); an X marks a task a
    # window somehow lacks, which a later sweep rerun would have to explain.
    tasks = sorted({r["task"] for tag in per for r in per[tag]})
    flips_t: list[str] = []
    flips_r: list[str] = []
    for task in tasks:

        def outcome(tag: str, task: str = task) -> str:
            rows = [r for r in per[tag] if r["task"] == task]
            if len(rows) != 1:
                return "X"
            return "P" if _passed(rows[0]) else "F"

        t_out = [outcome(t_by_idx[idx]) for idx in shared]
        r_out = [outcome(r_by_idx[idx]) for idx in shared]
        lines.append(f"  {task}: t={''.join(t_out)} r={''.join(r_out)}")
        if "X" not in t_out and "X" not in r_out:
            if t_out.count("F") >= 2 and r_out.count("P") == len(shared):
                flips_t.append(task)
            if r_out.count("F") >= 2 and t_out.count("P") == len(shared):
                flips_r.append(task)

    screen1 = bool(flips_t)
    screen2 = t_pass <= r_pass - 6
    slower_all = bool(ratios) and all(r > 1.0 for r in ratios)
    screen3 = slower_all and pooled >= 1.05
    screen4 = (
        bool(ratios)
        and all(r < 1.0 for r in ratios)
        and pooled <= 0.95
        and not screen1
        and t_pass >= r_pass
    )

    if flips_t:
        lines.append(f"screen 1 FIRES: t breaks {', '.join(flips_t)}")
    else:
        lines.append("screen 1: no task fails >=2 t sweeps while passing all r sweeps")
    if flips_r:
        lines.append(
            f"route fixes {', '.join(flips_r)} (r breaks, t passes -- recorded, not a screen)"
        )
    lines.append(
        f"screen 2: {'FIRES' if screen2 else 'does not fire'}"
        f" (threshold: t <= r-6; delta is {t_pass - r_pass})"
    )
    lines.append(
        f"screen 3: {'FIRES' if screen3 else 'does not fire'}"
        f" (needs all pairs > 1.0 and pooled >= 1.05)"
    )
    lines.append(
        f"screen 4: {'FIRES' if screen4 else 'does not fire'}"
        f" (needs all pairs < 1.0, pooled <= 0.95, no screen-1 task, t >= r)"
    )

    lines.append(
        "Report stops here: the numbers above are the deliverable. The"
        " regime choice -- #149 option (a) or option (b) -- is made on"
        " them by the issue, not by this script."
    )
    return lines


def main(argv: list[str] | None = None) -> int:
    repo = pathlib.Path(__file__).resolve().parent.parent
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout, format="%(message)s", force=True
    )
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--ledger",
        type=pathlib.Path,
        default=repo
        / "hardware"
        / "MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A"
        / "results.jsonl",
    )
    p.add_argument(
        "--run-dir",
        type=pathlib.Path,
        default=pathlib.Path.home() / "bench-logs" / "149-route-ab",
    )
    p.add_argument("--backend", default="qwen38fnds4shim")
    p.add_argument("--t-prefix", default="t-sweep")
    p.add_argument("--r-prefix", default="r-sweep")
    args = p.parse_args(argv)

    started = run_started(args.run_dir)
    if started is None:
        logger.info(
            "VOID: %s has no parseable started line", args.run_dir / "run-record.txt"
        )
        return 2
    if not args.ledger.exists():
        logger.info("VOID: ledger %s does not exist", args.ledger)
        return 2
    logger.info("cut: %s (run-record.txt)", started.isoformat(sep=" "))

    rows, dropped = load_rows(args.ledger, args.backend, started)
    logger.info("rows: %d usable for %s (dropped %d)", len(rows), args.backend, dropped)

    wins = read_windows(args.run_dir, args.t_prefix, args.r_prefix, started)
    if not wins:
        logger.info(
            "VOID: no completed sweep windows in %s", args.run_dir / "sweep-order.txt"
        )
        return 2
    per, leftover = assign(rows, wins)
    for line in evaluate(
        per, wins, leftover, dropped, args.run_dir, args.t_prefix, args.r_prefix
    ):
        logger.info("%s", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
