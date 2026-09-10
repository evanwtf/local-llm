"""Run ds4#990's model-free Qwen3.8-Flash-Next Metal suites and report (#170).

ds4#990 ships a native Metal port of Qwen3.8-Flash-Next and six test targets
that need Metal but **no model file**. That makes them the cheapest real answer
anyone can give that PR: the author validated on an M5 Pro, and whether the
kernels also pass on an M5 Max costs a build rather than a benchmark.

The PR moves daily -- it gained `ple-store` and then `indexer` after #170 was
written -- so the suite list is read from the tree's own Makefile rather than
hard-coded here. A target that appears upstream and is never run is the failure
this avoids.

**Exit 0 is not a pass.** These binaries initialise Metal before they assert,
and a device that refuses to come up prints its complaint and can still leave a
zero status behind. A suite counts as passed only when its own success line is
present, and the two spellings below are both real: five suites end with
`... tests: PASS` and `test_qwen38_ple_store` ends with `: ok`.

    uv run python scripts/qwen38_metal_suites.py --tree ~/git/ds4-pr990
    uv run python scripts/qwen38_metal_suites.py --tree ~/git/ds4-pr990 --no-build
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import subprocess
import sys
from dataclasses import asdict, dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

#: `test-qwen38-gdn: tests/test_qwen38_gdn` in the tree's Makefile. Reading the
#: list from the tree keeps a newly added upstream suite from being skipped.
TARGET = re.compile(r"^(test-qwen38-[a-z0-9-]+):", re.MULTILINE)

#: The two success spellings these binaries use. Anchored on the whole phrase:
#: the word PASS alone also appears in progress chatter.
PASS_LINES = (
    re.compile(r"^Qwen3\.8 .*tests: PASS$", re.MULTILINE),
    re.compile(r"^test_qwen38_[a-z_]+: ok$", re.MULTILINE),
)


@dataclass(frozen=True)
class Suite:
    """One suite's outcome. `passed` requires both a status and a pass line."""

    target: str
    returncode: int
    pass_line: str | None
    passed: bool


def suite_targets(makefile: str) -> list[str]:
    """Every `test-qwen38-*` target the tree declares, in Makefile order."""
    seen: list[str] = []
    for name in TARGET.findall(makefile):
        if name not in seen:
            seen.append(name)
    return seen


def parse_result(target: str, output: str, returncode: int) -> Suite:
    """Judge one suite. A missing pass line fails it even on a zero status."""
    line = None
    for pattern in PASS_LINES:
        found = pattern.search(output)
        if found is not None:
            line = found.group(0)
            break
    return Suite(
        target=target,
        returncode=returncode,
        pass_line=line,
        passed=returncode == 0 and line is not None,
    )


def binary_for(target: str) -> str:
    """`test-qwen38-ple-hash` -> `tests/test_qwen38_ple_hash`."""
    # The dashes become underscores and nothing is stripped: the Makefile
    # target `test-qwen38-gdn` builds `tests/test_qwen38_gdn`, which keeps its
    # own `test_` prefix inside the `tests/` directory.
    return f"tests/{target.replace('-', '_')}"


def run_suite(tree: pathlib.Path, target: str) -> Suite:
    done = subprocess.run(
        [f"./{binary_for(target)}"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    return parse_result(target, done.stdout + done.stderr, done.returncode)


def build(tree: pathlib.Path, targets: list[str], jobs: int) -> str:
    """Build the binaries only. Running is a separate step so a build warning
    is reported even when every suite then passes."""
    done = subprocess.run(
        ["make", f"-j{jobs}", *[binary_for(t) for t in targets]],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        logger.error("build failed:\n%s", done.stdout + done.stderr)
        raise SystemExit(1)
    return done.stdout + done.stderr


def warnings_in(build_log: str) -> list[str]:
    return [ln for ln in build_log.splitlines() if ": warning:" in ln]


def head_sha(tree: pathlib.Path) -> str:
    done = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tree,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.strip() or "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=pathlib.Path, required=True)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--json", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    logs.configure()

    tree: pathlib.Path = args.tree.expanduser()
    makefile = (tree / "Makefile").read_text()
    targets = suite_targets(makefile)
    sha = head_sha(tree)
    logger.info("tree %s at %s: %d suites", tree, sha[:8], len(targets))

    build_log = "" if args.no_build else build(tree, targets, args.jobs)
    for line in warnings_in(build_log):
        logger.warning("build: %s", line)

    results = [run_suite(tree, t) for t in targets]
    for r in results:
        logger.info(
            "%-24s %s exit=%d %s",
            r.target,
            "PASS" if r.passed else "FAIL",
            r.returncode,
            r.pass_line or "(no pass line)",
        )

    failed = [r.target for r in results if not r.passed]
    logger.info("%d of %d passed", len(results) - len(failed), len(results))
    if failed:
        logger.error("failed: %s", ", ".join(failed))

    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {
                    "tree": str(tree),
                    "head": sha,
                    "suites": [asdict(r) for r in results],
                    "build_warnings": warnings_in(build_log),
                },
                indent=2,
            )
            + "\n"
        )
        logger.info("wrote %s", args.json)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
