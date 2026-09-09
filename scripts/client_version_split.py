"""Which client version took which rows, and what that confounds (#137, #131).

The version was on every row from the start, keyed by client name inside
`env` alongside every other client on the machine. Seeing the split required
joining `client` to `env` across 1394 rows, so nobody did, and the result was
that the three newest ds4 backends were measured under OpenCode 1.18.27 while
everything older ran on 1.18.25 -- with every comparison between them
carrying the client version as a hidden second variable.

`client_version` (225b90c, backfilled in aedd5f3) makes that a lookup. This
makes it a command, so the check is repeatable rather than re-derived by hand
each time somebody wonders.

Two questions, and they are different:

* **Does one backend's rows span versions?** That contaminates the cell
  itself, and none currently do.
* **Are two backends on different versions?** That contaminates any
  comparison between them, and is the situation #137 describes.

    uv run python scripts/client_version_split.py <results.jsonl> [--client opencode]
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import pathlib
import sys

logger = logging.getLogger(__name__)


def rows_by_backend(
    path: pathlib.Path, client: str | None = None
) -> dict[str, collections.Counter]:
    """backend -> Counter of client versions seen on its rows."""
    out: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for line in path.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("excluded"):
            continue
        if client and row.get("client") != client:
            continue
        version = row.get("client_version")
        if not isinstance(version, str) or not version.strip():
            continue
        backend = row.get("backend")
        if isinstance(backend, str):
            out[backend][version] += 1
    return dict(out)


def split_backends(table: dict[str, collections.Counter]) -> list[str]:
    """Backends whose own rows span more than one client version.

    These are worse than a confounded comparison: the cell's own pass rate
    mixes two clients, so it cannot be split apart afterwards.
    """
    return sorted(b for b, versions in table.items() if len(versions) > 1)


def confounded_pairs(table: dict[str, collections.Counter]) -> list[tuple[str, str]]:
    """Backend pairs measured under disjoint client versions.

    Comparing two of these compares the clients as much as the backends.
    Returns pairs, not a count, because which ones matters -- a pair nobody
    compares is not a problem.
    """
    out: list[tuple[str, str]] = []
    names = sorted(table)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if not (set(table[a]) & set(table[b])):
                out.append((a, b))
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=pathlib.Path)
    p.add_argument("--client", default=None)
    args = p.parse_args(argv)

    table = rows_by_backend(args.results, args.client)
    if not table:
        logger.info("no rows carry a client_version")
        return 0

    by_version: dict[str, list[str]] = collections.defaultdict(list)
    for backend, versions in sorted(table.items()):
        for version in versions:
            by_version[version].append(backend)
    logger.info("client versions and the backends measured under them:")
    for version, backends in sorted(by_version.items()):
        logger.info("  %-10s %s", version, ", ".join(sorted(backends)))

    spanning = split_backends(table)
    logger.info("")
    if spanning:
        logger.warning(
            "backends whose OWN rows span versions (the cell itself is mixed): %s",
            ", ".join(spanning),
        )
    else:
        logger.info("no backend's own rows span a version -- every cell is consistent")

    pairs = confounded_pairs(table)
    if pairs:
        logger.warning(
            "%d backend pairs were measured under disjoint client versions; "
            "comparing one of these compares the clients too (#137)",
            len(pairs),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
