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

# Formats that cannot load on Metal. Checked first: a name can contain both
# "GGUF" and "NVFP4" and the second one decides it.
UNUSABLE = (
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
)
# Formats that do load here.
USABLE = ("gguf", "mlx", "mxfp8", "bf16", "q4_k", "q3_k", "q5_k", "q6_k", "q8_0", "iq")


def classify(repo_id: str) -> str:
    """'usable' | 'unusable' | 'unknown' for this machine, from the name.

    Name-based and therefore fallible -- but a repo id is what a sweep has, and
    the alternative is fetching a config for every hit. `unknown` is reported,
    not hidden: an unclassified repo is a question, and silence is how a real
    lead gets skipped.
    """
    lowered = repo_id.lower()
    # `mxfp8` is MLX's own 8-bit format and two of our backends use it
    # (qwen3.6:27b-coding-mxfp8, gemma4:31b-mxfp8). Bare `fp8` means NVIDIA.
    # Remove the specific before matching the general, or a substring hides
    # every new build of models we actually run -- the same mistake the ds4
    # probe made matching model ids by substring (#78).
    probe = lowered.replace("mxfp8", "")
    if any(bad in probe for bad in UNUSABLE):
        return "unusable"
    if any(good in lowered for good in USABLE):
        return "usable"
    return "unknown"


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


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--all", action="store_true", help="include unusable formats")
    p.add_argument("--limit", type=int, default=30, help="results per family")
    args = p.parse_args()
    provenance.configure()
    log_file = provenance.tee("hf-sweep", machine_specific=False)
    provenance.banner(logger, engines=False)

    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(hours=args.hours)
    logger.info(
        "Hugging Face sweep since %s (%.0fh)\n",
        cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        args.hours,
    )

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
