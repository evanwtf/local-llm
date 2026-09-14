"""Per-request engine timing from a llama.cpp server log, for #346.

#346's claim is that most of a harness's time-to-first-token is the client, not
the accelerator: one field report saw ~18 s to first reply under Hermes and ~2 s
under OpenCode against **the same** vLLM endpoint on one GB10. The measurement
that settles it is a subtraction:

    harness overhead = first token as the human sees it - the engine's own
                       prompt-eval time for that request

The right-hand term is exact and free, because the engine times every request
identically whoever sent it. `run.py` already records the LEFT term for OpenCode
(`step_ttft_ms_median`, client-side, `benchmarks/agent/test_opencode_ttft.py`) --
what has been missing is a reader for the right term that does not care which
client made the request. This is that reader.

llama.cpp's server prints one timing block per request:

    slot print_timing: id  0 | task 32478 | prompt eval time =  1421.34 ms /  123 tokens ( ... )
    slot print_timing: id  0 | task 32478 |        eval time =  9385.05 ms /  239 tokens ( ... )
    slot print_timing: id  0 | task 32478 |       total time = 10806.39 ms /  362 tokens

The **prompt eval time** is what the engine spent before the first generated
token -- the engine's TTFT for that request. Subtract it from the client-observed
first token and the remainder is harness overhead, attributed with no client
instrumentation at all.

Two shapes matter and are handled:

* **A cached prompt drops the prompt-eval line.** When the whole prompt is
  already in the KV cache, llama.cpp emits no `prompt eval time` line, so the
  engine's TTFT for that request is ~0. A large observed first token on a cached
  prompt is therefore almost entirely harness overhead -- exactly the case #346
  is about, so a request with no prompt-eval line is kept and marked cached, not
  dropped.
* **Concurrent slots interleave.** Two requests in flight print their three
  lines mixed together, so blocks are grouped by `task` id, not by adjacency.

vLLM has no equivalent per-request log line; its TTFT is the
`vllm:time_to_first_token_seconds` histogram, already scraped into Prometheus
(#346). That is a metrics-scrape path, not a log-parse one, and is out of scope
here -- this reader is the llama.cpp half.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import re
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

#: One `slot print_timing` line. The three kinds share this shape; `total time`
#: is the only one without a per-token tail, so the tail is not matched here.
_LINE = re.compile(
    r"slot print_timing:\s*id\s+(?P<id>\d+)\s*\|\s*task\s+(?P<task>\d+)\s*\|\s*"
    r"(?P<kind>prompt eval time|eval time|total time)\s*=\s*"
    r"(?P<ms>[\d.]+)\s*ms\s*/\s*(?P<tokens>\d+)\s*tokens"
)

_KIND = {
    "prompt eval time": "prompt",
    "eval time": "eval",
    "total time": "total",
}


@dataclasses.dataclass
class Request:
    """One request the engine served, keyed by its llama.cpp task id."""

    task: int
    slot: int
    prompt_ms: float | None = None
    prompt_tokens: int | None = None
    eval_ms: float | None = None
    eval_tokens: int | None = None
    total_ms: float | None = None

    @property
    def prompt_cached(self) -> bool:
        """True when the engine printed no prompt-eval line for this request.

        The whole prompt was already in the KV cache, so the engine spent no
        measurable time before the first token. Not the same as a 0 ms line --
        this is the absence of one, and it is a real state, not missing data.
        """
        return self.prompt_ms is None

    @property
    def engine_ttft_ms(self) -> float:
        """The engine's time to first generated token: the prompt-eval time.

        A cached prompt (no prompt-eval line) reads as 0.0 -- the engine did no
        measurable prefill, so any observed first-token latency on that request
        belongs to the client. This is the term subtracted from the
        client-observed first token to leave harness overhead (#346).
        """
        return self.prompt_ms if self.prompt_ms is not None else 0.0


def parse(text: str) -> list[Request]:
    """Every request in a llama.cpp server log, in first-seen task order.

    Grouped by task id, never by adjacency, so concurrent slots do not merge.
    A request appears as soon as any one of its lines is seen, so a request
    whose block is cut off by the end of the log still appears with the fields
    that were logged (a truncated log is common when a server is still running).
    """
    by_task: dict[int, Request] = {}
    order: list[int] = []
    for line in text.splitlines():
        m = _LINE.search(line)
        if not m:
            continue
        task = int(m["task"])
        if task not in by_task:
            by_task[task] = Request(task=task, slot=int(m["id"]))
            order.append(task)
        req = by_task[task]
        ms, tokens = float(m["ms"]), int(m["tokens"])
        kind = _KIND[m["kind"]]
        if kind == "prompt":
            req.prompt_ms, req.prompt_tokens = ms, tokens
        elif kind == "eval":
            req.eval_ms, req.eval_tokens = ms, tokens
        else:
            req.total_ms = ms
    return [by_task[t] for t in order]


def render(requests: list[Request]) -> list[str]:
    """A markdown table, one row per request, plus a median engine-TTFT line.

    Rendered through the logger as bare lines (`logs.PLAIN`) because CLAUDE.md
    forbids `print` and this output is meant to be pasted.
    """
    lines = [
        "| task | slot | prompt tok | engine TTFT (ms) | eval ms | eval tok | total ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in requests:
        cells = [
            str(r.task),
            str(r.slot),
            "-" if r.prompt_tokens is None else str(r.prompt_tokens),
            "cached" if r.prompt_cached else f"{r.engine_ttft_ms:.1f}",
            "-" if r.eval_ms is None else f"{r.eval_ms:.1f}",
            "-" if r.eval_tokens is None else str(r.eval_tokens),
            "-" if r.total_ms is None else f"{r.total_ms:.1f}",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    measured = [r.engine_ttft_ms for r in requests if not r.prompt_cached]
    cached = sum(1 for r in requests if r.prompt_cached)
    if measured:
        lines += [
            "",
            f"engine TTFT: median {statistics.median(measured):.1f} ms over "
            f"{len(measured)} request(s) with a prompt eval"
            + (f"; {cached} cached (engine TTFT ~0)" if cached else ""),
        ]
    elif requests:
        lines += ["", f"no request logged a prompt eval; {cached} cached"]
    return lines


def main(argv: list[str] | None = None) -> int:
    logs.configure(fmt=logs.PLAIN)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "log",
        nargs="?",
        type=argparse.FileType("r"),
        default=sys.stdin,
        help="llama.cpp server log file, or stdin",
    )
    args = p.parse_args(argv)
    requests = parse(args.log.read())
    if not requests:
        logger.error("no `slot print_timing` blocks found")
        return 1
    for line in render(requests):
        logger.info("%s", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
