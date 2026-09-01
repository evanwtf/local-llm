"""Move every pre---dir OpenCode row out of results.jsonl into the archive.

Those trials ran a client that was never told which directory to work in
(#67). `opencode run` attaches to a persistent server holding its own cwd, so
the client solved the task and wrote the answer into the launcher's directory.
Every one of them measures this harness, not OpenCode.

They are archived rather than deleted: they are still real records of what the
harness did, and the before/after comparison in `dirfix.py` reads them.

Lines are moved BYTE-IDENTICAL. This never edits a recorded measurement -- a
previous attempt to recompute a stored field corrupted 30 rows, and the rule
since is to annotate or relocate, never to rewrite.

Idempotent: running it twice moves nothing the second time.
"""

from __future__ import annotations

import json
import logging
import pathlib
import subprocess
import sys

logger = logging.getLogger(__name__)

FIX = "7356460"  # opencode_argv gained --dir
RESULTS = pathlib.Path("benchmarks/agent/results.jsonl")
ARCHIVE = pathlib.Path("docs/archive/results-opencode-pre-dir.jsonl")


def fixed_commits(repo: pathlib.Path) -> set[str]:
    r = subprocess.run(
        ["git", "log", "--format=%h", f"{FIX}~1..HEAD"],
        capture_output=True,
        text=True,
        cwd=repo,
        check=False,
    )
    heads = {c[:7] for c in r.stdout.split()}
    if not heads:
        raise SystemExit(f"no commits from {FIX} in {repo}: {r.stderr.strip()}")
    return heads


def is_pre_dir(line: str, after: set[str]) -> bool:
    """A row this fix invalidates: an OpenCode trial from before `--dir`.

    A row with no `harness_head` predates the provenance field entirely, which
    dates it before the fix.
    """
    row = json.loads(line)
    if row.get("client") != "opencode":
        return False
    return str(row.get("env", {}).get("harness_head", ""))[:7] not in after


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    root = pathlib.Path(__file__).resolve().parent.parent
    after = fixed_commits(root)

    lines = RESULTS.read_text().splitlines(keepends=True)
    move = [x for x in lines if is_pre_dir(x, after)]
    keep = [x for x in lines if not is_pre_dir(x, after)]

    if not move:
        logger.info("nothing to archive; results.jsonl holds %d rows", len(keep))
        return

    # Every line must land in exactly one file, unchanged.
    assert len(move) + len(keep) == len(lines), "row lost or duplicated"

    existing = ARCHIVE.read_text() if ARCHIVE.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    ARCHIVE.write_text(existing + "".join(move))
    RESULTS.write_text("".join(keep))

    logger.info("archived %d rows -> %s", len(move), ARCHIVE)
    logger.info("results.jsonl now holds %d rows", len(keep))


if __name__ == "__main__":
    main()
