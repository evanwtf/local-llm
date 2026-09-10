"""A directory listing of every model on disk, with full paths.

Each runtime keeps its models in its own tree, and searching the wrong one is a
false negative -- a confident "the pack is not on disk" that is only looking in
the wrong place. It cost a session real time on 2026-09-10: the mlx-serve Qwen
pack was reported missing from `~/models` while it sat, fully pulled, in
`~/.mlx-serve/models`. `docs/model-locations.md` writes the roots down, but a
doc drifts; this reads the disk, so preflight can print the current truth
before every run.

The output is a directory listing of the known roots with **full absolute
paths**, so a later reader never has to guess where a pack lives:

    models on disk (5 roots populated, 36 entries):
      ds4/llama.cpp GGUF -- /Users/x/models (7)
        /Users/x/models/qwen3.8-flash-next-ds4-q4k-imatrix
        /Users/x/models/deepseek-v4-flash-aproj
        ...
      mlx-serve -- /Users/x/.mlx-serve/models (2)
        /Users/x/.mlx-serve/models/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit
        ...
      Ollama -- /Users/x/.ollama/models (14)
        qwen3.5:9b-mlx  (ollama tag)
        ...

The walk is readdir only -- no `du`, no recursion into a pack -- so it is cheap
enough to run on every preflight. Sizes are opt-in (`--sizes`) because `du` on
~600 GB is not.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "scripts" / "lib"))

logger = logging.getLogger(__name__)

# GGUF glob suffixes that live as bare files in ~/models (not every model is a
# directory). Kept small and explicit; a new suffix is a one-line change.
_WEIGHT_SUFFIXES = (".gguf", ".safetensors")


@dataclasses.dataclass(frozen=True)
class Entry:
    """One model on disk. `path` is absolute; `label` is how to read it."""

    path: pathlib.Path
    label: str = ""


@dataclasses.dataclass(frozen=True)
class Root:
    """A model tree: its label, its filesystem path, and how deep packs sit.

    `depth` is how many directory levels below `path` a pack is:
      1 -- immediate children are packs (`~/models/<pack>`)
      2 -- packs are one level down (`~/.mlx-serve/models/<org>/<pack>`)
    """

    label: str
    path: pathlib.Path
    depth: int = 1


def default_roots(home: pathlib.Path | None = None) -> list[Root]:
    """The dir-based model roots. Ollama is handled separately (it is not one)."""
    h = home or pathlib.Path.home()
    return [
        Root("ds4/llama.cpp GGUF", h / "models", depth=1),
        Root("mlx-serve", h / ".mlx-serve" / "models", depth=2),
        Root("LM Studio", h / ".lmstudio" / "models", depth=2),
        Root("Hugging Face cache", h / ".cache" / "huggingface" / "hub", depth=1),
    ]


def list_root(root: Root) -> list[Entry]:
    """Every pack under a dir-based root, as absolute-path entries.

    Missing root -> empty list (a runtime that was never installed). At `depth`
    1 a bare weight file (`*.gguf`, `*.safetensors`) counts as a pack too, so a
    single-file GGUF is not invisible. Hidden entries and non-pack files above
    the pack depth are skipped.
    """
    if not root.path.is_dir():
        return []
    entries: list[Entry] = []
    for child in sorted(root.path.iterdir()):
        if child.name.startswith("."):
            continue
        if root.depth == 1:
            if child.is_dir() or child.suffix in _WEIGHT_SUFFIXES:
                entries.append(Entry(child))
        else:  # depth == 2: list <org>/<pack>
            if not child.is_dir():
                continue
            for pack in sorted(child.iterdir()):
                if pack.is_dir() and not pack.name.startswith("."):
                    entries.append(Entry(pack))
    return entries


def parse_ollama_list(text: str) -> list[Entry]:
    """Ollama tags from `ollama list` output.

    Ollama stores content-addressed blobs, so there is no per-model directory to
    list; the tag name is the identity. The header row and blank lines are
    dropped. Each entry is labelled so a reader does not mistake the tag for a
    path.
    """
    entries: list[Entry] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("NAME"):
            continue
        name = stripped.split()[0]
        entries.append(Entry(pathlib.Path(name), label="ollama tag"))
    return entries


def ollama_entries(home: pathlib.Path | None = None) -> list[Entry]:
    """Ollama tags via `ollama list`, or [] when Ollama is absent/unreachable.

    Falls back silently: preflight must never hard-fail because a runtime CLI is
    missing.
    """
    h = home or pathlib.Path.home()
    if not (h / ".ollama" / "models").is_dir():
        return []
    try:
        out = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_ollama_list(out.stdout)


def dir_size_bytes(path: pathlib.Path) -> int | None:
    """Best-effort `du` for one pack. None on error or timeout. Opt-in only."""
    try:
        out = subprocess.run(
            ["du", "-sk", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return int(out.stdout.split()[0]) * 1024
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _human(nbytes: int) -> str:
    val = float(nbytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024 or unit == "TB":
            return f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} TB"


def log_inventory(
    *, home: pathlib.Path | None = None, sizes: bool = False
) -> dict[str, list[Entry]]:
    """Log a directory listing of every model root. Returns the inventory.

    Never raises: a root that cannot be read is logged as empty, not fatal.
    """
    inv: dict[str, list[Entry]] = {}
    for root in default_roots(home):
        inv[root.label] = list_root(root)
    inv["Ollama"] = ollama_entries(home)

    roots_with_any = sum(1 for v in inv.values() if v)
    total = sum(len(v) for v in inv.values())
    logger.info(
        "models on disk (%d roots populated, %d entries):", roots_with_any, total
    )

    h = home or pathlib.Path.home()
    root_path = {
        "ds4/llama.cpp GGUF": h / "models",
        "mlx-serve": h / ".mlx-serve" / "models",
        "LM Studio": h / ".lmstudio" / "models",
        "Hugging Face cache": h / ".cache" / "huggingface" / "hub",
        "Ollama": h / ".ollama" / "models",
    }
    for label, entries in inv.items():
        logger.info("  %s -- %s (%d)", label, root_path[label], len(entries))
        for e in entries:
            if e.label:
                logger.info("    %s  (%s)", e.path, e.label)
            elif sizes:
                nbytes = dir_size_bytes(e.path)
                logger.info(
                    "    %s  (%s)", e.path, _human(nbytes) if nbytes else "size n/a"
                )
            else:
                logger.info("    %s", e.path)
    return inv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        action="store_true",
        help="compute each pack's size with du (slow on large trees)",
    )
    args = parser.parse_args(argv)
    # provenance.configure(), not logging.basicConfig: an entry point in this
    # package stamps the harness commit and uses the one ISO-8601 line format
    # (tests/test_logging_format.py, benchmarks/agent/test_provenance.py).
    import provenance

    provenance.configure()
    log_inventory(sizes=args.sizes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
