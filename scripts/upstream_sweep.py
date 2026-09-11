"""Sweep the repositories this project depends on, in one command.

The 2026-09-01 sweep was done by hand: a dozen `gh api` calls, a list of repos
reconstructed from memory, and two of them missed because SOURCES.md linked the
author's profile rather than the repo. It found the thing that mattered --
`qwen4exp` is Qwen3.8-Flash-Next, so llama.cpp commits under that name are work
on our own fast pick -- which is an argument for doing it regularly, not for
doing it from memory.

WATCHED is the single source of truth for the repositories, tagged by machine
(#307): the Mac's Metal/MLX engines and the DGX Spark's CUDA serving stack are
different lists, so `--platform` picks a lane. SOURCES.md is the companion list
of who to read on X.

    uv run python scripts/upstream_sweep.py --hours 24              # all repos
    uv run python scripts/upstream_sweep.py --hours 24 --platform dgx
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pathlib
import subprocess
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import provenance

logger = logging.getLogger(__name__)

# Platform tags. The engines diverge between the two machines (#307): the Mac
# runs Metal and MLX, the DGX Spark (GB10, CUDA) runs the NVFP4/FP8 serving
# stack. A repo that belongs to no machine -- repo hygiene, this project --
# carries BOTH so no lane loses it.
MAC = "mac"
DGX = "dgx"
BOTH = (MAC, DGX)

# repo -> (why, platforms). The reason is the useful half: a sweep that lists
# activity without saying why it matters is a second inbox. `platforms` says
# which machine's lane includes the repo, so `--platform dgx` does not walk 12
# MLX repos that cannot run on it, and `--platform mac` does not walk vLLM.
WATCHED: dict[str, tuple[str, tuple[str, ...]]] = {
    "antirez/ds4": (
        "our primary engine; the only one that runs DeepSeek-V4-Flash and GLM-5.3",
        BOTH,  # built `make cuda-spark` on the DGX, Metal on the Mac
    ),
    "ggml-org/llama.cpp": (
        "our fast pick's engine; `qwen4exp` IS Qwen3.8-Flash-Next",
        BOTH,  # CUDA build on the DGX, Metal on the Mac
    ),
    "ollama/ollama": (
        "the 31 GB entry point; MLX runtime on the Mac, cuda_v13 on the DGX",
        BOTH,
    ),
    "anomalyco/opencode": ("our only client", BOTH),
    "evanwtf/local-llm": ("this project", BOTH),
    "evanwtf/gmail-archive": ("the excision tasks' target repository", BOTH),
    "evanwtf/ds4": ("our ds4 fork (#27 asks whether it can be retired)", BOTH),
    # Mac-only: Metal and MLX have no meaning on the DGX.
    "ml-explore/mlx": ("the framework everything MLX sits on", (MAC,)),
    "ml-explore/mlx-lm": (
        "reference MLX server; new architectures land here first",
        (MAC,),
    ),
    "jundot/omlx": ("oMLX -- prefill leader, untested here (#60)", (MAC,)),
    "ddalcu/mlx-serve": ("benchmarked on our exact machine; llmprobe's author", (MAC,)),
    "youssofal/MTPLX": (
        "MTP speculative decoding; we hold one unreplicated number",
        (MAC,),
    ),
    "raullenchai/Rapid-MLX": ("the one MLX engine reachable by pip (#57, #60)", (MAC,)),
    "ARahim3/mlx-dspark": ("DSpark/DFlash ported to MLX (#19, #58, #75)", (MAC,)),
    "Blaizzy/mlx-vlm": (
        "expert offloading, prefix caching, Qwen3.8-Flash-Next MTP",
        (MAC,),
    ),
    "unslothai/llama.cpp": ("the fork with a working qwen4exp MTP graph (#77)", (MAC,)),
    "Layr-Labs/mlxfast-gemma4-26b-a4b-engine": (
        "MLX Fast leaderboard harness (#80)",
        (MAC,),
    ),
    "sudoingX/qwen38-mtp": (
        "61 paired baseline-vs-MTP runs, disciplined method (#19, #39)",
        (MAC,),
    ),
    "trymirai/uzu": (
        "Apple-only Rust engine, reachable by pip; claims 2x MTPLX (#134)",
        (MAC,),
    ),
    # DGX-only: the CUDA-native serving stack that reaches NVFP4/FP8 on Blackwell,
    # which Ollama on Linux cannot (#293). The DGX's emphasis is aggregate
    # multi-stream throughput, so these matter more here than a single-stream Mac.
    "vllm-project/vllm": (
        "NVFP4/FP8 serving on Blackwell; the #299 single-Spark recipe",
        (DGX,),
    ),
    "NVIDIA/TensorRT-LLM": (
        "the other CUDA-native path to NVFP4 on the Spark (#293, #299)",
        (DGX,),
    ),
    "sgl-project/sglang": (
        "aggregate multi-stream serving; the DGX's throughput emphasis (#307)",
        (DGX,),
    ),
}


def watched_for(platform: str) -> dict[str, str]:
    """The `repo -> why` map for `platform` ('mac', 'dgx' or 'all')."""
    return {
        repo: why
        for repo, (why, platforms) in WATCHED.items()
        if platform == "all" or platform in platforms
    }


def gh(path: str) -> list | dict | None:
    try:
        r = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("gh api %s failed: %s", path, exc)
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


#: How many open PRs to name per repo before collapsing to a count. llama.cpp
#: alone can touch dozens in a day; a sweep nobody can read is a second inbox.
PR_LIST_CAP = 8


def open_pulls(repo: str, since: str) -> list[str]:
    """Open PRs updated inside the window, newest first.

    **Commits and releases cannot see this.** A fix can sit in an open PR from
    a fork for days without touching main or cutting a release, and that is
    exactly where the interesting ones live: `ddalcu/mlx-serve#383` fixed a
    speculative-decoding bug that drops the prefix cache -- on our exact model,
    on our exact machine -- while #191 spent three and a half hours
    benchmarking the five-day-old release that carried the bug. The repo was
    already watched. Only its main branch was.

    PRs, not branches, because a fork PR's branch lives in the fork: listing
    branches on the upstream repo would not have shown #383 either. ds4's own
    preview branches stay preflight's job (#38).
    """
    pulls = gh(f"repos/{repo}/pulls?state=open&sort=updated&direction=desc&per_page=50")
    if not isinstance(pulls, list):
        return []
    out = []
    for pr in pulls:
        if not isinstance(pr, dict):
            continue
        if (pr.get("updated_at") or "") <= since:
            # sorted by updated desc, so the first stale one ends the window
            break
        out.append(f"#{pr.get('number')} {(pr.get('title') or '')[:78]}")
    return out


def sweep(repo: str, since: str) -> dict:
    commits = gh(f"repos/{repo}/commits?since={since}&per_page=100")
    releases = gh(f"repos/{repo}/releases?per_page=10") or []
    subjects = [
        c["commit"]["message"].splitlines()[0]
        for c in (commits or [])
        if isinstance(c, dict)
    ]
    tags = [
        r["tag_name"]
        for r in releases
        if isinstance(r, dict) and (r.get("published_at") or "") > since
    ]
    return {
        "commits": subjects,
        "releases": tags,
        "pulls": open_pulls(repo, since),
        "reachable": commits is not None,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hours", type=float, default=24.0)
    p.add_argument("--quiet-empty", action="store_true", help="hide idle repos")
    p.add_argument(
        "--platform",
        choices=("mac", "dgx", "all"),
        default="all",
        help="which machine's engines to sweep. 'mac' is Metal/MLX, 'dgx' is "
        "the GB10 CUDA serving stack (vLLM, TensorRT-LLM); 'all' is both (#307).",
    )
    args = p.parse_args()

    provenance.configure()
    log_file = provenance.tee("upstream-sweep", machine_specific=False)
    provenance.banner(logger, engines=False)
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=args.hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    watched = watched_for(args.platform)
    logger.info(
        "Upstream sweep since %s (%.0fh) -- %s: %d repos\n",
        since,
        args.hours,
        args.platform,
        len(watched),
    )

    unreachable = []
    for repo, why in watched.items():
        got = sweep(repo, since)
        if not got["reachable"]:
            unreachable.append(repo)
            continue
        if (
            args.quiet_empty
            and not got["commits"]
            and not got["releases"]
            and not got["pulls"]
        ):
            continue
        logger.info("== %s -- %s", repo, why)
        if got["releases"]:
            logger.info("   releases: %s", ", ".join(got["releases"]))
        for subject in got["commits"][:12]:
            logger.info("   %s", subject)
        if len(got["commits"]) > 12:
            logger.info("   ... and %d more", len(got["commits"]) - 12)
        for pr in got["pulls"][:PR_LIST_CAP]:
            logger.info("   PR %s", pr)
        if len(got["pulls"]) > PR_LIST_CAP:
            logger.info("   ... and %d more open PRs", len(got["pulls"]) - PR_LIST_CAP)
        if not got["commits"] and not got["releases"] and not got["pulls"]:
            logger.info("   (quiet)")
        logger.info("")

    if unreachable:
        # Never silent: a repo that cannot be read looks identical to a quiet
        # one, and "nothing happened upstream" is exactly the wrong conclusion
        # to draw from a rename or an auth failure.
        logger.warning(
            "UNREACHABLE (renamed, private, or gh not authed): %s",
            ", ".join(unreachable),
        )
    logger.info("log: %s", log_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
