"""How much does running second inside a rep cost? (#130)

`decode_ab_engine.sh` alternates arm order between repetitions -- odd reps run
A then B, even reps run B then A -- so that drift within a run divides across
both arms instead of always landing on the one that goes second. That rule has
always been justified by argument. This measures it.

The quantity is the **position effect**: within one rep, the ratio of the arm
that ran first to the arm that ran second, at each frontier. If position costs
nothing the ratio is 1.0 and the alternation is harmless bookkeeping. If it is
larger than the effect under test, a fixed-order run reports position and calls
it engine.

Order comes from `run-order.txt` when the harness wrote one -- `decode_ab.sh`
records `rep=N position=P of 2 label=L` for exactly this purpose (#130 item 3,
"so a later reader can test for positional bias instead of assuming it away").
`decode_ab_engine.sh` does not write it, so mtime ordering is the fallback.
Recorded truth beats inference where it exists; inference beats nothing where
it does not. Either way the run directory stays self-describing, which the
driver log in ~/bench-logs is not -- it is not shipped with the evidence.

    uv run python scripts/arm_order_effect.py benchmarks/ds4/prefill-ab-171 ...
"""

from __future__ import annotations

import argparse
import csv
import logging
import pathlib
import re
import statistics
import sys

logger = logging.getLogger(__name__)

#: `head-20d5dff6-rep3.csv` -> ("head-20d5dff6", 3)
NAME = re.compile(r"^(?P<arm>.+)-rep(?P<rep>\d+)\.csv$")


def parse_name(path: pathlib.Path) -> tuple[str, int] | None:
    m = NAME.match(path.name)
    return (m.group("arm"), int(m.group("rep"))) if m else None


def read_rates(path: pathlib.Path, column: str) -> dict[int, float]:
    """Frontier -> rate. A row missing the column is skipped, not guessed."""
    out: dict[int, float] = {}
    with path.open() as fh:
        for row in csv.DictReader(fh):
            value = row.get(column)
            if not value:
                continue
            out[int(row["ctx_tokens"])] = float(value)
    return out


#: `rep=2 position=1 of 2 label=head-20d5dff6`
ORDER_LINE = re.compile(
    r"^rep=(?P<rep>\d+)\s+position=(?P<position>\d+)\s+of\s+\d+\s+label=(?P<arm>.+)$"
)


def recorded_order(outdir: pathlib.Path) -> dict[tuple[int, str], int]:
    """(rep, arm) -> position, from run-order.txt. Empty if absent."""
    path = outdir / "run-order.txt"
    if not path.exists():
        return {}
    out: dict[tuple[int, str], int] = {}
    for line in path.read_text().splitlines():
        m = ORDER_LINE.match(line.strip())
        if m:
            out[(int(m.group("rep")), m.group("arm").strip())] = int(
                m.group("position")
            )
    return out


def reps_in(outdir: pathlib.Path) -> dict[int, list[tuple[float, str, pathlib.Path]]]:
    """rep -> [(sort key, arm, path)] in run order, first arm first.

    The sort key is the recorded position when the harness wrote one, and the
    file mtime otherwise. Mixing them within a rep would be meaningless, so a
    rep uses recorded positions only when every one of its arms has one.
    """
    recorded = recorded_order(outdir)
    found: dict[int, list[tuple[float, str, pathlib.Path]]] = {}
    for path in sorted(outdir.glob("*.csv")):
        parsed = parse_name(path)
        if parsed is None:
            continue
        arm, rep = parsed
        found.setdefault(rep, []).append((path.stat().st_mtime, arm, path))
    for rep, entries in found.items():
        positions = [recorded.get((rep, arm)) for _, arm, _ in entries]
        if all(p is not None for p in positions):
            entries[:] = [
                (float(pos), arm, path)
                for pos, (_, arm, path) in zip(positions, entries, strict=True)
            ]
        entries.sort()
    return found


def position_ratios(outdir: pathlib.Path, column: str) -> list[tuple[int, str, float]]:
    """(rep, first-arm, median first/second ratio) for every complete rep.

    Above 1.0 means the arm that ran first was faster -- a penalty for going
    second. A rep with one arm is dropped: a pair is the unit, and half a pair
    is not a slower half.
    """
    out: list[tuple[int, str, float]] = []
    for rep, entries in sorted(reps_in(outdir).items()):
        if len(entries) != 2:
            logger.warning(
                "%s rep %d has %d arm(s), not 2; skipped",
                outdir.name,
                rep,
                len(entries),
            )
            continue
        (_, first_arm, first_path), (_, _, second_path) = entries
        first, second = read_rates(first_path, column), read_rates(second_path, column)
        shared = sorted(set(first) & set(second))
        if not shared:
            continue
        ratios = [first[k] / second[k] for k in shared if second[k]]
        if ratios:
            out.append((rep, first_arm, statistics.median(ratios)))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dirs", nargs="+")
    p.add_argument("--column", default="prefill_tps")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

    every: list[float] = []
    for name in args.dirs:
        outdir = pathlib.Path(name)
        rows = position_ratios(outdir, args.column)
        if not rows:
            logger.info("%s: no complete reps", outdir.name)
            continue
        logger.info("=== %s (%s) ===", outdir.name, args.column)
        for rep, first_arm, ratio in rows:
            logger.info(
                "  rep%-2d first=%-16s first/second = %.3f (%+.1f%%)",
                rep,
                first_arm,
                ratio,
                100 * (ratio - 1),
            )
        every.extend(r for _, _, r in rows)

    if not every:
        logger.error("nothing to summarise")
        return 1
    faster = sum(1 for r in every if r > 1)
    logger.info("")
    logger.info("reps where the FIRST arm was faster: %d of %d", faster, len(every))
    logger.info(
        "median position effect: %.3f (%+.1f%%)",
        statistics.median(every),
        100 * (statistics.median(every) - 1),
    )
    logger.info(
        "  Above zero means going second costs something. Compare it to the "
        "effect under test before reading a fixed-order run as a result."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
