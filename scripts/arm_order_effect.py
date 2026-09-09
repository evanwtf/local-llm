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

That per-rep ratio conflates position with arm identity. A rep with arm A first
measures r·p (the true A/B ratio times the position advantage); a rep with arm
B first measures p/r. When the run alternates, the geometric mean of the two
arm-first groups is p and their ratio under a square root is r, so the script
reports both. When `engines.txt` shows the two arms are the same, r is 1 by
construction and the bare position number is the whole story; when they differ,
a bare position number would conflate the two, so the script refuses it and
reports position and arm effects instead.

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
import math
import pathlib
import re
import statistics
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

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


@dataclass(frozen=True)
class ArmEffects:
    """Position effect p and arm effect r, separated from the per-rep ratios.

    A rep with arm A first measures r·p (the true A/B ratio times the position
    advantage); a rep with arm B first measures p/r. The geometric mean of the
    A-first group is r·p and of the B-first group p/r, so:

        p = sqrt(GM_A_first * GM_B_first)
        r = sqrt(GM_A_first / GM_B_first)

    `groups` is (arm, group geometric mean, reps) in A/B order, so a reader
    can audit which reps produced each effect.
    """

    position: float
    arm: float
    groups: tuple[tuple[str, float, tuple[int, ...]], ...]


def arm_effects(
    rows: list[tuple[int, str, float]], a_label: str, b_label: str
) -> ArmEffects | None:
    """Separate the position effect from the arm effect.

    `rows` is the (rep, first_arm, ratio) list from `position_ratios`.
    `a_label` and `b_label` name the two arms; r is reported as a_label/b_label.
    Both arms must have run first at least once -- with one group the two
    effects are not separable, and the function returns None.
    """
    by_arm: dict[str, list[tuple[int, float]]] = {}
    for rep, arm, ratio in rows:
        by_arm.setdefault(arm, []).append((rep, ratio))
    if a_label not in by_arm or b_label not in by_arm:
        return None
    gm_a = statistics.geometric_mean(r for _, r in by_arm[a_label])
    gm_b = statistics.geometric_mean(r for _, r in by_arm[b_label])
    groups = (
        (a_label, gm_a, tuple(rep for rep, _ in by_arm[a_label])),
        (b_label, gm_b, tuple(rep for rep, _ in by_arm[b_label])),
    )
    return ArmEffects(
        position=math.sqrt(gm_a * gm_b),
        arm=math.sqrt(gm_a / gm_b),
        groups=groups,
    )


#: `A label=head-20d5dff6 tree=/x @ abc` -> ("A", "head-20d5dff6")
ARM_LINE = re.compile(r"^(?P<side>[AB]) label=(?P<label>\S+)")


def arm_labels(outdir: pathlib.Path) -> tuple[str, str] | None:
    """(A label, B label) from engines.txt, or None when it cannot say."""
    path = outdir / "engines.txt"
    if not path.exists():
        return None
    labels: dict[str, str] = {}
    for line in path.read_text().splitlines():
        m = ARM_LINE.match(line.strip())
        if m:
            labels[m.group("side")] = m.group("label")
    if "A" not in labels or "B" not in labels:
        return None
    return labels["A"], labels["B"]


def arms_differ(outdir: pathlib.Path) -> bool | None:
    """True when engines.txt names two different arms, False when it names the
    same arm twice, None when it cannot say (absent or unparseable)."""
    labels = arm_labels(outdir)
    if labels is None:
        return None
    a, b = labels
    return a != b


def _report_bare_position(rows: list[tuple[int, str, float]]) -> None:
    """The arms are the same, so r is 1 by construction and the bare position
    number is the whole story."""
    ratios = [r for _, _, r in rows]
    faster = sum(1 for r in ratios if r > 1)
    logger.info("reps where the FIRST arm was faster: %d of %d", faster, len(ratios))
    logger.info(
        "median position effect: %.3f (%+.1f%%)",
        statistics.median(ratios),
        100 * (statistics.median(ratios) - 1),
    )
    logger.info(
        "  Above zero means going second costs something. Compare it to the "
        "effect under test before reading a fixed-order run as a result."
    )


def _report_effects(effects: ArmEffects) -> None:
    """The arms differ, so a bare position number would conflate position with
    arm. Report both, each with the reps it came from."""
    from_clause = " and ".join(
        f"{arm}-first reps {','.join(str(r) for r in reps)} (GM {gm:.3f})"
        for arm, gm, reps in effects.groups
    )
    logger.info(
        "position effect p = %.3f (%+.1f%%)  from %s",
        effects.position,
        100 * (effects.position - 1),
        from_clause,
    )
    total = sum(len(reps) for _, _, reps in effects.groups)
    a_label, b_label = effects.groups[0][0], effects.groups[1][0]
    logger.info(
        "arm effect r = %s/%s = %.3f (%+.1f%%)  from the same %d reps",
        a_label,
        b_label,
        effects.arm,
        100 * (effects.arm - 1),
        total,
    )


def _summarise(outdir: pathlib.Path, rows: list[tuple[int, str, float]]) -> None:
    """One summary per run: the bare position number when the arms are the
    same, position and arm effects when they differ."""
    differ = arms_differ(outdir)
    if differ is False:
        _report_bare_position(rows)
        return
    # Arms differ (or engines.txt cannot say). r needs the A/B assignment;
    # engines.txt names it, and the two arms seen in the rows are the fallback.
    labels = arm_labels(outdir)
    if labels is None:
        arms = sorted({arm for _, arm, _ in rows})
        if len(arms) < 2:
            logger.warning(
                "%s: arms differ but no rep alternates; cannot separate position from arm",
                outdir.name,
            )
            return
        labels = (arms[0], arms[1])
    effects = arm_effects(rows, *labels)
    if effects is not None:
        _report_effects(effects)
    else:
        logger.warning(
            "%s: arms differ but no rep alternates; cannot separate position from arm",
            outdir.name,
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("dirs", nargs="+")
    p.add_argument("--column", default="prefill_tps")
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)

    any_rows = False
    for name in args.dirs:
        outdir = pathlib.Path(name)
        rows = position_ratios(outdir, args.column)
        if not rows:
            logger.info("%s: no complete reps", outdir.name)
            continue
        any_rows = True
        logger.info("=== %s (%s) ===", outdir.name, args.column)
        for rep, first_arm, ratio in rows:
            logger.info(
                "  rep%-2d first=%-16s first/second = %.3f (%+.1f%%)",
                rep,
                first_arm,
                ratio,
                100 * (ratio - 1),
            )
        _summarise(outdir, rows)

    if not any_rows:
        logger.error("nothing to summarise")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
