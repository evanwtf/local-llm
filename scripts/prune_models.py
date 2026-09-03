"""Delete local model weights that are superseded and re-downloadable (#111).

The disk is 84% full with roughly 2 TB of model weights, and six to eight of
them carry every result this project has published. This script deletes the
rest -- but only the ones that are **both** superseded on our own measurements
**and** re-downloadable, which is the line `CONVENTIONS.md` draws:

    Fair game:     superseded on the numbers, easy to re-download.
    Not fair game: anything hard or impossible to reacquire. Ask first.

So this is deliberately conservative. Three tiers, and only one of them is ever
deleted without you naming it:

    KEEP    never listed for deletion, never touched
    DELETE  measured here and beaten, re-downloadable; removed by --delete
    REVIEW  a judgement call, or blocked on open work; needs --also <name>

**It is a dry run unless you pass --delete.** Every entry prints the exact
command to get the weights back, because "I can re-download it" is the whole
justification for removing it.

    uv run python scripts/prune_models.py                 # show the plan
    uv run python scripts/prune_models.py --delete        # do the DELETE tier
    uv run python scripts/prune_models.py --delete --also glm52   # plus one REVIEW

Two things this script will not do:

- **It never removes an Ollama model with `rm`.** Ollama content-addresses its
  blobs and shares them between tags, so deleting a directory by hand corrupts
  the store for every model that shares a layer. It shells out to `ollama rm`.
- **It refuses to run while a model server is up**, because deleting a file a
  running server has mapped gives you a half-alive process and a benchmark that
  fails for a reason nobody will find.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

KEEP = "KEEP"
DELETE = "DELETE"
REVIEW = "REVIEW"

HOME = pathlib.Path.home()

# Only paths under these roots may ever be touched. A path that escapes them is
# a bug in the table below, and the script refuses rather than deleting it.
ALLOWED_ROOTS = (HOME / "models", HOME / "git/ds4/gguf")


@dataclasses.dataclass(frozen=True)
class Entry:
    """One model on disk, and what we decided about it."""

    name: str
    path: str | None  # None for an Ollama tag
    ollama_tag: str | None
    tier: str
    reason: str
    redownload: str

    def resolved(self) -> pathlib.Path | None:
        return (HOME / self.path) if self.path else None


# ---------------------------------------------------------------------------
# The decision table. Every DELETE line cites the measurement that beat it.
# ---------------------------------------------------------------------------

PLAN: tuple[Entry, ...] = (
    # --- KEEP: the eight that carry the published results ------------------
    Entry(
        "ds4-primary",
        "git/ds4/gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf",
        None,
        KEEP,
        "the ds4 primary: 30/30 at 115s, and 46/46 under Claude Code. The single "
        "best-evidenced backend in the repo.",
        "",
    ),
    Entry(
        "qwen38fnds4-pack",
        "models/qwen3.8-flash-next-ds4-q4",
        None,
        KEEP,
        "the ds4 fast-pack, 36/45, and the subject of open work (#77, #112). "
        "Contains a 0-byte symlink the manifest depends on -- never rewrite this "
        "directory.",
        "",
    ),
    Entry(
        "qwen38fnq3",
        "models/Qwen3.8-Flash-Next-GGUF/UD-Q3_K_XL",
        None,
        KEEP,
        "30/30 at 90s: our most-measured backend, and the reference the other "
        "Qwen3.8-Flash-Next quants are judged against.",
        "",
    ),
    Entry(
        "qwen38fnq3-mtp",
        "models/Qwen3.8-Flash-Next-GGUF/MTP",
        None,
        KEEP,
        "6.4 GB MTP head for the speculative-decoding work (#19, #77). Small and "
        "in the queue.",
        "",
    ),
    Entry(
        "qwen38fnq3reap",
        "models/Qwen3.8-Flash-Next-REAP320",
        None,
        KEEP,
        "21/21 at 110s, and 38 s/1k output tokens -- fourth-best rate measured.",
        "",
    ),
    Entry(
        "glm53-antirez",
        "git/ds4/gguf/GLM-5.3-Flash-Q2.gguf",
        None,
        KEEP,
        "22/24 on ds4, and the ONLY non-Qwen/non-DeepSeek lineage we can run. "
        "#16 is open precisely because the fallback plan is a monoculture; "
        "deleting this makes that worse.",
        "",
    ),
    Entry(
        "ornith15",
        None,
        "ornith-1.5:35b",
        KEEP,
        "21/21 at 44s and 21 s/1k -- the fastest backend measured, and quoted in "
        "RECOMMENDATIONS.md.",
        "",
    ),
    Entry(
        "qwen36coding",
        None,
        "qwen3.6:27b-coding-mxfp8",
        KEEP,
        "24/24. RECOMMENDATIONS.md tells a newcomer to install exactly this one; "
        "deleting it breaks the documented on-ramp.",
        "",
    ),
    Entry(
        "gemma426",
        None,
        "gemma4:26b-mlx-bf16",
        KEEP,
        "11/11 at 150s and 21 s/1k, tied fastest per token. Also the model "
        "CONVENTIONS.md names as kept under the archive rule.",
        "",
    ),
    # --- DELETE: measured here and beaten, and re-downloadable -------------
    Entry(
        "glm52",
        "models/GLM-5.2-GGUF",
        None,
        DELETE,
        "196.6 GiB IQ2_XXS. Streams into 30.8 GiB but is 14x too slow to use -- "
        "measured and rejected 2026-08-29. Superseded by GLM-5.3-Flash-Q2, which "
        "is kept.",
        "hf download unsloth/GLM-5.2-GGUF --include 'UD-IQ2_XXS/*' "
        "--local-dir ~/models/GLM-5.2-GGUF   # HF_HUB_ENABLE_HF_TRANSFER=1",
    ),
    Entry(
        "atomicchat",
        "models/AtomicChat-Qwen3.8-Flash-Next",
        None,
        REVIEW,
        "4-bit -M64 tune, tested and rejected: +28% slower than the 3-bit we keep "
        "(995.1 -> 1276.0 s), and it never entered the recommended set. It is in "
        "REVIEW rather than DELETE for one reason only: **we never wrote down the "
        "HF repo it came from**, so 'just re-download it' is not verified. The "
        "quant is Qwen3.8-Flash-Next-AD-4.27bpw-Q4_K_M-M64, 33 shards. Confirm "
        "the repo resolves before deleting 88 GiB on the strength of a guess.",
        "hf download <UNRECORDED repo>/Qwen3.8-Flash-Next-AD-4.27bpw-Q4_K_M-M64 "
        "--local-dir ~/models/AtomicChat-Qwen3.8-Flash-Next   # verify first",
    ),
    Entry(
        "qwen38fnq2",
        "models/Qwen3.8-Flash-Next-GGUF/UD-Q2_K_XL",
        None,
        DELETE,
        "13/16 at a 5235s median -- the worst suite time recorded here, against "
        "the Q3 sibling's 30/30 at 90s. Same model, strictly dominated.",
        "hf download unsloth/Qwen3.8-Flash-Next-GGUF --include 'UD-Q2_K_XL/*' "
        "--local-dir ~/models/Qwen3.8-Flash-Next-GGUF",
    ),
    Entry(
        "ds4-mxfp4",
        "git/ds4/gguf/DeepSeek-V4-Flash-MXFP4Experts-F16HC-F16Compressor-F16Indexer-Q8Attn-Q8Shared-Q8Out-chat-v2-mxfp4-0731.gguf",
        None,
        DELETE,
        "145 GB experimental MXFP4 quant, superseded by the Layers37-42 pack that "
        "serves the ds4 primary. Regenerable from the HF base plus the imatrix, "
        "which are both kept.",
        "regenerate with ds4's quantize tooling from ~/models/DeepSeek-V4-Flash-hf "
        "and ~/git/ds4/gguf/imatrix (see benchmarks/ds4/coding/)",
    ),
    Entry(
        "ds4-arm-a",
        "git/ds4/gguf/ARM-A-f16attn-0731.gguf",
        None,
        DELETE,
        "an A/B arm from the attention-precision experiment, not a served backend. "
        "The finding it produced is recorded; the 91 GB artifact is not needed to "
        "keep it.",
        "regenerate from the HF base plus imatrix, as above",
    ),
    Entry(
        "ds4-arm-b",
        "git/ds4/gguf/ARM-B-q8attn-0731.gguf",
        None,
        DELETE,
        "the other arm of the same experiment. Same reasoning.",
        "regenerate from the HF base plus imatrix, as above",
    ),
    Entry(
        "ornith-35b-retired",
        None,
        "ornith:35b",
        DELETE,
        "`retired` in tasks.toml: superseded by ornith-1.5:35b, and a GGUF through "
        "Ollama is llama.cpp with a wrapper. Its 27 rows stay valid -- the config "
        "block is the record, not the weights.",
        "ollama pull ornith:35b",
    ),
    # --- REVIEW: judgement calls and open work. --also to include one. ------
    Entry(
        "deepseek-hf-base",
        "models/DeepSeek-V4-Flash-hf",
        None,
        REVIEW,
        "149 GB of ORIGINAL base weights. Every ds4 DeepSeek quant we keep was "
        "derived from these, and this project's premise is that open weights may "
        "not stay downloadable. Highest option value on the disk -- deleting it "
        "is the one choice here you cannot cheaply undo. Recommend keeping.",
        "hf download deepseek-ai/DeepSeek-V4-Flash --local-dir ~/models/DeepSeek-V4-Flash-hf",
    ),
    Entry(
        "ds4-aproj-q4",
        "git/ds4/gguf/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ4-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf",
        None,
        REVIEW,
        "an arm of #51 (Q4_K attention worth +12.6% decode), which is OPEN and "
        "waiting on ds4#952. Deleting it now would delete the experiment.",
        "regenerate from the HF base plus imatrix",
    ),
    Entry(
        "ds4-aproj-q8",
        "git/ds4/gguf/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf",
        None,
        REVIEW,
        "the control arm for the same open issue (#51).",
        "regenerate from the HF base plus imatrix",
    ),
    Entry(
        "glm53-unsloth",
        "models/GLM-5.3-Flash-GGUF",
        None,
        REVIEW,
        "101 GB Unsloth GLM-5.3. Serves the `glm53` backend (15/15) through "
        "llama.cpp, but antirez's GLM-5.3-Flash-Q2 is kept and is the better path "
        "on ds4 (#25: the two declare different architectures). Redundant unless "
        "you want the llama.cpp GLM route specifically.",
        "hf download unsloth/GLM-5.3-Flash-GGUF --include 'UD-Q2_K_XL/*' "
        "--local-dir ~/models/GLM-5.3-Flash-GGUF",
    ),
    Entry(
        "qwen38flashnext-mlx",
        None,
        "qwen3.8-flash-next:125b-mlx",
        REVIEW,
        "112 GB, and #80 is OPEN specifically to measure it -- that run is what "
        "decides whether this stays. Deleting it now pre-empts the decision "
        "instead of making it.",
        "ollama pull qwen3.8-flash-next:125b-mlx",
    ),
    Entry(
        "qwen36a3b",
        None,
        "qwen3.6:35b-a3b-coding-mxfp8",
        REVIEW,
        "37 GB, and the other half of #80's remaining sweep. Same reasoning.",
        "ollama pull qwen3.6:35b-a3b-coding-mxfp8",
    ),
    Entry(
        "qwen36a3b-q8",
        None,
        "qwen3.6:35b-a3b-q8_0",
        REVIEW,
        "38 GB, no backend in tasks.toml serves it and no row references it. "
        "Probably safe, but 'I cannot find a user' is weaker evidence than "
        "'we measured it and it lost'.",
        "ollama pull qwen3.6:35b-a3b-q8_0",
    ),
    Entry(
        "gemma4-31b",
        None,
        "gemma4:31b-mxfp8",
        REVIEW,
        "32 GB, backend `gemma4`, 15 rows. Beaten by gemma426 on every axis we "
        "measure, but it is a live backend rather than a retired one.",
        "ollama pull gemma4:31b-mxfp8",
    ),
)


# Directories whose contents this script can delete. An rsync/rclone/cp reading
# any of them is a backup in flight, and deleting underneath it produces a
# silently incomplete copy of the very models being backed up.
BACKUP_WATCHED = ("/.ollama/models", "/models", "/ds4/gguf")


def check_no_backup_running() -> list[str]:
    """Name any copy process reading a directory we delete from.

    This exists because of a near miss: a 410 GB `rsync ~/.ollama/models` to a
    NAS was running while this script was about to `ollama rm` six tags. The
    backup would have completed successfully and been missing the models it was
    taken to preserve, which is worse than either outcome alone.
    """
    found = []
    probe = subprocess.run(
        ["ps", "-Ao", "pid,command"], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        return found
    for line in probe.stdout.splitlines()[1:]:
        lowered = line.lower()
        if not any(tool in lowered for tool in ("rsync", "rclone", "/cp ", " cp ")):
            continue
        if "prune_models" in lowered:
            continue
        if any(watched in line for watched in BACKUP_WATCHED):
            found.append(line.strip()[:160])
    return found


def check_no_server_running() -> list[str]:
    """Name any model server holding weights. Deleting under one is unsafe."""
    running = []
    for pattern, label in (
        ("ds4-server", "ds4-server"),
        ("llama-server", "llama-server"),
    ):
        probe = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, check=False
        )
        if probe.returncode == 0 and probe.stdout.strip():
            running.append(label)
    # Ollama's own app is normally up and holds nothing until a model is loaded,
    # so it is reported only when it actually has one resident.
    ps = subprocess.run(["ollama", "ps"], capture_output=True, text=True, check=False)
    if ps.returncode == 0:
        rows = [ln for ln in ps.stdout.splitlines()[1:] if ln.strip()]
        if rows:
            running.append(f"ollama ({len(rows)} model(s) resident)")
    return running


def directory_size(path: pathlib.Path) -> int:
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        return path.stat().st_size if not path.is_symlink() else 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def ollama_size_bytes(tag: str) -> int:
    """Size Ollama reports for a tag, or 0 when it is not installed."""
    listing = subprocess.run(
        ["ollama", "list"], capture_output=True, text=True, check=False
    )
    if listing.returncode != 0:
        return 0
    for line in listing.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[0] == tag:
            try:
                value = float(parts[2])
            except ValueError:
                return 0
            unit = parts[3].upper()
            scale = {"KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12}
            return int(value * scale.get(unit, 1))
    return 0


def entry_size(entry: Entry) -> int:
    if entry.ollama_tag:
        return ollama_size_bytes(entry.ollama_tag)
    path = entry.resolved()
    return directory_size(path) if path else 0


def gib(n: int) -> float:
    return n / 1024**3


def is_allowed(path: pathlib.Path) -> bool:
    """A path must sit under an allowed root. Refuse anything else."""
    resolved = path.resolve()
    for root in ALLOWED_ROOTS:
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        # The root itself is never a deletion target, only things inside it.
        if resolved != root.resolve():
            return True
    return False


def remove(entry: Entry, dry_run: bool) -> bool:
    """Delete one entry. Returns True when something was (or would be) removed."""
    if entry.ollama_tag:
        if dry_run:
            logger.info("    would run: ollama rm %s", entry.ollama_tag)
            return True
        # NEVER rm -rf an Ollama model directory: blobs are content-addressed
        # and shared between tags, so removing files by hand corrupts every
        # other model that shares a layer.
        done = subprocess.run(
            ["ollama", "rm", entry.ollama_tag],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            logger.error(
                "    ollama rm %s failed: %s",
                entry.ollama_tag,
                (done.stderr or done.stdout).strip()[:200],
            )
            return False
        logger.info("    removed ollama model %s", entry.ollama_tag)
        return True

    path = entry.resolved()
    if path is None or not path.exists():
        logger.warning("    %s is already gone; nothing to do", entry.name)
        return False
    if not is_allowed(path):
        logger.error(
            "    REFUSING %s: %s is outside the allowed roots", entry.name, path
        )
        return False
    if dry_run:
        logger.info("    would delete: %s", path)
        return True
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    logger.info("    deleted %s", path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="actually delete. Without this the script only prints the plan.",
    )
    parser.add_argument(
        "--also",
        action="append",
        default=[],
        metavar="NAME",
        help="include a REVIEW entry by name. Repeatable.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="proceed even though a model server is running. Do not use this.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    known = {e.name for e in PLAN}
    unknown = [n for n in args.also if n not in known]
    if unknown:
        logger.error("unknown --also name(s): %s", ", ".join(unknown))
        logger.error("valid names: %s", ", ".join(sorted(known)))
        return 2

    selected = [
        e
        for e in PLAN
        if e.tier == DELETE or (e.tier == REVIEW and e.name in args.also)
    ]

    logger.info("=" * 78)
    for tier in (KEEP, DELETE, REVIEW):
        entries = [e for e in PLAN if e.tier == tier]
        total = sum(entry_size(e) for e in entries)
        logger.info("%s -- %d entries, %.1f GiB", tier, len(entries), gib(total))
        for e in entries:
            size = entry_size(e)
            mark = ""
            if tier == DELETE:
                mark = (
                    "  <- will be deleted" if args.delete else "  <- would be deleted"
                )
            elif tier == REVIEW:
                mark = "  <- INCLUDED via --also" if e.name in args.also else ""
            target = e.ollama_tag or e.path
            logger.info("  %-22s %7.1f GiB  %s%s", e.name, gib(size), target, mark)
            logger.info("      %s", e.reason)
            if e.redownload:
                logger.info("      re-download: %s", e.redownload)
        logger.info("-" * 78)

    reclaim = sum(entry_size(e) for e in selected)
    logger.info("selected %d entries, %.1f GiB to reclaim", len(selected), gib(reclaim))

    skipped = [e for e in PLAN if e.tier == REVIEW and e.name not in args.also]
    if skipped:
        logger.info(
            "%d REVIEW entries NOT selected (%.1f GiB). Add with --also <name>.",
            len(skipped),
            gib(sum(entry_size(e) for e in skipped)),
        )

    if not args.delete:
        logger.info("")
        logger.info("DRY RUN. Nothing was deleted. Re-run with --delete to proceed.")
        for e in selected:
            remove(e, dry_run=True)
        return 0

    backups = check_no_backup_running()
    if backups and not args.force:
        logger.error("")
        logger.error("REFUSING TO DELETE: a backup appears to be reading these:")
        for line in backups:
            logger.error("    %s", line)
        logger.error("Let it finish. Deleting underneath a running backup gives")
        logger.error("you a copy that completes successfully and is missing the")
        logger.error("models it was taken to preserve.")
        return 1

    running = check_no_server_running()
    if running and not args.force:
        logger.error("")
        logger.error("REFUSING TO DELETE: these are running and may hold weights:")
        for name in running:
            logger.error("    %s", name)
        logger.error("Stop them first. Deleting a file a live server has mapped")
        logger.error("leaves a half-alive process and a benchmark that fails")
        logger.error("for a reason nobody will find.")
        return 1

    before = shutil.disk_usage(HOME).free
    removed = sum(1 for e in selected if remove(e, dry_run=False))
    after = shutil.disk_usage(HOME).free
    logger.info("")
    logger.info(
        "removed %d/%d entries; free space %.1f -> %.1f GiB (+%.1f)",
        removed,
        len(selected),
        gib(before),
        gib(after),
        gib(after - before),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
