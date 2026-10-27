"""Check a local checkpoint against the Hub's own hashes for one revision.

Weights move twice before a run: one download on the head, then a copy to the
worker over the fabric (CLAUDE.md, "Get weights in this order"). Both copies
must match the revision that was approved, file for file. A truncated or corrupt
shard otherwise surfaces hours later as a model failure.

The Hub's tree API gives, per file, the git blob id and, for LFS files, the
sha256 and size. This script:

  * compares each LFS file by size first (cheap), then by sha256;
  * compares each small file by its git blob id;
  * reports missing files; files on disk that the revision lacks are ignored;
  * with ``--manifest``, writes the LFS hashes in ``sha256sum`` format, so a
    node without this checkout can run ``sha256sum -c`` on its copy.

Usage::

    uv run python scripts/verify_hf_weights.py REPO REVISION DIR
    uv run python scripts/verify_hf_weights.py REPO REVISION DIR --manifest out.sha256

Exit status is 0 when every file matches, 1 otherwise.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import pathlib
import sys
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

# `hf download --local-dir` does not write these.
DEFAULT_SKIP = frozenset({".gitattributes"})
CHUNK = 16 * 1024 * 1024


@dataclasses.dataclass(frozen=True)
class Entry:
    path: str
    size: int
    sha256: str | None  # LFS files
    git_oid: str | None  # small files: the git blob sha1


@dataclasses.dataclass
class Report:
    checked: int = 0
    problems: list[str] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def git_blob_sha1(data: bytes) -> str:
    """The id `git hash-object` gives a file with this content."""
    h = hashlib.sha1(usedforsecurity=False)
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def parse_tree(tree: Iterable[dict[str, Any]]) -> list[Entry]:
    """The files of a Hub tree listing, sorted by path."""
    entries = []
    for item in tree:
        if item.get("type") != "file":
            continue
        lfs = item.get("lfs")
        if lfs:
            entries.append(Entry(item["path"], int(lfs["size"]), str(lfs["oid"]), None))
        else:
            entries.append(
                Entry(item["path"], int(item["size"]), None, str(item["oid"]))
            )
    return sorted(entries, key=lambda e: e.path)


def fetch_tree(repo: str, revision: str) -> list[dict[str, Any]]:
    """The recursive tree of one revision, with LFS metadata."""
    rev = urllib.parse.quote(revision, safe="")
    url = f"https://huggingface.co/api/models/{repo}/tree/{rev}?recursive=1&expand=1"
    with urllib.request.urlopen(url, timeout=60) as r:
        data: list[dict[str, Any]] = json.load(r)
    return data


def file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def check_one(root: pathlib.Path, e: Entry) -> str | None:
    """One problem line for this file, or None when it matches."""
    p = root / e.path
    if not p.is_file():
        return f"{e.path}: missing"
    size = p.stat().st_size
    if size != e.size:
        return f"{e.path}: size {size}, expected {e.size}"
    if e.sha256 is not None:
        return None if file_sha256(p) == e.sha256 else f"{e.path}: sha256 differs"
    return (
        None
        if git_blob_sha1(p.read_bytes()) == e.git_oid
        else f"{e.path}: git blob id differs"
    )


def verify(
    root: pathlib.Path,
    entries: Sequence[Entry],
    *,
    skip: Iterable[str] = DEFAULT_SKIP,
    workers: int = 4,
) -> Report:
    """Compare every entry not in `skip` with the file under `root`."""
    skipped = set(skip)
    todo = [e for e in entries if e.path not in skipped]
    report = Report(checked=len(todo))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for problem in pool.map(lambda e: check_one(root, e), todo):
            if problem:
                report.problems.append(problem)
    return report


def sha256sum_manifest(entries: Iterable[Entry]) -> str:
    """The LFS files' hashes as `sha256sum -c` reads them, sorted by path."""
    rows = sorted((e.path, e.sha256) for e in entries if e.sha256 is not None)
    return "".join(f"{digest}  {path}\n" for path, digest in rows)


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("repo", help="the Hub repository, owner/name")
    p.add_argument("revision", help="the approved commit (a full or short sha)")
    p.add_argument("dir", type=pathlib.Path, help="the local copy")
    p.add_argument(
        "--manifest",
        type=pathlib.Path,
        help="also write the LFS hashes in sha256sum format here",
    )
    p.add_argument(
        "--workers", type=int, default=4, help="files hashed at once (default 4)"
    )
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)

    entries = parse_tree(fetch_tree(args.repo, args.revision))
    if not entries:
        logger.error("%s @ %s: the Hub lists no files", args.repo, args.revision)
        return 1
    if args.manifest:
        args.manifest.write_text(sha256sum_manifest(entries))
        logger.info("wrote %s", args.manifest)
    report = verify(args.dir, entries, workers=args.workers)
    for problem in report.problems:
        logger.error("%s", problem)
    total_gib = sum(e.size for e in entries) / 2**30
    if report.ok:
        logger.info(
            "%s @ %s: %d of %d files match (%.1f GiB) in %s",
            args.repo,
            args.revision,
            report.checked,
            len(entries),
            total_gib,
            args.dir,
        )
        return 0
    logger.error(
        "%s @ %s: %d problem(s) in %s",
        args.repo,
        args.revision,
        len(report.problems),
        args.dir,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
