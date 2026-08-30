"""Summarise a paired decode A/B produced by scripts/decode_ab.sh (#48).

Reports the per-frontier paired ratio, not just two medians: the frontiers
differ from each other by more than the effect we are chasing, so pooling them
would hide it. The paired median ratio is the statistic that answers "did
decode get faster", and the per-frontier spread says whether it held
everywhere or came from one point.
"""

from __future__ import annotations

import csv
import logging
import pathlib
import statistics as st
import sys
from collections import defaultdict

logger = logging.getLogger(__name__)


def load(outdir: pathlib.Path) -> dict[str, dict[int, list[float]]]:
    """label -> ctx_tokens -> [gen_steady_tps, ...] across repetitions."""
    data: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for path in sorted(outdir.glob("*-rep*.csv")):
        label = path.name.rsplit("-rep", 1)[0]
        with path.open() as fh:
            for row in csv.DictReader(fh):
                # gen_steady_tps excludes first-token latency, which is the
                # part that moves with prefill rather than decode.
                data[label][int(row["ctx_tokens"])].append(float(row["gen_steady_tps"]))
    return data


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    outdir = pathlib.Path(argv[1]) if len(argv) > 1 else pathlib.Path.cwd()
    data = load(outdir)
    if len(data) != 2:
        logger.error("need exactly 2 labels, found %s", sorted(data))
        return 1

    a, b = sorted(data)
    frontiers = sorted(set(data[a]) & set(data[b]))
    logger.info("%-10s %12s %12s %8s", "ctx", a, b, "b/a")
    ratios = []
    for ctx in frontiers:
        ma, mb = st.median(data[a][ctx]), st.median(data[b][ctx])
        ratios.append(mb / ma)
        logger.info("%-10d %12.2f %12.2f %8.3f", ctx, ma, mb, mb / ma)

    logger.info("")
    # Name both directions. Labels sort alphabetically, so which arm lands in
    # the numerator is an accident of naming -- and a bare "0.872" has already
    # been misread once as the wrong way round.
    median = st.median(ratios)
    logger.info(
        "paired median %s/%s: %.3f  (%+.1f%%)", b, a, median, (median - 1) * 100
    )
    logger.info(
        "paired median %s/%s: %.3f  (%+.1f%%)", a, b, 1 / median, (1 / median - 1) * 100
    )
    logger.info(
        "range of %s/%s across frontiers: %.3f - %.3f", b, a, min(ratios), max(ratios)
    )
    if len(ratios) > 1:
        wins = sum(1 for r in ratios if r > 1)
        logger.info("frontiers where %s > %s: %d of %d", b, a, wins, len(ratios))
        logger.info(
            "frontiers where %s > %s: %d of %d", a, b, len(ratios) - wins, len(ratios)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
