"""Post one completed decode-A/B run to a GitHub issue, once.

A four-run recheck is only useful if each run is on the record as it lands.
Doing that by hand means composing the same comment four times and
transcribing numbers out of a terminal -- which is how a figure gets written
from memory. This runs `decode_ab_report.py` and posts its output verbatim.

Idempotent by design: the comment carries a marker naming the run directory,
and an existing comment with that marker means the run has been posted and
this is a no-op. A repeat-poster that duplicates on every invocation is worse
than no automation, because the noise lands on the issue people read.

    uv run python scripts/post_ab_run.py 91 benchmarks/ds4/pr621-recheck-run1
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import subprocess
import sys

logger = logging.getLogger(__name__)

MARKER = "<!-- ab-run-report: {name} -->"


def _gh(args: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False, **kw
    )


def already_posted(issue: int, name: str, repo: str | None = None) -> bool:
    """Whether this run's marker is already on the issue."""
    args = ["issue", "view", str(issue), "--json", "comments"]
    if repo:
        args += ["--repo", repo]
    got = _gh(args)
    if got.returncode != 0:
        # Cannot tell. Refuse rather than risk a duplicate: a missing comment
        # is recoverable by rerunning, a duplicate is not.
        raise RuntimeError(f"could not read issue {issue}: {got.stderr.strip()}")
    bodies = [c.get("body", "") for c in json.loads(got.stdout).get("comments", [])]
    return any(MARKER.format(name=name) in b for b in bodies)


def is_complete(run: pathlib.Path) -> bool:
    """Every CSV holds the widest frontier count present.

    Counting files reports a run finished while ds4-bench is still filling
    its last one, and a report taken then includes a partial arm.
    """
    csvs = sorted(run.glob("*.csv"))
    if not csvs:
        return False
    counts = [sum(1 for _ in p.open()) - 1 for p in csvs]
    return len(set(counts)) == 1 and counts[0] > 0


def report(run: pathlib.Path) -> str:
    """`decode_ab_report.py` output for one run, verbatim."""
    got = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).with_name("decode_ab_report.py")),
            str(run),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if got.returncode != 0:
        raise RuntimeError(f"report failed for {run}: {got.stderr.strip()}")
    return got.stdout.strip()


def body(run: pathlib.Path) -> str:
    state = run / "start-state.txt"
    lines = [
        MARKER.format(name=run.name),
        f"## `{run.name}`",
        "",
        (
            "Posted automatically as the run completed. Numbers are "
            "`scripts/decode_ab_report.py` output, not transcribed."
        ),
        "",
        "```",
        report(run),
        "```",
    ]
    if state.exists():
        lines += [
            "",
            "<details><summary>machine state</summary>",
            "",
            "```",
            state.read_text().strip(),
            "```",
            "",
            "</details>",
        ]
    lines += [
        "",
        (
            "**One run is one datapoint.** No comparison against any prior "
            "figure until at least three runs of this A/B exist."
        ),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("issue", type=int)
    p.add_argument("run", type=pathlib.Path)
    p.add_argument("--repo", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    if not is_complete(args.run):
        logger.info("%s is not complete yet; nothing posted", args.run.name)
        return 0
    if already_posted(args.issue, args.run.name, args.repo):
        logger.info("%s already posted to #%d", args.run.name, args.issue)
        return 0

    text = body(args.run)
    if args.dry_run:
        logger.info("%s", text)
        return 0

    tmp = pathlib.Path("/tmp") / f"ab-run-{args.run.name}.md"
    tmp.write_text(text)
    cmd = ["issue", "comment", str(args.issue), "--body-file", str(tmp)]
    if args.repo:
        cmd += ["--repo", args.repo]
    got = _gh(cmd)
    if got.returncode != 0:
        logger.error("posting failed: %s", got.stderr.strip())
        return 1
    logger.info("posted %s to #%d: %s", args.run.name, args.issue, got.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
