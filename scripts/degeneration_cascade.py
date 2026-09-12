#!/usr/bin/env python3
"""Measure the #112 tool-call degeneration from captured OpenCode transcripts.

#112 hypothesised a **context-poisoning cascade**: once a tool *error* enters
the conversation, the model stops calling tools and narrates about the format,
so P(malformed call) should rise with the count of prior tool errors. The
2026-09-11 read-out could not test it -- the transcripts were gone. This reads
the transcripts captured by `degeneration_cascade_run.py` and measures it.

Input: a `--client-log` directory of `*.stdout.jsonl` OpenCode transcripts.
Each line is one event: `step_start`, `text`, `tool_use`, `step_finish`. A
`tool_use` carries `part.state.status` (`completed`/`error`); a `step_finish`
carries `part.finishReason`; a `text` part carries the model's prose.

For each transcript it walks the steps in order and classifies each one:

- **tool-error**  -- a `tool_use` with `status == "error"`.
- **malformed**   -- a step whose text complains about the tool-call format
  (the signature strings below) or stacks bare `<tool_call>` opens, and which
  did not finish with `tool-calls`. This is the degeneration turn.
- **clean-tool**  -- a step that finished with `tool-calls` and no error.

Then it reports, for every malformed turn, how many tool errors preceded it in
the same conversation -- the conditional #112 asked for -- plus the per-trial
counts and the recovery rate (a malformed turn followed later by a clean tool
call). It prints a table and writes `cascade-summary.json` beside the input.

    uv run python scripts/degeneration_cascade.py ~/bench-logs/112-cascade-*/transcripts
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import logs

logger = logging.getLogger(__name__)

# The model's own words when it degenerates. Taken verbatim from the transcripts
# in #112's opening post and the 2026-09-12 run: it narrates *about* the format
# instead of emitting a call. Case-insensitive.
FORMAT_COMPLAINT = re.compile(
    r"tool.?call format|format (?:is|was) wrong|malformed|function_calls|"
    r"correct format|invalid.*tool",
    re.IGNORECASE,
)
# Two or more bare <tool_call> opens in a row, with no function name between --
# the stacked-opens signature. The shim strips these from returned content, so
# this matches what survives into the transcript text.
STACKED_OPENS = re.compile(r"<tool_call>\s*<tool_call>", re.IGNORECASE)


@dataclass
class Step:
    """One step_start..step_finish span of a transcript."""

    text: str = ""
    tool_names: list[str] = field(default_factory=list)
    tool_statuses: list[str] = field(default_factory=list)
    finish_reason: str = ""
    output_tokens: int = 0

    @property
    def had_tool_error(self) -> bool:
        return any(s == "error" for s in self.tool_statuses)

    @property
    def made_clean_tool_call(self) -> bool:
        """The step landed a real tool call.

        `finishReason` is `tool-calls` on a clean call, but the transcripts also
        carry steps that finish with a null reason yet contain a `completed`
        `tool_use` (the model narrated, then called). Either counts as a landed
        call, so a step that quotes a tool error and then succeeds is not a
        degeneration turn. Caught by script-reverse trial 3, which passed."""
        if self.had_tool_error:
            return False
        if self.finish_reason == "tool-calls":
            return True
        return any(s == "completed" for s in self.tool_statuses)

    @property
    def is_malformed(self) -> bool:
        """A degeneration turn: complains about the format (or stacks bare opens)
        and lands no tool call in the same step."""
        if self.made_clean_tool_call:
            return False
        complained = bool(
            FORMAT_COMPLAINT.search(self.text) or STACKED_OPENS.search(self.text)
        )
        return complained


@dataclass
class Trial:
    name: str
    steps: list[Step] = field(default_factory=list)

    @property
    def tool_errors(self) -> int:
        return sum(1 for s in self.steps if s.had_tool_error)

    @property
    def malformed_steps(self) -> list[int]:
        return [i for i, s in enumerate(self.steps) if s.is_malformed]

    def recovered(self) -> bool:
        """A malformed turn followed later by a clean tool call."""
        mal = self.malformed_steps
        if not mal:
            return False
        first = mal[0]
        return any(s.made_clean_tool_call for s in self.steps[first + 1 :])


def parse_transcript(path: pathlib.Path) -> Trial:
    """Walk a transcript into ordered steps."""
    name = path.name.replace(".stdout.jsonl", "")
    trial = Trial(name=name)
    cur: Step | None = None
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = e.get("type")
        part = e.get("part", {}) or {}
        if etype == "step_start":
            cur = Step()
            trial.steps.append(cur)
        elif cur is None:
            # Events before the first step_start (session metadata): skip.
            continue
        elif etype == "text":
            cur.text += part.get("text") or ""
        elif etype == "tool_use":
            state = part.get("state", {}) or {}
            cur.tool_names.append(part.get("tool") or "?")
            cur.tool_statuses.append(state.get("status") or "?")
        elif etype == "step_finish":
            cur.finish_reason = part.get("finishReason") or part.get("reason") or ""
            toks = part.get("tokens") or {}
            cur.output_tokens = int(toks.get("output") or 0)
    return trial


def conditional(trials: list[Trial]) -> dict[int, tuple[int, int]]:
    """P(malformed | k prior tool errors): {k: (malformed, total tool-attempt steps)}.

    A "tool-attempt step" is one that either made a clean call or was malformed --
    the steps where the model tried to act. `k` is the number of tool errors in
    the conversation strictly before that step.
    """
    buckets: dict[int, list[int]] = {}
    for t in trials:
        prior_errors = 0
        for s in t.steps:
            attempt = s.made_clean_tool_call or s.is_malformed
            if attempt:
                bucket = buckets.setdefault(prior_errors, [0, 0])
                bucket[1] += 1
                if s.is_malformed:
                    bucket[0] += 1
            if s.had_tool_error:
                prior_errors += 1
    return {k: (v[0], v[1]) for k, v in sorted(buckets.items())}


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("transcripts", type=pathlib.Path, help="a --client-log dir")
    args = p.parse_args(argv)
    logs.configure()

    files = sorted(args.transcripts.glob("*.stdout.jsonl"))
    if not files:
        logger.error("no *.stdout.jsonl under %s", args.transcripts)
        return 1
    trials = [parse_transcript(f) for f in files]

    total_errors = sum(t.tool_errors for t in trials)
    with_malformed = [t for t in trials if t.malformed_steps]
    recovered = [t for t in with_malformed if t.recovered()]

    logger.info("transcripts: %d", len(trials))
    logger.info("total tool errors across all conversations: %d", total_errors)
    logger.info(
        "trials with >=1 malformed turn: %d (%s)",
        len(with_malformed),
        ", ".join(t.name for t in with_malformed) or "none",
    )
    logger.info("of those, recovered a clean tool call afterwards: %d", len(recovered))
    logger.info("--- malformed turns vs tool errors before them ---")
    for t in with_malformed:
        first = t.malformed_steps[0]
        prior = sum(1 for s in t.steps[:first] if s.had_tool_error)
        logger.info(
            "  %s: first malformed at step %d, %d tool error(s) before it, recovered=%s",
            t.name,
            first,
            prior,
            t.recovered(),
        )
    logger.info("--- conditional P(malformed | k prior tool errors) ---")
    cond = conditional(trials)
    for k, (mal, tot) in cond.items():
        logger.info("  k=%d: %d/%d malformed (%.0f%%)", k, mal, tot, 100 * mal / tot)

    summary = {
        "transcripts": len(trials),
        "total_tool_errors": total_errors,
        "trials_with_malformed": [t.name for t in with_malformed],
        "recovered": [t.name for t in recovered],
        "conditional_malformed_by_prior_errors": {
            str(k): {"malformed": m, "attempts": n} for k, (m, n) in cond.items()
        },
    }
    out = args.transcripts / "cascade-summary.json"
    out.write_text(json.dumps(summary, indent=2) + "\n")
    logger.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
