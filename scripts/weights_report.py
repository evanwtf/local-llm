"""Advisory report: every model on disk, its size, and a verdict with a reason. #464

The operator asked for a census that **recommends** and never deletes
(2026-09-17). `scripts/prune_models.py` deletes from a hand-kept list that goes
stale with every download; this reads the disk instead, through
`benchmarks/agent/model_inventory.py`'s roots, so it works on any machine.

The verdict starts from size, and the evidence has to overturn it (operator,
2026-09-17: "anything > 200GB should PROBABLY be deleted unless we've
determined it's great ... anything <10 GB should PROBABLY keep just because
the cost is low"):

    partial download (*.incomplete)          DELETE
    no weight files at all                   DELETE
    named in RECOMMENDATIONS.md              KEEP
    > 200 GB                                 PROBABLY DELETE
    < 10 GB                                  PROBABLY KEEP
    10-200 GB, a tasks.toml backend names it KEEP
    10-200 GB, ledger rows or an issue       CONSIDER
    10-200 GB, no footprint in the repo      UNKNOWN

Every verdict depends on the model's history being visible in this repo. The
report prints the evidence it found for each entry (backends, ledger rows,
issues), so an UNKNOWN is a prompt to file the missing issue, not a licence to
delete. "Unused" alone is never a reason to delete (CONVENTIONS.md, the
archive rule).

Usage:

    uv run python scripts/weights_report.py            # sizes every entry (slow)
    uv run python scripts/weights_report.py --issues   # also search issues (gh)

GitHub's issue search splits a name into words, so an issue hit is a lead to
read, not proof that the issue is about that pack.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import pathlib
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterable, Mapping

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import model_inventory

import logs

logger = logging.getLogger(__name__)

GB = 10**9
BIG = 200 * GB  # above: PROBABLY DELETE unless recommended (operator, #464)
SMALL = 10 * GB  # below: PROBABLY KEEP, the cost is low (operator, #464)

DELETE = "DELETE"
PROBABLY_DELETE = "PROBABLY DELETE"
CONSIDER = "CONSIDER"
UNKNOWN = "UNKNOWN"
PROBABLY_KEEP = "PROBABLY KEEP"
KEEP = "KEEP"
ORDER = (DELETE, PROBABLY_DELETE, CONSIDER, UNKNOWN, PROBABLY_KEEP, KEEP)

WEIGHT_SUFFIXES = (".gguf", ".safetensors", ".bin", ".npz")
HF_PREFIX = "models--"


@dataclasses.dataclass(frozen=True)
class Item:
    """One entry from the inventory, with the root it came from."""

    root: str
    path: pathlib.Path
    ollama: bool = False


@dataclasses.dataclass(frozen=True)
class Facts:
    """What the report knows about one entry. `issues` is None when not searched."""

    size: int | None
    incomplete: int
    has_weights: bool
    backends: tuple[str, ...]
    ledger_rows: int
    recommended: bool
    issues: tuple[int, ...] | None


def verdict(f: Facts) -> tuple[str, str]:
    """The recommendation and its reason. Size first; evidence overturns it."""
    if f.incomplete:
        return DELETE, f"partial download: {f.incomplete} *.incomplete file(s)"
    if not f.has_weights:
        return DELETE, "no weight files; a leftover directory"
    if f.recommended:
        return KEEP, "named in RECOMMENDATIONS.md"
    if f.size is not None and f.size > BIG:
        return PROBABLY_DELETE, (
            f"{f.size / GB:.0f} GB is over 200 GB and not in RECOMMENDATIONS.md"
        )
    if f.size is not None and f.size < SMALL:
        return PROBABLY_KEEP, f"{f.size / GB:.1f} GB is under 10 GB; the cost is low"
    if f.backends:
        return KEEP, "a tasks.toml backend names it"
    if f.ledger_rows or f.issues:
        return CONSIDER, "measured or discussed here, and no backend names it"
    return UNKNOWN, "no backend, ledger row, or issue names it"


def link_targets(items: list[Item], real: dict[Item, pathlib.Path]) -> dict[Item, Item]:
    """Entries whose real path lies inside another entry's: deleting one frees
    nothing. LM Studio's `Qwen3.8-Flash-Next-UD-Q3_K_XL` is a symlink into
    `~/models/Qwen3.8-Flash-Next-GGUF`, and the report once listed its 90 GB
    as a second saving (2026-09-30). Of two entries with one real path, the
    first is kept. Pure: `real` maps each entry to its resolved path."""
    out: dict[Item, Item] = {}
    order = [i for i in items if i in real]
    for n, item in enumerate(order):
        r = real[item]
        for m, other in enumerate(order):
            if other is item:
                continue
            ro = real[other]
            if ro in r.parents or (ro == r and m < n):
                out[item] = other
                break
    return out


def repo_id(path: pathlib.Path) -> str:
    """`org/name` for a Hugging Face cache dir `models--org--name`, else ''."""
    if not path.name.startswith(HF_PREFIX):
        return ""
    return path.name[len(HF_PREFIX) :].replace("--", "/", 1)


def needle(item: Item) -> str:
    """The string that names this entry in the repo's text."""
    if item.ollama:
        return str(item.path)
    rid = repo_id(item.path)
    if rid:
        return rid.split("/", 1)[1]
    if item.path.suffix in WEIGHT_SUFFIXES:
        return item.path.stem
    return item.path.name


