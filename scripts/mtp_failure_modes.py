"""Classify what the #39 MTP-arm trial deaths failed on (#39).

The #39 pass gap is 21/30 (MTP) vs 28/30 (control): 9 deaths vs 2. The peer
asked what the 9 deaths failed on -- wrong output, timeout, no tool call ever
emitted, or something else -- before reaching for a mechanism. This script
counts the failure modes from the opencode transcripts in the #39 evidence
tree, one row per death.

A death is a trial whose verdict is FAIL. The transcript is the opencode
stdout JSONL for that trial. The classifier reads the step_finish reason
sequence and the last assistant text:

  malformed-tool-call  the trial's last step is reason=unknown (the harness
                       could not parse the model's tool call) and the last
                       text carries a <tool_call> block. The model's final
                       action was an unparseable tool call, so no tool ran and
                       the trial stopped without a final answer.
  stopped-no-answer    the trial made real tool calls, then stopped with no
                       final answer text.
  wrong-output         the model produced a final answer but the tests failed.
  timeout              the trial hit a timeout.

The counts are re-derived from the transcripts, never inherited. The script
reads the bench-logs tree only; it needs no machine time.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import logging
import pathlib
import sys

logger = logging.getLogger(__name__)

# The death tasks, from the client logs' FAIL verdict lines. MTP arm is the
# qwen38fnds4mtp7shim backend; control is qwen38fnds4shim.
DEATHS: dict[str, list[tuple[str, str]]] = {
    "new-sweep1": [
        ("parser-mbox-quoting", "qwen38fnds4mtp7shim"),
        ("storage-blob-put", "qwen38fnds4mtp7shim"),
        ("mbox-scan", "qwen38fnds4mtp7shim"),
        ("swift-downsample-buckets", "qwen38fnds4mtp7shim"),
    ],
    "new-sweep2": [
        ("storage-blob-put", "qwen38fnds4mtp7shim"),
        ("parser-date", "qwen38fnds4mtp7shim"),
        ("mbox-scan", "qwen38fnds4mtp7shim"),
        ("storage-put-and-sweep", "qwen38fnds4mtp7shim"),
        ("swift-sevensegment-glyphs", "qwen38fnds4mtp7shim"),
    ],
    "old-sweep1": [("mbox-quoting-both-halves", "qwen38fnds4shim")],
    "old-sweep2": [("storage-put-and-sweep", "qwen38fnds4shim")],
}

MALFORMED = "malformed-tool-call"
STOPPED = "stopped-no-answer"
WRONG_OUTPUT = "wrong-output"
TIMEOUT = "timeout"
OTHER = "other"


@dataclasses.dataclass(frozen=True)
class Trial:
    sweep: str
    task: str
    backend: str
    n_steps: int
    n_tool_calls: int
    tools: tuple[str, ...]
    reasons: tuple[str, ...]
    last_text: str | None
    mode: str


def parse_transcript(
    path: pathlib.Path,
) -> tuple[int, list[str], list[str], str | None]:
    """Return (n_steps, tool names, step_finish reasons, last text) of a transcript."""
    n_steps = 0
    tools: list[str] = []
    reasons: list[str] = []
    last_text: str | None = None
    for line in path.read_text().splitlines():
        d = json.loads(line)
        t = d.get("type")
        if t == "step_start":
            n_steps += 1
        elif t == "tool_use":
            tools.append(d.get("part", {}).get("tool", ""))
        elif t == "text":
            last_text = d.get("part", {}).get("text", "")
        elif t == "step_finish":
            reasons.append(d.get("part", {}).get("reason", ""))
    return n_steps, tools, reasons, last_text


def classify(reasons: tuple[str, ...], last_text: str | None) -> str:
    """Label a death's failure mode from its step_finish reasons and last text.

    The malformed-tool-call signature is a final `unknown` step (the harness
    could not parse the model's tool call) followed by `stop`, with a
    <tool_call> block in the last text. A trial that made tool calls and then
    stopped with no final answer is stopped-no-answer.
    """
    if (
        len(reasons) >= 2
        and reasons[-2] == "unknown"
        and reasons[-1] == "stop"
        and last_text
        and "<tool_call>" in last_text
    ):
        return MALFORMED
    if (
        reasons
        and reasons[-1] == "stop"
        and (not last_text or "<tool_call>" in last_text)
    ):
        return STOPPED
    return OTHER


def ledger_path() -> pathlib.Path:
    """This machine's results ledger, where each row carries `client_log`."""
    import sys as _sys

    _repo = pathlib.Path(__file__).resolve().parent.parent
    _sys.path.insert(0, str(_repo / "benchmarks" / "agent"))
    import results

    return results.default_path()


def client_logs_from_ledger(
    ledger: pathlib.Path,
    task: str,
    backend: str,
    bench_root: pathlib.Path,
) -> list[pathlib.Path]:
    """The authoritative transcript paths for FAIL rows of (task, backend).

    The ledger's `client_log` field names the exact file the harness wrote,
    including the `.2`/`.3` suffix a #112 collision produces. A constructed
    path cannot predict that suffix, and mtime is a proxy for "last flushed"
    that breaks where arms interleave. Join on `client_log`, never on mtime.
    """
    logs: list[pathlib.Path] = []
    if not ledger.exists():
        return logs
    for line in ledger.read_text().splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if (
            d.get("task") == task
            and d.get("backend") == backend
            and d.get("passed") is False
        ):
            log = d.get("client_log")
            if log and pathlib.Path(log).is_relative_to(bench_root):
                logs.append(pathlib.Path(log))
    return logs


def load_trials(bench_root: pathlib.Path) -> list[Trial]:
    trials: list[Trial] = []
    for sweep, tasks in DEATHS.items():
        for task, backend in tasks:
            path = bench_root / sweep / f"{task}-{backend}-opencode-1.stdout.jsonl"
            if not path.exists():
                logger.warning("missing transcript: %s", path)
                continue
            n_steps, tools, reasons, last_text = parse_transcript(path)
            trials.append(
                Trial(
                    sweep=sweep,
                    task=task,
                    backend=backend,
                    n_steps=n_steps,
                    n_tool_calls=len(tools),
                    tools=tuple(tools),
                    reasons=tuple(reasons),
                    last_text=last_text,
                    mode=classify(tuple(reasons), last_text),
                )
            )
    return trials


def report(trials: list[Trial]) -> None:
    by_arm: dict[str, list[Trial]] = collections.defaultdict(list)
    for t in trials:
        arm = "MTP" if t.backend == "qwen38fnds4mtp7shim" else "control"
        by_arm[arm].append(t)

    for arm in ("MTP", "control"):
        arm_trials = by_arm[arm]
        logger.info("=== %s arm: %d deaths ===", arm, len(arm_trials))
        logger.info(
            "%-8s %-30s %5s %5s %-22s %s",
            "sweep",
            "task",
            "steps",
            "tools",
            "mode",
            "reasons",
        )
        for t in arm_trials:
            logger.info(
                "%-8s %-30s %5d %5d %-22s %s",
                t.sweep,
                t.task,
                t.n_steps,
                t.n_tool_calls,
                t.mode,
                ",".join(t.reasons),
            )
        counts = collections.Counter(t.mode for t in arm_trials)
        logger.info("  mode counts: %s", dict(counts))
        logger.info("")

    all_modes = collections.Counter(t.mode for t in trials)
    logger.info("=== all deaths: %d ===", len(trials))
    logger.info("mode counts: %s", dict(all_modes))
    logger.info("")


def default_bench_root() -> pathlib.Path:
    """Where the harness actually writes transcripts.

    ~/bench-logs, not <repo>/../bench-logs. save_transcript() writes to the
    home-relative tree (benchmarks/agent/run.py); the first version of this
    script computed `here.parent.parent / "bench-logs"`, which resolves to
    ~/git/bench-logs and does not exist. The script therefore found nothing
    unless --bench-root was passed, and could not reproduce the result it had
    already published. A durable script whose default cannot re-derive its own
    number is not durable.
    """
    return pathlib.Path.home() / "bench-logs" / "39-mtp-ab"


def main(argv: list[str] | None = None) -> int:
    default_root = default_bench_root()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--bench-root",
        type=pathlib.Path,
        default=default_root,
        help="the 39-mtp-ab bench-logs dir",
    )
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")

    trials = load_trials(args.bench_root)
    if not trials:
        logger.error("no transcripts found under %s", args.bench_root)
        return 1
    report(trials)
    return 0


if __name__ == "__main__":
    sys.exit(main())
