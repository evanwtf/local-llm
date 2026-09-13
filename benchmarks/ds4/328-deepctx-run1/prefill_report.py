"""One-off: paired prefill ratio for #328 deep-context A/B, pooled over runs.

level2 (MM_NAX unset) vs comp (=5), same build/pack/PLE. Pairs WITHIN each rep
(level2/comp at the same frontier and rep), then medians the per-rep ratios --
the same paired statistic decode_ab_report uses, which cannot run here because
gen_steady_tps is 0 for a pure-prefill sweep (that gap is filed separately).

Usage: 328_prefill_report.py <run-dir> [<run-dir> ...]
Each dir has {level2,comp}-rep{1,2,3}.csv. Ratios pool across all runs/reps.
"""

from __future__ import annotations

import csv
import pathlib
import statistics as st
import sys

DIRS = [pathlib.Path(a) for a in sys.argv[1:]]
REPS = (1, 2, 3)


def load(d: pathlib.Path, label: str) -> dict[int, dict[int, float]]:
    out: dict[int, dict[int, float]] = {}
    for rep in REPS:
        with (d / f"{label}-rep{rep}.csv").open() as fh:
            for row in csv.DictReader(fh):
                out.setdefault(int(row["ctx_tokens"]), {})[rep] = float(
                    row["prefill_tps"]
                )
    return out


# per frontier -> list of (level2/comp) ratios across every run and rep
pooled: dict[int, list[float]] = {}
# per run, per frontier -> median ratio (for between-run spread)
per_run: list[dict[int, float]] = []
lvl_abs: dict[int, list[float]] = {}
cmp_abs: dict[int, list[float]] = {}

for d in DIRS:
    lvl, cmp = load(d, "level2"), load(d, "comp")
    run_fr: dict[int, float] = {}
    for ctx in sorted(set(lvl) & set(cmp)):
        rs = [lvl[ctx][r] / cmp[ctx][r] for r in REPS if cmp[ctx][r]]
        pooled.setdefault(ctx, []).extend(rs)
        run_fr[ctx] = st.median(rs)
        lvl_abs.setdefault(ctx, []).extend(lvl[ctx][r] for r in REPS)
        cmp_abs.setdefault(ctx, []).extend(cmp[ctx][r] for r in REPS)
    per_run.append(run_fr)

frontiers = sorted(pooled)
print(
    f"pooled over {len(DIRS)} runs x {len(REPS)} reps = {len(DIRS) * len(REPS)} pairs/frontier"
)
print(f"{'ctx':>8} {'level2 t/s':>11} {'comp t/s':>10} {'lvl2/comp':>10} {'gain':>8}")
frontier_med: dict[int, float] = {}
for ctx in frontiers:
    med = st.median(pooled[ctx])
    frontier_med[ctx] = med
    if ctx % 8192 == 0 or ctx >= 114688:
        print(
            f"{ctx:>8} {st.median(lvl_abs[ctx]):>11.1f} {st.median(cmp_abs[ctx]):>10.1f} "
            f"{med:>10.3f} {med - 1:>+8.1%}"
        )


def band(lo: int, hi: int) -> float:
    vals = [r for c, r in frontier_med.items() if lo <= c <= hi]
    return st.median(vals)


print()
print(
    f"shallow (<=14336)  median lvl2/comp: {band(0, 14336):.3f} ({band(0, 14336) - 1:+.1%})"
)
print(
    f"mid (16384-65536)  median lvl2/comp: {band(16384, 65536):.3f} ({band(16384, 65536) - 1:+.1%})"
)
print(
    f"deep (>=98304)     median lvl2/comp: {band(98304, 10**9):.3f} ({band(98304, 10**9) - 1:+.1%})"
)
allm = list(frontier_med.values())
print(
    f"all frontiers      median lvl2/comp: {st.median(allm):.3f} ({st.median(allm) - 1:+.1%})"
)
print(f"frontiers where comp > level2: {sum(1 for r in allm if r < 1)} of {len(allm)}")

# between-run agreement at the deep tail
print("\ndeep-tail (>=98304) per-run median ratio:")
for i, rf in enumerate(per_run, 1):
    deep = [r for c, r in rf.items() if c >= 98304]
    print(f"  run{i}: {st.median(deep):.3f} ({st.median(deep) - 1:+.1%})")
