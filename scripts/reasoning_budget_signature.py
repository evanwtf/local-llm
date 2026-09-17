"""Does an empty agent solution coincide with a spent reasoning budget? (#349)

A single-Spark field report claims that with thinking **on** and a short
`max_tokens`, the model can spend its whole budget reasoning and return **empty
content** -- a turn-1 death with no tool call rather than a wrong answer. If that
mechanism is real, some fraction of the corpus's `solution_empty` rows are budget
exhaustion, not degeneration, and those are different defects with different fixes
(#112 cataloged the empty solutions but not their cause).

This scans a results.jsonl and asks whether the existing rows already separate the
two. The tell is a row that emitted **many reasoning tokens** yet produced an
**empty or near-empty solution** -- reasoning ate the turn. It cannot *prove* the
ceiling mechanism, because the rows do not record `max_tokens` (a provenance gap
this surfaces): a sweep that varies the ceiling is what settles it. What this does
is say whether the signal is present and on which backends, so the sweep confirms
a boundary rather than discovers a mechanism.

    uv run python scripts/reasoning_budget_signature.py \
        hardware/Cortex-X925-128GB-GB10/results.jsonl

Thinking-off rows (reasoning_tokens == 0) are the control: an empty solution there
cannot be budget exhaustion, so they are reported separately, never pooled in.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Content this short is effectively empty: a handful of tokens cannot carry a
# tool call plus a patch, so the turn produced nothing usable.
NEAR_EMPTY_OUTPUT_TOKENS = 64
# "Many" reasoning tokens -- a full agent turn's worth, well past incidental
# scratch thinking. Chosen to sit above the noise in this corpus, not tuned.
HIGH_REASONING_TOKENS = 1000
# Budget exhaustion is reasoning DWARFING surviving content: the model reasoned
# for the whole turn and little final answer was left. A row where content is a
# small fraction of the reasoning spend is starved; one where content is
# comparable or larger reasoned plenty and still produced output (an unparseable
# but non-empty answer is degeneration, a different defect). Reasoning at least
# this many times the output is the starvation line.
REASONING_TO_OUTPUT_RATIO = 10.0


def _num(v: object) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def classify(rows: list[dict]) -> dict:
    """Split rows by thinking state and pick out the budget-exhaustion signature.

    A *suspect* is a thinking-on row where reasoning was heavy AND the surviving
    content was starved -- near-empty output, or output dwarfed by the reasoning
    spend (ratio). That is budget exhaustion: reasoning ate the turn. A row with
    heavy reasoning AND substantial output that was merely flagged
    ``solution_empty`` reasoned plenty and still emitted an answer that failed to
    parse -- that is degeneration, counted apart, never pooled with the suspects.
    Only thinking-on rows (reasoning_tokens > 0) can qualify; an empty
    thinking-off solution is a third, distinct failure and is counted on its own.
    """
    thinking_on: list[dict] = []
    thinking_off: list[dict] = []
    for r in rows:
        rt = _num(r.get("reasoning_tokens"))
        if rt is None:
            continue  # no reasoning accounting on this row -- cannot classify
        (thinking_on if rt > 0 else thinking_off).append(r)

    def near_empty(r: dict) -> bool:
        ot = _num(r.get("output_tokens"))
        return ot is not None and ot <= NEAR_EMPTY_OUTPUT_TOKENS

    def content_starved(r: dict) -> bool:
        rt = _num(r.get("reasoning_tokens")) or 0
        if rt < HIGH_REASONING_TOKENS:
            return False
        if near_empty(r):
            return True
        ot = _num(r.get("output_tokens"))
        return ot is not None and ot > 0 and rt >= REASONING_TO_OUTPUT_RATIO * ot

    def flagged_empty(r: dict) -> bool:
        return bool(r.get("solution_empty")) or near_empty(r)

    suspects = [r for r in thinking_on if content_starved(r)]
    # Heavy reasoning, empty-flagged, but NOT starved -- reasoned and emitted a
    # substantial (unparseable) answer. Degeneration, not budget exhaustion.
    degenerate = [
        r
        for r in thinking_on
        if flagged_empty(r)
        and (_num(r.get("reasoning_tokens")) or 0) >= HIGH_REASONING_TOKENS
        and not content_starved(r)
    ]
    off_empty = [r for r in thinking_off if flagged_empty(r)]
    return {
        "thinking_on": thinking_on,
        "thinking_off": thinking_off,
        "suspects": suspects,
        "degenerate": degenerate,
        "thinking_off_empty": off_empty,
    }


def _load(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("results", type=pathlib.Path, help="a results.jsonl file")
    args = p.parse_args(argv)

    rows = _load(args.results)
    c = classify(rows)
    on, off = c["thinking_on"], c["thinking_off"]
    print(f"rows: {len(rows)}   thinking-on: {len(on)}   thinking-off: {len(off)}")
    print(
        f"budget-exhaustion suspects (empty/near-empty solution AND "
        f">= {HIGH_REASONING_TOKENS} reasoning tokens): {len(c['suspects'])}"
    )
    if c["suspects"]:
        print(f"\n  {'backend':30} {'task':16} {'reason_tok':>10} {'out_tok':>8}")
        for r in sorted(
            c["suspects"], key=lambda r: -(_num(r.get("reasoning_tokens")) or 0)
        ):
            print(
                f"  {str(r.get('backend', '?'))[:30]:30} "
                f"{str(r.get('task', '?'))[:16]:16} "
                f"{r.get('reasoning_tokens', '-')!s:>10} "
                f"{r.get('output_tokens', '-')!s:>8}"
            )
    print(
        f"\ndegeneration (heavy reasoning, empty-flagged, but substantial output -- "
        f"a different defect): {len(c['degenerate'])}"
    )
    print(
        f"thinking-off empty solutions (control -- NOT budget exhaustion): "
        f"{len(c['thinking_off_empty'])}"
    )
    if not on:
        print(
            "\nNo thinking-on rows in this corpus: it cannot settle #349 on its "
            "own. Run the max_tokens sweep with thinking on to establish the floor."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
