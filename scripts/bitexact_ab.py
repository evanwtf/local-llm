"""Bit-exact A/B for two ds4 engine trees: the output-equality check #143 lacked.

ds4 PR #964 claims its new Metal kernels are bit-exact against the old ones.
#143 re-tested speed only (+18.7% decode, -1.0% prefill, four runs per arm)
and had to report "bit-exactness was not checked", because nothing existed to
check it with. This script is that check. It runs the same raw prompt through
two engine trees at temperature 0, at each frontier (default 2048 and 16384),
and compares the generated token sequences and the top-k logits behind them.

What ds4 already provides, checked in ds4_cli.c before building anything:

  --dump-logprobs FILE   writes, per generated step, the argmax-selected
      token plus the top-k {token, logit, logprob} rows as JSON. That loop
      (run_dump_logprobs) is top_logprobs -> argmax -> eval: no sampler, no
      seed, no speculative path, so at temperature 0 the dump is a
      deterministic function of the prompt tokens, the weights, the kernel
      code and ctx. Comparing two trees' dumps IS the bit-exactness question.
  --dump-tokens --raw    prints the exact token stream of a raw prompt (ids
      and text pieces) and exits, loading only model metadata and vocab --
      no weights, no GPU. The instrument uses it to cut the corpus at a true
      token boundary per frontier, and to verify the cut re-tokenizes to the
      same ids.
  bench's --show-output and --dump-frontier-logits-dir were not usable: one
  prints prose, the other dumps prefill logits but never the generated
  sequence, and neither diffs mechanically across two trees.

Honest limits, restated on every report:

  * Both ds4 dump writers print floats with %.9g. "identical" therefore means
    identical at 9-significant-digit print resolution; a difference confined
    to the last ulp prints identically. This instrument can confirm the claim
    to print resolution and refute it at print resolution; it cannot prove
    ulp-level bit-exactness.
  * the CLI reports no sampling parameters in any output. The instrument
    records the full argv and the environment pins it ran with, and marks the
    comparison conditional on that record. (As of the dump path above, the
    comparison does not depend on sampling at all; the record exists for the
    day upstream changes that.)
  * Temperature is pinned at 0 -- a sampled comparison says nothing about
    bit-exactness. --seed 0 is refused: ds4 treats seed 0 as unset and seeds
    from time/pid/clock instead (run_sampled_generation). The dump path never
    samples, but the seed is pinned and recorded anyway.
  * The Metal 4 TensorOps route is pinned ON for both arms:
    DS4_METAL_ENABLE_TENSOR=1 opts any Metal 4 GPU into the route
    (ds4_metal.m:2626), and DS4_METAL_DISABLE_METAL4=0 clears an inherited
    operator disable, which would outrank the opt-in (ds4_metal.m:2586).
    Without a pin, two trees can sit on different routes for reasons that are
    not the change under test: the automatic enable is derived from the device
    generation (ds4_metal.m:2605), and fix/m5-tensor-drift (a11bf74) withholds
    it -- so a "diverged at step N" finding would name the PR when the route
    is the cause. Pinning does NOT make the self-check catch a route problem:
    both self-check arms run the same tree on the same pinned route, so they
    agree even when that route is not what the PR's reference kernels
    describe. Route-vs-reference drift is the equivalence gate's question
    (benchmarks/agent/preflight.py metal_tensor_gate, i.e. ds4_test
    --metal-tensor-equivalence), not this instrument's.

Sequence: the SAME tree runs twice first. If tree A disagrees with itself
under identical arguments, temperature 0 is not deterministic on this Metal
stack and the instrument cannot answer at all. It refuses loudly and never
runs tree B, rather than reporting B as non-exact -- the same collapse
#26 warned about, where ordinary sampling spread was blamed on the KV cache.

An arm that fails to run (nonzero exit, missing or corrupt dump) is reported
as "arm failed", never as "not bit-exact" -- the same rule as
source_repo_intact: a broken instrument collapses every distinction.

    uv run python scripts/bitexact_ab.py new ~/git/ds4-main old ~/git/ds4-old \
        ~/models/qwen3-8f-ds4.gguf --frontier 2048 --frontier 16384
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_FRONTIERS = (2048, 16384)
DEFAULT_GEN = 128
DEFAULT_SEED = 1
DEFAULT_TIMEOUT = 1800
#: Generation headroom over the frontier prompt. ds4 refuses a prompt at or
#: over ctx ("one token of generation room is required"), so ctx must clear
#: the prompt by more than the generation budget.
MARGIN = 64
TOP_K = 20  # the CLI's own default for --dump-logprobs; pinned, not inherited.

# The binary built from ds4_cli.c is named `ds4` (Makefile:90 -- `ds4:
# ds4_cli.o ...`), not `ds4-cli`. There is no ds4-cli target and `make
# ds4-cli` fails outright. Naming it from the source file looked right and
# refused every real tree.
CLI_NAME = "ds4"


class InstrumentRefused(Exception):
    """The instrument cannot answer; say why and stop. Never a finding."""


class ArmFailed(Exception):
    """One arm failed to run. Not 'not bit-exact' -- a broken arm answers nothing."""


# --- parsing ds4's own outputs ---------------------------------------------


def parse_token_dump(text: str) -> tuple[list[int], list[str]]:
    """Parse --dump-tokens output into (ids, pieces).

    ds4 prints the ids as a JSON-ish array, then one line per token:
    right-aligned id, two spaces, the token's text piece. A piece may start
    with a space (BPE style) or be empty; both survive the regex below. A
    piece containing a bare newline does not -- the verify step refuses
    rather than cut the corpus at a wrong boundary.
    """
    lines = text.splitlines()
    if not lines or not lines[0].startswith("["):
        raise InstrumentRefused(
            f"--dump-tokens output did not start with an id array: {text[:120]!r}"
        )
    ids = [int(x) for x in lines[0].strip().strip("[]").split(",") if x.strip()]
    pieces: dict[int, str] = {}
    for line in lines[1:]:
        m = re.match(r"^ {0,5}(\d+)  (.*)$", line)
        if m:
            pieces[int(m.group(1))] = m.group(2)
    missing = [i for i in ids if i not in pieces]
    if missing:
        raise InstrumentRefused(
            f"--dump-tokens table is missing pieces for ids {missing[:5]} "
            "(a token piece containing a bare newline breaks the table; "
            "cut this corpus with --prompt-file instead)"
        )
    return ids, [pieces[i] for i in ids]


def parse_dump(path: pathlib.Path) -> dict:
    """Parse a --dump-logprobs JSON file; tolerate a crash-truncated tail.

    The writer fprintf()s each step as it goes and only fclose()s at the end,
    so a decode that dies mid-run leaves the early steps intact. A truncated
    file is flagged; the caller treats it as an arm failure.
    """
    text = pathlib.Path(path).read_text(errors="replace")
    try:
        doc = json.loads(text)
        return {
            "prompt_tokens": doc["prompt_tokens"],
            "ctx": doc["ctx"],
            "steps": doc["steps"],
            "truncated": False,
        }
    except (ValueError, KeyError, TypeError):
        pass
    steps = []
    step_re = re.compile(
        r'\{"step":(\d+),"selected":("(?:[^"\\]|\\.)*"),"top_logprobs":\[(.*?)\]\}'
    )
    for m in step_re.finditer(text):
        try:
            steps.append(
                {
                    "step": int(m.group(1)),
                    "selected": json.loads(m.group(2)),
                    "top_logprobs": json.loads(f"[{m.group(3)}]"),
                }
            )
        except ValueError:
            continue
    pt = re.search(r'"prompt_tokens":(\d+)', text)
    ctx = re.search(r'"ctx":(\d+)', text)
    return {
        "prompt_tokens": int(pt.group(1)) if pt else None,
        "ctx": int(ctx.group(1)) if ctx else None,
        "steps": steps,
        "truncated": True,
    }


def first_divergence(a: dict, b: dict) -> tuple[str, object, str] | None:
    """First point where two dumps disagree, as (kind, step, detail).

    kinds: prompt_tokens (the trees tokenized the same bytes differently),
    selected (the generated token differs), logprobs (tokens still match but
    the top-k logits differ -- a real numeric divergence the text would
    hide), length (one arm stopped early).
    """
    if a["prompt_tokens"] != b["prompt_tokens"]:
        return (
            "prompt_tokens",
            "prefill",
            (
                f"the same prompt file tokenized to {a['prompt_tokens']} vs "
                f"{b['prompt_tokens']} tokens -- the trees' tokenizers disagree"
            ),
        )
    if a["ctx"] != b["ctx"]:
        return ("ctx", "prefill", f"ctx recorded as {a['ctx']} vs {b['ctx']}")
    sa, sb = a["steps"], b["steps"]
    n = min(len(sa), len(sb))
    for i in range(n):
        if sa[i]["selected"] != sb[i]["selected"]:
            return (
                "selected",
                i,
                f"arm A selected {sa[i]['selected']!r}, arm B {sb[i]['selected']!r}",
            )
        if sa[i]["top_logprobs"] != sb[i]["top_logprobs"]:
            return (
                "logprobs",
                i,
                "selected tokens still match; the top-k logits/logprobs differ",
            )
    if len(sa) != len(sb):
        return (
            "length",
            n,
            f"arm A produced {len(sa)} steps, arm B {len(sb)}; first {n} steps identical",
        )
    return None


# --- driving ds4-cli --------------------------------------------------------


def cli_argv(
    tree: pathlib.Path,
    gguf: str,
    prompt: pathlib.Path,
    ctx: int,
    gen: int,
    seed: int,
    dump_path: pathlib.Path,
    backend_flag: str,
) -> list[str]:
    return [
        str(tree / CLI_NAME),
        "-m",
        gguf,
        backend_flag,
        "--raw",
        "--prompt-file",
        str(prompt),
        "--ctx",
        str(ctx),
        "--tokens",
        str(gen),
        "--temp",
        "0",
        "--seed",
        str(seed),
        "--logprobs-top-k",
        str(TOP_K),
        "--dump-logprobs",
        str(dump_path),
    ]


def tokenize_argv(tree: pathlib.Path, gguf: str, prompt: pathlib.Path) -> list[str]:
    # --dump-tokens dispatches before engine creation and loads only model
    # metadata and vocab (ds4_dump_text_tokenization), so this is cheap and
    # loads no weights.
    return [
        str(tree / CLI_NAME),
        "-m",
        gguf,
        "--raw",
        "--prompt-file",
        str(prompt),
        "--dump-tokens",
    ]


def run_capture(
    argv: list[str], env: dict, timeout: int
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )


def run_arm(argv: list[str], env: dict, timeout: int, dump_path: pathlib.Path) -> dict:
    """Run one arm and return its parsed dump. Any failure raises ArmFailed."""
    try:
        proc = run_capture(argv, env, timeout)
    except subprocess.TimeoutExpired:
        raise ArmFailed(f"timed out after {timeout}s") from None
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["(no stderr)"]
        raise ArmFailed(f"rc={proc.returncode}: {tail[0]}")
    if not dump_path.exists():
        raise ArmFailed("the arm exited 0 but wrote no dump file")
    dump = parse_dump(dump_path)
    if dump["truncated"] or dump["steps"] is None:
        raise ArmFailed(
            "the dump file is corrupt or truncated; the arm did not finish cleanly"
        )
    return dump


# --- frontier prompts -------------------------------------------------------


def frontier_prompt(
    tree_a: pathlib.Path,
    gguf: str,
    corpus: pathlib.Path,
    frontier: int,
    out_dir: pathlib.Path,
    env: dict,
    timeout: int,
) -> tuple[pathlib.Path, int]:
    """Cut the corpus at a true token boundary for this frontier.

    Tokenize the whole corpus with tree A's vocab (metadata only), keep the
    first `frontier` pieces, and verify the cut file re-tokenizes to exactly
    those ids. A corpus that is not prefix-stable under its own tokenizer is
    refused, never mislabeled.
    """
    proc = run_capture(tokenize_argv(tree_a, gguf, corpus), env, timeout)
    if proc.returncode != 0:
        raise InstrumentRefused(f"tokenizing the corpus failed: {proc.stderr.strip()}")
    ids, pieces = parse_token_dump(proc.stdout)
    if len(ids) < frontier:
        raise InstrumentRefused(
            f"{corpus} tokenizes to {len(ids)} tokens; frontier {frontier} needs more. "
            "Supply a longer --corpus or a smaller --frontier."
        )
    prefix = "".join(pieces[:frontier])
    path = out_dir / f"prompt-{frontier}.txt"
    path.write_text(prefix)
    proc = run_capture(tokenize_argv(tree_a, gguf, path), env, timeout)
    if proc.returncode != 0:
        raise InstrumentRefused(
            f"verifying the frontier prompt failed: {proc.stderr.strip()}"
        )
    v_ids, _ = parse_token_dump(proc.stdout)
    if v_ids != ids[:frontier]:
        raise InstrumentRefused(
            f"the frontier-{frontier} prompt re-tokenizes to {len(v_ids)} tokens, "
            f"not {frontier} with the same ids -- the corpus is not prefix-stable "
            "at this cut. Supply an exact --prompt-file instead."
        )
    sha = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    logger.info(
        "frontier %d: prompt cut at a token boundary (%d tok, sha %s)",
        frontier,
        frontier,
        sha,
    )
    return path, frontier


# --- the instrument ---------------------------------------------------------


def tree_commit(tree: pathlib.Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(tree), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return proc.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def refuse_seed(seed: int) -> None:
    if seed == 0:
        raise InstrumentRefused(
            "--seed 0 is refused: ds4 treats seed 0 as unset and seeds from "
            "time/pid/clock instead (ds4_cli.c run_sampled_generation). "
            "Pass a nonzero seed."
        )


def require_binaries(trees: dict[str, pathlib.Path]) -> None:
    for label, tree in trees.items():
        cli = tree / CLI_NAME
        if not cli.is_file() or not os.access(cli, os.X_OK):
            raise InstrumentRefused(
                f"{cli} is missing or not executable -- build {label} first"
            )


def take_lock(label: str) -> None:
    preflight = REPO / "benchmarks" / "agent" / "preflight.py"
    proc = run_capture(
        [
            sys.executable,
            str(preflight),
            "--acquire-lock",
            label,
            "--owner-pid",
            str(os.getpid()),
        ],
        dict(os.environ),
        120,
    )
    if proc.returncode != 0:
        raise InstrumentRefused(
            "refusing to start: the machine is claimed by another run "
            f"({(proc.stdout or proc.stderr).strip()})"
        )


def release_lock() -> None:
    preflight = REPO / "benchmarks" / "agent" / "preflight.py"
    run_capture(
        [
            sys.executable,
            str(preflight),
            "--release-lock",
            "--owner-pid",
            str(os.getpid()),
        ],
        dict(os.environ),
        120,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("label_a")
    p.add_argument("tree_a", type=pathlib.Path)
    p.add_argument("label_b")
    p.add_argument("tree_b", type=pathlib.Path)
    p.add_argument("gguf")
    p.add_argument(
        "--frontier",
        type=int,
        action="append",
        default=None,
        metavar="N",
        help=f"context frontier to test; repeatable "
        f"(default: {' and '.join(str(f) for f in DEFAULT_FRONTIERS)})",
    )
    p.add_argument(
        "--corpus",
        type=pathlib.Path,
        default=None,
        help="prompt source, cut per frontier at a token boundary "
        "(default: tree A's speed-bench/promessi_sposi.txt, "
        "the decode_ab_engine.sh convention)",
    )
    p.add_argument(
        "--prompt-file",
        type=pathlib.Path,
        default=None,
        help="use this exact prompt instead of cutting the corpus; "
        "the frontier then only sets the ctx scale",
    )
    p.add_argument(
        "--gen",
        type=int,
        default=DEFAULT_GEN,
        help=f"generation budget per arm (default {DEFAULT_GEN})",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"sampling seed, recorded; must be nonzero (default {DEFAULT_SEED})",
    )
    p.add_argument(
        "--backend", choices=["metal", "cpu", "rocm", "cuda"], default="metal"
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"per-arm wall budget in seconds (default {DEFAULT_TIMEOUT})",
    )
    p.add_argument(
        "--out",
        type=pathlib.Path,
        default=None,
        help="report, dumps and prompt files "
        f"(default {pathlib.Path.home() / 'bench-logs' / 'bitexact-ab'})",
    )
    p.add_argument(
        "--no-lock",
        action="store_true",
        help="skip the preflight run lock (tests only)",
    )
    return p.parse_args(argv)


def frontiers_of(args: argparse.Namespace) -> list[int]:
    return list(args.frontier) if args.frontier else list(DEFAULT_FRONTIERS)


def run(args: argparse.Namespace) -> int:
    refuse_seed(args.seed)
    frontiers = frontiers_of(args)
    out_dir = args.out or pathlib.Path.home() / "bench-logs" / "bitexact-ab"
    out_dir.mkdir(parents=True, exist_ok=True)
    trees = {"A": args.tree_a, "B": args.tree_b}
    require_binaries(trees)
    if not pathlib.Path(args.gguf).exists():
        raise InstrumentRefused(f"{args.gguf} does not exist")
    if args.prompt_file and not args.prompt_file.exists():
        raise InstrumentRefused(f"{args.prompt_file} does not exist")
    corpus = args.corpus
    if corpus is None and args.prompt_file is None:
        corpus = args.tree_a / "speed-bench" / "promessi_sposi.txt"
    if corpus is not None and not corpus.exists():
        raise InstrumentRefused(f"corpus {corpus} does not exist -- pass --corpus")

    backend_flag = {
        "metal": "--metal",
        "cpu": "--cpu",
        "rocm": "--rocm",
        "cuda": "--cuda",
    }[args.backend]
    env = dict(os.environ)
    # The dump-logprobs loop never speculatively decodes, but pin the env
    # anyway and record it: if a future ds4 adds an MTP path there, the pin
    # already held when this comparison was made.
    env["DS4_MTP_SPEC_DISABLE"] = "1"
    # #149: pin the TensorOps route ON for both arms. Without a pin the two
    # trees can sit on different routes for reasons that are not the change
    # under test (the automatic enable is derived from the device generation,
    # ds4_metal.m:2605, and fix/m5-tensor-drift withholds it). The disable
    # override is not decoration: a disable in the inherited environment
    # outranks the opt-in (ds4_metal.m:2586), so the opt-in alone would not
    # pin anything. See "Honest limits" at the top of this file for what the
    # self-check does and does not cover.
    env["DS4_METAL_ENABLE_TENSOR"] = "1"
    env["DS4_METAL_DISABLE_METAL4"] = "0"

    report = {
        "instrument": "scripts/bitexact_ab.py",
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "a": {
            "label": args.label_a,
            "tree": str(args.tree_a),
            "commit": tree_commit(args.tree_a),
        },
        "b": {
            "label": args.label_b,
            "tree": str(args.tree_b),
            "commit": tree_commit(args.tree_b),
        },
        "gguf": args.gguf,
        "corpus": str(corpus) if corpus else None,
        "prompt_file_override": str(args.prompt_file) if args.prompt_file else None,
        "frontiers": frontiers,
        "gen": args.gen,
        "backend": args.backend,
        "resolution": "%.9g (9 significant digits; both ds4 dump writers)",
        "sampling_record": {
            "requested": {
                "temperature": 0,
                "seed": args.seed,
                "top_k": TOP_K,
                "raw_prompt": True,
            },
            "engine_reports_sampling": False,
            "note": "the CLI prints timings only; the --dump-logprobs JSON "
            "records prompt_tokens, ctx and top_k but none of the "
            "sampling parameters it used. The comparison is "
            "conditional on the recorded argv and env. The dump "
            "path itself is an eval->argmax loop and does not "
            "consult the sampler, so temperature and seed cannot "
            "change what it compares.",
        },
        "env_pins": {
            "DS4_MTP_SPEC_DISABLE": env["DS4_MTP_SPEC_DISABLE"],
            "DS4_METAL_ENABLE_TENSOR": env["DS4_METAL_ENABLE_TENSOR"],
            "DS4_METAL_DISABLE_METAL4": env["DS4_METAL_DISABLE_METAL4"],
        },
        "frontier_results": [],
    }

    if not args.no_lock:
        take_lock(f"bitexact_ab.py {args.label_a} vs {args.label_b}")
    try:
        for frontier in frontiers:
            result = run_frontier(
                args, trees, corpus, backend_flag, env, frontier, out_dir
            )
            report["frontier_results"].append(result)
            report["verdict"] = verdict(report)
            (out_dir / "report.json").write_text(
                json.dumps(report, indent=1, default=str)
            )
            if result["outcome"] in ("arm_failed", "self_check_failed"):
                # A broken arm stays broken; burning the next frontier's
                # GPU-minutes on it measures nothing new.
                logger.error(
                    "stopping after frontier %d: %s", frontier, result["detail"]
                )
                break
    finally:
        if not args.no_lock:
            release_lock()

    for line in report_lines(report):
        logger.info("%s", line)
    logger.info("report: %s", out_dir / "report.json")
    return (
        0
        if all(
            r.get("outcome") in ("identical", "diverged")
            for r in report["frontier_results"]
        )
        else 2
    )


def run_frontier(
    args: argparse.Namespace,
    trees: dict[str, pathlib.Path],
    corpus: pathlib.Path | None,
    backend_flag: str,
    env: dict,
    frontier: int,
    out_dir: pathlib.Path,
) -> dict:
    """One frontier: prompt, same-tree self-check, then the A/B."""
    ctx = frontier + args.gen + MARGIN
    result: dict = {
        "frontier": frontier,
        "ctx": ctx,
        "gen": args.gen,
        "seed": args.seed,
    }
    if args.prompt_file:
        prompt = args.prompt_file
        proc = run_capture(
            tokenize_argv(trees["A"], args.gguf, prompt), env, args.timeout
        )
        if proc.returncode != 0:
            raise InstrumentRefused(
                f"tokenizing {prompt} with tree A failed: {proc.stderr.strip()}"
            )
        ids, _ = parse_token_dump(proc.stdout)
        ptokens = len(ids)
        if ptokens >= ctx:
            raise InstrumentRefused(
                f"{prompt} tokenizes to {ptokens} tokens; ctx {ctx} would refuse it "
                "(ds4 requires one token of generation room)"
            )
    else:
        assert corpus is not None
        prompt, ptokens = frontier_prompt(
            trees["A"], args.gguf, corpus, frontier, out_dir, env, args.timeout
        )
    expected_steps = max(0, min(args.gen, ctx - ptokens - 1))
    result["prompt_tokens"] = ptokens
    result["steps_expected"] = expected_steps

    dumps: dict[str, dict] = {}

    def arm_argv(run_name: str) -> list[str]:
        """argv for one run: a1/a2 drive tree A, b drives tree B. The dump
        path in the argv must be the exact path run_arm verifies."""
        arm = "B" if run_name == "b" else "A"
        return cli_argv(
            trees[arm],
            args.gguf,
            prompt,
            ctx,
            args.gen,
            args.seed,
            out_dir / f"{run_name}-{frontier}.json",
            backend_flag,
        )

    result["argv"] = {"A": arm_argv("a1"), "B": arm_argv("b")}

    # The same tree twice, identical arguments. A disagreement here means the
    # machine cannot answer the question; refuse before spending a run on B.
    try:
        for name in ("a1", "a2"):
            dumps[name] = run_arm(
                arm_argv(name), env, args.timeout, out_dir / f"{name}-{frontier}.json"
            )
    except ArmFailed as e:
        result["outcome"] = "arm_failed"
        result["arm"] = "A"
        result["detail"] = (
            f"tree A ({args.label_a}) failed during its self-check run: {e}. "
            "A failed arm is not 'not bit-exact'; it is no answer."
        )
        return result
    self_check = first_divergence(dumps["a1"], dumps["a2"])
    result["self_check"] = {
        "identical": self_check is None,
        "steps": len(dumps["a1"]["steps"]),
    }
    if self_check is not None:
        result["outcome"] = "self_check_failed"
        result["detail"] = (
            f"tree A ({args.label_a}) disagrees with itself under identical "
            f"arguments: {self_check[2]} (kind {self_check[0]}, step {self_check[1]}). "
            "Temperature 0 is not deterministic on this stack; the instrument "
            "cannot answer and tree B was not run."
        )
        return result

    try:
        dumps["b"] = run_arm(
            arm_argv("b"), env, args.timeout, out_dir / f"b-{frontier}.json"
        )
    except ArmFailed as e:
        result["outcome"] = "arm_failed"
        result["arm"] = "B"
        result["detail"] = (
            f"arm B ({args.label_b}) failed to run: {e}. "
            "A failed arm is not 'not bit-exact'; it is no answer."
        )
        return result

    div = first_divergence(dumps["a1"], dumps["b"])
    n = max(len(dumps["a1"]["steps"]), len(dumps["b"]["steps"]))
    if div is None:
        result["outcome"] = "identical"
        result["steps"] = n
        result["detail"] = (
            f"identical for all {n} steps, selected tokens and top-{TOP_K} logits both"
        )
    else:
        result["outcome"] = "diverged"
        result["steps"] = n
        result["first_divergence"] = {"step": div[1], "kind": div[0], "detail": div[2]}
        result["detail"] = f"diverged at step {div[1]} of {n} ({div[0]}): {div[2]}"
    return result


def verdict(report: dict) -> str:
    results = report["frontier_results"]
    bad = [r for r in results if r["outcome"] == "self_check_failed"]
    failed = [r for r in results if r["outcome"] == "arm_failed"]
    diverged = [r for r in results if r["outcome"] == "diverged"]
    identical = [r for r in results if r["outcome"] == "identical"]
    if bad:
        return (
            "REFUSED: the same tree disagreed with itself; the instrument "
            "cannot answer any frontier"
        )
    if failed:
        return (
            f"NO ANSWER: {len(failed)}/{len(results)} frontier(s) lost an arm "
            "(a failed arm is not 'not bit-exact'); fix and re-run"
        )
    if diverged:
        d = diverged[0]
        return (
            f"DIVERGED: frontier {d['frontier']} {d['detail']} -- the "
            "bit-exact claim does not hold at print resolution"
        )
    return (
        f"IDENTICAL at print resolution ({report['resolution']}) for "
        f"{len(identical)}/{len(results)} frontier(s) -- consistent with "
        "the bit-exact claim. A difference below print resolution would "
        "print identically; this instrument cannot see one."
    )


def report_lines(report: dict) -> list[str]:
    lines = [
        (
            f"bit-exact A/B: {report['a']['label']} @ {report['a']['commit'][:12]} "
            f"vs {report['b']['label']} @ {report['b']['commit'][:12]}"
        ),
        (
            f"gguf {report['gguf']}  backend {report['backend']}  gen {report['gen']}  "
            f"seed {report['frontier_results'][0]['seed'] if report['frontier_results'] else '-'}"
        ),
        (
            f"pin: DS4_MTP_SPEC_DISABLE={report['env_pins']['DS4_MTP_SPEC_DISABLE']}  "
            "tensor route: DS4_METAL_ENABLE_TENSOR="
            f"{report['env_pins']['DS4_METAL_ENABLE_TENSOR']}, "
            f"DS4_METAL_DISABLE_METAL4={report['env_pins']['DS4_METAL_DISABLE_METAL4']}  "
            "sampling: engine reports none; conditional on the recorded argv"
        ),
    ]
    for r in report["frontier_results"]:
        head = (
            f"frontier {r['frontier']}: prompt {r['prompt_tokens']} tok, "
            f"ctx {r['ctx']}, expected steps {r['steps_expected']}"
        )
        lines.append(head)
        if r.get("self_check"):
            lines.append(
                f"  self-check: tree A identical for {r['self_check']['steps']} steps"
            )
        if r["outcome"] == "identical":
            lines.append(f"  a vs b: IDENTICAL -- {r['detail']}")
        elif r["outcome"] == "diverged":
            lines.append(f"  a vs b: DIVERGED -- {r['detail']}")
        else:
            lines.append(f"  a vs b: {r['outcome'].upper()} -- {r['detail']}")
    lines.append(f"verdict: {report['verdict']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = parse_args(argv)
    try:
        return run(args)
    except InstrumentRefused as e:
        logger.error("REFUSING: %s", e)
        return 2
    except ArmFailed as e:
        logger.error("ARM FAILED: %s", e)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