def reacquire(item: Item) -> str:
    """The command that gets it back, or '' when the repo does not record one."""
    if item.ollama:
        return f"ollama pull {item.path}"
    rid = repo_id(item.path)
    return f"hf download {rid}" if rid else ""


_SIZE = re.compile(r"\s(\d+(?:\.\d+)?)\s+(KB|MB|GB|TB)\s")
_UNIT = {"KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}


def parse_ollama_sizes(text: str) -> dict[str, int]:
    """Tag -> bytes from `ollama list`. Ollama prints decimal units."""
    sizes: dict[str, int] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("NAME"):
            continue
        m = _SIZE.search(line + " ")
        if m:
            sizes[line.split()[0]] = round(float(m.group(1)) * _UNIT[m.group(2)])
    return sizes


def scan_weights(path: pathlib.Path) -> tuple[int, bool]:
    """(count of *.incomplete files, whether any weight file exists).

    A Hugging Face cache keeps suffix-less blobs and names them only in
    `snapshots/`, so a weight file counts whether it is a file or a symlink.
    """
    if path.is_file():
        return 0, path.suffix in WEIGHT_SUFFIXES
    incomplete = 0
    weights = False
    for p in path.rglob("*"):
        if p.name.endswith(".incomplete"):
            incomplete += 1
        elif p.suffix in WEIGHT_SUFFIXES:
            weights = True
    return incomplete, weights


def file_needles(path: pathlib.Path) -> tuple[str, ...]:
    """The GGUF file names directly inside a pack.

    A row often names the file, not the pack: #834's ds4 rows reach
    `qwen3.8-flash-next-ds4-upstream-q4k/Qwen3.8-Flash-Next-Q4.gguf` through a
    symlink in the engine tree, and the pack name appears nowhere. Only GGUF
    names count: `model-00001.safetensors` would match every MLX pack.
    """
    if not path.is_dir():
        return ()
    return tuple(sorted(p.name for p in path.iterdir() if p.suffix == ".gguf"))


def count_rows(
    lines: Iterable[str], groups: Mapping[Item, tuple[str, ...]]
) -> dict[Item, int]:
    """How many lines name each item, by any of its needles. A line counts once."""
    counts = dict.fromkeys(groups, 0)
    for line in lines:
        for item, needles in groups.items():
            if any(n in line for n in needles):
                counts[item] += 1
    return counts


def backends_naming(
    backends: Mapping[str, Mapping[str, object]], text: str
) -> tuple[str, ...]:
    """The tasks.toml backends whose table mentions `text` anywhere."""
    return tuple(name for name, table in backends.items() if text in json.dumps(table))


def search_issues(text: str) -> tuple[int, ...] | None:
    """Issue numbers that mention `text`, open or closed. None if gh fails."""
    try:
        out = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--repo",
                "evanwtf/local-llm",
                "--state",
                "all",
                "--limit",
                "20",
                "--json",
                "number",
                "--search",
                f'"{text}"',
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return tuple(sorted(i["number"] for i in json.loads(out.stdout)))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def inventory() -> list[Item]:
    items = [
        Item(root.label, entry.path)
        for root in model_inventory.default_roots()
        for entry in model_inventory.list_root(root)
    ]
    items += [
        Item("Ollama", e.path, ollama=True) for e in model_inventory.ollama_entries()
    ]
    return items


def ollama_sizes() -> dict[str, int]:
    try:
        out = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_ollama_sizes(out.stdout)


def newest_mtime(item: Item) -> str:
    """The newest modification date among the pack and its direct children."""
    if item.ollama:
        return "n/a"
    real = item.path.resolve()
    try:
        stamps = [real.stat().st_mtime]
        if real.is_dir():
            stamps += [p.lstat().st_mtime for p in real.iterdir()]
    except OSError:
        return "?"
    return time.strftime("%Y-%m-%d", time.localtime(max(stamps)))


def _gb(size: int | None) -> str:
    return "?" if size is None else f"{size / GB:.1f}"


def main(argv: list[str] | None = None) -> int:
    logs.configure(fmt=logs.PLAIN)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--issues", action="store_true", help="also search issues with gh (slow)"
    )
    args = parser.parse_args(argv)

    items = inventory()
    aliases = link_targets(items, {i: i.path.resolve() for i in items if not i.ollama})
    items = [i for i in items if i not in aliases]
    backends = tomllib.loads((REPO / "benchmarks/agent/tasks.toml").read_text())[
        "backend"
    ]
    recommendations = (REPO / "RECOMMENDATIONS.md").read_text()
    needles = {item: needle(item) for item in items}
    ledger_lines: list[str] = []
    for ledger in sorted(REPO.glob("hardware/*/results.jsonl")):
        ledger_lines += ledger.read_text().splitlines()
    groups = {
        item: (needles[item],)
        + ((file_needles(item.path.resolve())) if not item.ollama else ())
        for item in items
    }
    rows = count_rows(ledger_lines, groups)
    osizes = ollama_sizes()

    started = time.monotonic()
    rated: list[tuple[str, str, Item, Facts]] = []
    modified: dict[Item, str] = {}
    for item in items:
        if item.ollama:
            size, incomplete, weights = osizes.get(str(item.path)), 0, True
        else:
            real = item.path.resolve()
            size = model_inventory.dir_size_bytes(real)
            incomplete, weights = scan_weights(real)
        n = needles[item]
        f = Facts(
            size=size,
            incomplete=incomplete,
            has_weights=weights,
            backends=tuple(
                dict.fromkeys(
                    b for g in groups[item] for b in backends_naming(backends, g)
                )
            ),
            ledger_rows=rows[item],
            recommended=n in recommendations,
            issues=search_issues(n) if args.issues else None,
        )
        label, reason = verdict(f)
        rated.append((label, reason, item, f))
        modified[item] = newest_mtime(item)
    elapsed = time.monotonic() - started

    by_name: dict[str, list[Item]] = {}
    for item in items:
        by_name.setdefault(needles[item].lower(), []).append(item)

    logger.info("# Model weights on disk: advisory only (#464). Nothing is deleted.")
    for label in ORDER:
        group = [r for r in rated if r[0] == label]
        if not group:
            continue
        total = sum(r[3].size or 0 for r in group)
        logger.info("")
        logger.info("## %s: %d entries, %.1f GB", label, len(group), total / GB)
        for _, reason, item, f in sorted(group, key=lambda r: -(r[3].size or 0)):
            where = f"{item.path} ({item.root})" if item.ollama else str(item.path)
            logger.info("- %s GB  %s", _gb(f.size), where)
            logger.info("    why: %s (modified %s)", reason, modified[item])
            issues = (
                "not searched"
                if f.issues is None
                else (", ".join(f"#{i}" for i in f.issues) or "none")
            )
            logger.info(
                "    evidence: backends %s; ledger rows %d; issues %s",
                ", ".join(f.backends) or "none",
                f.ledger_rows,
                issues,
            )
            twins = [o for o in by_name[needles[item].lower()] if o is not item]
            if twins:
                logger.info(
                    "    same name also at: %s", ", ".join(str(o.path) for o in twins)
                )
            if cmd := reacquire(item):
                logger.info("    re-download: %s", cmd)

    if aliases:
        logger.info("")
        logger.info("## Links: %d entries; deleting one frees nothing", len(aliases))
        for link, target in aliases.items():
            logger.info("- %s -> inside %s", link.path, target.path)

    logger.info("")
    logger.info("## Totals by root")
    for root in dict.fromkeys(item.root for item in items):
        total = sum(r[3].size or 0 for r in rated if r[2].root == root)
        logger.info("- %s: %.1f GB", root, total / GB)
    seen: set[int] = set()
    for root in model_inventory.default_roots():
        if not root.path.exists():
            continue
        real = root.path.resolve()
        dev = real.stat().st_dev
        if dev in seen:
            continue
        seen.add(dev)
        free = shutil.disk_usage(real).free
        logger.info("- free on the volume under %s: %.1f GB", real, free / GB)
    logger.info("")
    logger.info("Sized %d entries in %.0f s.", len(items), elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
