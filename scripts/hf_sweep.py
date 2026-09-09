"""Watch Hugging Face for new quants of the models we actually run.

`upstream_sweep.py` watches engines. Nothing watched **models**, so a new GGUF
or MLX build of a model already in our matrix would appear and nobody would
know -- and #84 established that a quant's own declared sampler can move our
numbers, so a re-quant is not cosmetic.

Most new quants are useless here. The top two results for our own fastest model
on 2026-09-02 were `ROCMFP4_STRIX` and `NVFP4-QSA-FP8`: one AMD, one NVIDIA
Blackwell, neither loadable on Metal. So this classifies before it reports,
and hides what cannot run rather than making a person filter it by eye.

    uv run python scripts/hf_sweep.py --hours 24
    uv run python scripts/hf_sweep.py --hours 168 --all   # include unusable
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import provenance

logger = logging.getLogger(__name__)

API = "https://huggingface.co/api/models"

# Families we run, and why we care that a new build appeared.
WATCHED: dict[str, str] = {
    "Qwen3.8-Flash-Next": "our fastest stack (llama.cpp Q3, and the 112 GB MLX build)",
    "DeepSeek-V4-Flash": "our only independent lineage; ds4 serves it",
    "GLM-5.3-Flash": "the other non-Qwen on ds4",
    "Qwen3.6-27B": "the 31 GB 'start here' pick",
    "Ornith-1.5": "fastest measured backend",
    "gemma-4-26b": "the MLX Fast leaderboard model; measured 11/11",
    "gemma-4-31b": "the Google-lineage backend, measured 12/12",
}

# --- hardware profiles -------------------------------------------------------
#
# The usable/unusable lists INVERT between machines, which is why this cannot be
# one global table. EXL2, AWQ and GPTQ are unloadable on Metal and fine on a
# CUDA card; MLX and mxfp8 are the reverse. Getting that backwards would either
# hide every candidate or recommend one that cannot load.
#
# `vram_gb` is what the weights must fit in for a fully-resident run. On Apple
# Silicon that is the Metal working set, not the machine's RAM. `ram_gb` is what
# an MoE offload path can stream from.

PROFILES: dict[str, dict] = {
    "m5-max": {
        "description": "MacBook Pro M5 Max, 128 GB unified, Metal",
        "vram_gb": 112.0,  # the raised iogpu.wired_limit_mb, not the 128 GB
        "ram_gb": 128.0,
        "unified": True,
        "usable": (
            "gguf",
            "mlx",
            "mxfp8",
            "bf16",
            "q4_k",
            "q3_k",
            "q5_k",
            "q6_k",
            "q8_0",
            "iq",
        ),
        # Ampere-and-later NVIDIA formats, AMD formats, and server runtimes.
        "unusable": (
            "nvfp4",
            "fp8",
            "rocm",
            "awq",
            "gptq",
            "exl2",
            "exl3",
            "marlin",
            "int4",
            "w4a16",
            "w8a8",
            "tensorrt",
            "vllm",
            "sglang",
        ),
        # mxfp8 contains "fp8" and is ours; strip it before matching (#78).
        "protect": ("mxfp8",),
    },
    "rtx3080ti": {
        "description": "RTX 3080 Ti, 12 GiB VRAM, Ampere sm_86, 32 GB host RAM",
        "vram_gb": 12.0,
        "ram_gb": 32.0,
        "unified": False,
        # Ampere runs GGUF/CUDA, and the ExLlama and AWQ/GPTQ families.
        "usable": (
            "gguf",
            "exl2",
            "exl3",
            "awq",
            "gptq",
            "q4_k",
            "q3_k",
            "q5_k",
            "q6_k",
            "q8_0",
            "iq",
            "int4",
            "w4a16",
        ),
        # sm_86 has NO FP8 and NO NVFP4 hardware -- those need Ada or Blackwell.
        # MLX and mxfp8 are Apple-only. ROCm is AMD.
        "unusable": ("nvfp4", "fp8", "mxfp8", "mlx", "rocm", "tensorrt"),
        "protect": (),
    },
}


def classify(repo_id: str, profile: str = "m5-max") -> str:
    """'usable' | 'unusable' | 'unknown' on `profile`, from the name.

    Name-based and therefore fallible -- but a repo id is what a sweep has, and
    the alternative is fetching a config for every hit. `unknown` is reported,
    not hidden: an unclassified repo is a question, and silence is how a real
    lead gets skipped.
    """
    prof = PROFILES[profile]
    lowered = repo_id.lower()
    # Strip protected substrings before matching the general list: `mxfp8` is
    # MLX's own 8-bit format and contains "fp8", which means NVIDIA. Matching
    # the general first hides every new build of models we actually run -- the
    # same mistake the ds4 probe made matching model ids by substring (#78).
    probe = lowered
    for keep in prof["protect"]:
        probe = probe.replace(keep, "")
    if any(bad in probe for bad in prof["unusable"]):
        return "unusable"
    if any(good in lowered for good in prof["usable"]):
        return "usable"
    return "unknown"


# Canned searches for "what should I run on this box". Coding-agent first:
# this project measures agents, so a chat-tuned general model is not the answer
# even when it fits. Terms are model FAMILIES, because Hugging Face search
# matches repo names and the quantisers put the family name in theirs.
CANNED: dict[str, tuple[str, ...]] = {
    "coding": (
        "Qwen3.6-27B-coding",
        "Qwen3.8-27B",
        "Ornith-1.5",
        "Devstral",
        "gemma-4-26b",
        "Mistral-Nemo-12B",
        "Qwen3.6-9B",
        "Ornith-1.5-9B",
    ),
    "small": (
        "Qwen3.6-9B",
        "Ornith-1.5-9B",
        "gemma-4-12b",
        "Mistral-Nemo-12B",
        "Qwen3.6-4B",
    ),
    "moe": (
        "Ornith-1.5",
        "Qwen3.6-35B-A3B",
        "Qwen3.8-Flash-Next",
        "gemma-4-26b",
    ),
}


def fits(size_gb: float | None, profile: str, *, offload: bool = False) -> bool | None:
    """Whether weights of `size_gb` fit. None when the size is unknown.

    Resident means the weights sit in VRAM. `offload` allows an MoE to stream
    experts from host RAM, which is the only way a 12 GiB card runs a 22 GB
    model -- and it is a real path, not a fudge: it is what #20 exists to test.

    Unknown is returned rather than guessed. A model whose size we cannot read
    is a question for `--sizes`, not a silent exclusion.
    """
    if size_gb is None:
        return None
    prof = PROFILES[profile]
    budget = prof["vram_gb"]
    if offload and not prof["unified"]:
        # Discrete card: experts stream from host RAM, but the non-routed
        # weights and the KV cache still have to be resident. Leave headroom.
        budget = prof["vram_gb"] + prof["ram_gb"] * 0.8
    return size_gb <= budget


def repo_size_gb(repo: str) -> float | None:
    """Total bytes of the largest single variant in a repo, in GB.

    A quant repo usually holds several variants in subdirectories; summing them
    all would report a repo far larger than anything you would actually load.
    Group by top-level directory and take the largest group, which is the
    biggest single thing a person might download.
    """
    query = f"{API}/{repo}/tree/main?recursive=true"
    req = urllib.request.Request(query, headers={"User-Agent": "curl/8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as fh:
            tree = json.loads(fh.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    groups: dict[str, int] = {}
    for entry in tree:
        path = entry.get("path", "")
        if not path.endswith((".gguf", ".safetensors")):
            continue
        head = path.split("/")[0] if "/" in path else ""
        groups[head] = groups.get(head, 0) + (entry.get("size") or 0)
    return round(max(groups.values()) / 1e9, 1) if groups else None


def search(term: str, limit: int = 30) -> list[dict]:
    query = urllib.parse.urlencode(
        {"search": term, "sort": "lastModified", "direction": -1, "limit": limit}
    )
    req = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": "curl/8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as fh:
            return json.loads(fh.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.debug("hf search %s failed: %s", term, exc)
        return []


def params_b(entry: dict) -> float | None:
    total = (entry.get("safetensors") or {}).get("total")
    return round(total / 1e9, 1) if total else None


def _recommend(terms: tuple[str, ...], args) -> int:
    """Rank candidates for a machine: loadable first, then by downloads.

    Downloads are the tie-breaker rather than recency, because a fresh upload
    with no pulls has been exercised by nobody. A build with thousands has.
    """
    seen: set[str] = set()
    rows: list[tuple] = []
    hidden = 0
    for term in terms:
        for entry in search(term, args.limit):
            repo = entry["id"]
            if repo in seen:
                continue
            seen.add(repo)
            kind = classify(repo, args.profile)
            if kind == "unusable" and not args.all:
                hidden += 1
                continue
            size = repo_size_gb(repo) if args.sizes else None
            ok = fits(size, args.profile, offload=args.offload)
            if ok is False and not args.all:
                hidden += 1
                continue
            rows.append((entry.get("downloads", 0), repo, kind, size, ok))

    if not rows:
        logger.info("nothing loadable found; try --all to see what was hidden")
        return 1
    rows.sort(key=lambda r: -r[0])
    logger.info("%-58s %8s %7s  %s", "repo", "downloads", "size", "fit")
    for downloads, repo, kind, size, ok in rows[:25]:
        mark = {"usable": " ", "unknown": "?", "unusable": "x"}[kind]
        fit = "-" if ok is None else ("yes" if ok else "NO")
        logger.info(
            "%s %-58s %8d %7s  %s",
            mark,
            repo[:58],
            downloads,
            f"{size}GB" if size else "?",
            fit,
        )
    if hidden and not args.all:
        logger.info(
            "\n%d hidden: wrong format for this machine, or too large. "
            "--all to see them.",
            hidden,
        )
    if not args.sizes:
        logger.info("\nSizes not fetched. Re-run with --sizes to check fit.")
    logger.info("\nLegend:  (blank) loads here   ? unclassified, check it")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--all", action="store_true", help="include unusable formats")
    p.add_argument("--limit", type=int, default=30, help="results per family")
    p.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="m5-max",
        help="which machine to judge loadability and fit against. The format "
        "lists invert between machines: EXL2/AWQ/GPTQ are unusable on Metal "
        "and fine on CUDA; MLX and mxfp8 are the reverse.",
    )
    p.add_argument(
        "--find",
        metavar="QUERY",
        help="search a term instead of sweeping watched families. Use a canned "
        f"set by name ({', '.join(sorted(CANNED))}) or any free text.",
    )
    p.add_argument(
        "--sizes",
        action="store_true",
        help="fetch each repo's file tree to report real sizes and whether it "
        "fits. One extra request per repo, so it is off by default.",
    )
    p.add_argument(
        "--offload",
        action="store_true",
        help="judge fit assuming MoE experts stream from host RAM (#20)",
    )
    args = p.parse_args()
    provenance.configure()
    log_file = provenance.tee("hf-sweep", machine_specific=False)
    provenance.banner(logger, engines=False)

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=args.hours)
    if not args.find:
        # --find searches the whole index; only the watch mode has a window,
        # and printing one for a search would misdescribe the results.
        logger.info(
            "Hugging Face sweep since %s (%.0fh)\n",
            cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            args.hours,
        )

    if args.find:
        terms = CANNED.get(args.find, (args.find,))
        prof = PROFILES[args.profile]
        logger.info(
            "Searching for: %s\n  against %s\n  budget %.0f GiB VRAM%s\n",
            ", ".join(terms),
            prof["description"],
            prof["vram_gb"],
            f" + {prof['ram_gb']:.0f} GB host RAM (offload)"
            if args.offload and not prof["unified"]
            else "",
        )
        return _recommend(terms, args)

    hidden = 0
    for family, why in WATCHED.items():
        fresh = []
        for entry in search(family, args.limit):
            stamp = (entry.get("lastModified") or "")[:19]
            if not stamp:
                continue
            try:
                when = dt.datetime.fromisoformat(stamp).replace(tzinfo=dt.UTC)
            except ValueError:
                continue
            if when < cutoff:
                continue
            kind = classify(entry["id"])
            if kind == "unusable" and not args.all:
                hidden += 1
                continue
            fresh.append(
                (entry["id"], stamp, kind, params_b(entry), entry.get("downloads", 0))
            )
        if not fresh:
            continue
        logger.info("== %s -- %s", family, why)
        for repo, stamp, kind, size, downloads in fresh:
            mark = {"usable": " ", "unknown": "?", "unusable": "x"}[kind]
            logger.info(
                "  %s %-58s %s  %s  dl=%d",
                mark,
                repo[:58],
                stamp[:16],
                f"{size}B" if size else "     ",
                downloads,
            )
        logger.info("")

    if hidden and not args.all:
        # Never a silent filter. Most new quants target CUDA or ROCm, and a
        # count is the difference between "nothing shipped" and "nothing that
        # runs here shipped" -- which are very different facts.
        logger.info(
            "%d result(s) hidden as unloadable on Metal (NVFP4/FP8/ROCm/AWQ/"
            "GPTQ/EXL). Re-run with --all to see them.",
            hidden,
        )
    logger.info("\nLegend:  (blank) loads here   ? unclassified, check it   x hidden")
    logger.info("log: %s", log_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
