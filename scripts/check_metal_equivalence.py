"""Run ds4's Metal tensor-route equivalence test and cache the verdict (#149).

    uv run python scripts/check_metal_equivalence.py            # cached, or run it
    uv run python scripts/check_metal_equivalence.py --force    # run it regardless

The test maps 93 GiB and takes minutes, so its answer is cached against a
fingerprint of the `ds4_test` binary and the model. Rebuild either and the
cached answer reads `stale` and this runs again -- a rebuilt engine must not
inherit yesterday's verdict.

Exit status is the gate: 0 when the fast route agrees with the reference
kernels on every emitted token, 1 when it does not, 2 when the answer could not
be established. **A pass is not a claim of bit-exactness** -- it reports the
logit drift it measured, and on 2026-09-06 that drift was real (worst_max_abs
5.33 on the long code-audit fixture) while no greedy token moved.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent"))

import metal_equivalence as me  # noqa: E402
import provenance  # noqa: E402

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tree",
        type=pathlib.Path,
        default=pathlib.Path.home() / "git" / "ds4-metal",
        help="ds4 checkout holding ds4_test",
    )
    parser.add_argument(
        "--model",
        type=pathlib.Path,
        default=pathlib.Path.home() / "git" / "ds4" / "ds4flash.gguf",
        help="model the fixtures run against",
    )
    parser.add_argument("--force", action="store_true", help="ignore any cached verdict")
    args = parser.parse_args(argv)
    provenance.configure()

    binary = args.tree / "ds4_test"
    fp = me.fingerprint(binary, args.model)
    if fp is None:
        logger.error("no ds4_test at %s, or no model at %s", binary, args.model)
        return 2

    if not args.force:
        cached = me.cached_verdict(me.DEFAULT_CACHE, fp)
        if cached in ("pass", "fail"):
            entry = me.cached_entry(me.DEFAULT_CACHE, fp) or {}
            logger.info(
                "cached verdict %s for %s from %s: %s",
                cached.upper(),
                entry.get("label") or args.model.name,
                entry.get("checked_at", "?"),
                entry.get("summary", {}),
            )
            return 0 if cached == "pass" else 1
        logger.info("no verdict for this build (%s) -- running the test", cached)

    logger.info("running ds4_test --metal-tensor-equivalence (maps ~93 GiB, minutes)")
    verdict, summary, text = me.run_test(args.tree, args.model)
    if verdict == "unknown":
        logger.error("the test produced no summary line; not recording a verdict")
        for line in text.splitlines()[-5:]:
            logger.error("  %s", line)
        return 2

    me.write_verdict(
        me.DEFAULT_CACHE,
        fingerprint=fp,
        verdict=verdict,
        summary=summary,
        label=args.model.name,
    )
    logger.info("verdict %s, recorded in %s", verdict.upper(), me.DEFAULT_CACHE)
    logger.info("summary: %s", summary)
    if verdict == "pass":
        logger.info(
            "greedy agreement on %s cases. NOT bit-exact: worst rms %s, "
            "worst max_abs %s, min top5 overlap %s",
            summary.get("cases"),
            summary.get("worst_rms"),
            summary.get("worst_max_abs"),
            summary.get("min_top5_overlap"),
        )
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
