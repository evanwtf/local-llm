#!/usr/bin/env python3
"""Count OpenCode steps that ran to the client's output-token cap. #672

A step that generates exactly `limit.output` tokens was cut off, not finished:
the model ran away and OpenCode stopped it. With thinking off, MiMo-V2.6-Flash
had 10 of 30 trials containing at least one such step at 16,384 tokens, and
every failure and the timeout were among those 10. The ledger row carries only
a trial's total output tokens, so this reads the per-step counts from the
trial's OpenCode event stream (the `client_log`, `*.stdout.jsonl`) instead.

    uv run python scripts/opencode_step_caps.py --cap 16384 ~/bench-logs/*-<backend>-opencode-*.stdout.jsonl

A step counts as capped when its output tokens, or its output plus reasoning
tokens, reach the cap: engines differ in which of the two the limit applies to,
and the row says which it was.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)


def step_tokens(lines) -> list[tuple[int, int]]:
    """(output, reasoning) tokens for every `step_finish` event, in order.
    Lines that are not JSON, or events of other types, are skipped."""
    steps = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "step_finish":
            continue
        tokens = (event.get("part") or {}).get("tokens") or {}
        steps.append(
            (int(tokens.get("output") or 0), int(tokens.get("reasoning") or 0))
        )
    return steps


def capped(steps: list[tuple[int, int]], cap: int) -> list[tuple[int, int]]:
    """The steps that reached the cap on output, or on output + reasoning."""
    return [(o, r) for o, r in steps if o >= cap or o + r >= cap]


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cap", type=int, default=16384, help="the client's limit.output")
    p.add_argument(
        "logs", nargs="+", type=pathlib.Path, help="OpenCode *.stdout.jsonl files"
    )
    args = p.parse_args(argv)
    trials_hit = 0
    steps_hit = 0
    for path in sorted(args.logs):
        with path.open() as fh:
            steps = step_tokens(fh)
        hit = capped(steps, args.cap)
        trials_hit += bool(hit)
        steps_hit += len(hit)
        most = max((o + r for o, r in steps), default=0)
        print(
            f"{path.name}\tsteps={len(steps)}\tcapped={len(hit)}\tmax_step_tokens={most}"
        )
    print(
        f"{trials_hit} of {len(args.logs)} trials have at least one step at the "
        f"{args.cap}-token cap; {steps_hit} such steps in all"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
