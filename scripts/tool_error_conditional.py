"""Does a tool error make the NEXT tool call more likely to fail? (#112)

#112's cheapest remedy: "count tool errors already in the conversation
against the probability the next call is malformed." The claim it tests is
that the failure is a *loop* -- once an error enters the context the model's
tool behaviour degrades from there -- rather than a fixed per-call error rate.

The distinction matters for what to do about it. A fixed rate is a formatting
problem to fix in the translator. A rising conditional is a context problem,
and it argues for the shim stripping its own scaffolding out of what it hands
back (#112 item 2) rather than for better parsing.

Reads OpenCode `*.stdout.jsonl` transcripts, which record every tool call with
`state.status` of `completed` or `error`. Calls are grouped by `sessionID`, in
timestamp order, because "prior errors" only means anything within one
conversation.

    uv run python scripts/tool_error_conditional.py ~/bench-logs/112-run1/*.jsonl
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

#: Below this many failures the table is a shape, not a rate. Chosen to be
#: plainly conservative rather than derived: with 14 failures the difference
#: between 2.9% and 5.6% is a handful of events either way.
MEANINGFUL_FAILURES = 30


@dataclasses.dataclass(frozen=True)
class Call:
    session: str
    timestamp: int
    failed: bool


def calls(text: str) -> list[Call]:
    """Every tool call in one transcript, in file order."""
    out: list[Call] = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("type") != "tool_use":
            continue
        part = row.get("part") or {}
        state = part.get("state") or {}
        status = state.get("status")
        if status not in ("completed", "error"):
            continue
        out.append(
            Call(
                session=str(part.get("sessionID") or row.get("sessionID") or "?"),
                timestamp=int(row.get("timestamp") or 0),
                failed=status == "error",
            )
        )
    return out


def conditional(items: list[Call]) -> dict[int, tuple[int, int]]:
    """prior-error count -> (failures, calls) at that count.

    Walks each session in timestamp order. A call's key is how many errors
    already happened in that session before it. Sessions are kept separate:
    an error in one conversation says nothing about the next.
    """
    by_session: dict[str, list[Call]] = collections.defaultdict(list)
    for call in items:
        by_session[call.session].append(call)
    table: dict[int, list[int]] = collections.defaultdict(lambda: [0, 0])
    for session_calls in by_session.values():
        priors = 0
        for call in sorted(session_calls, key=lambda c: c.timestamp):
            table[priors][1] += 1
            if call.failed:
                table[priors][0] += 1
                priors += 1
    return {k: (v[0], v[1]) for k, v in sorted(table.items())}


def report(table: dict[int, tuple[int, int]]) -> str:
    if not table:
        return "no tool calls found"
    lines = ["prior errors | failed / calls | rate"]
    for priors, (failed, total) in sorted(table.items()):
        rate = failed / total if total else 0.0
        lines.append(f"{priors:>12} | {failed:>6} / {total:<5} | {rate:6.1%}")
    baseline = table.get(0)
    after = [(f, t) for k, (f, t) in table.items() if k >= 1]
    if baseline and after and baseline[1]:
        base_rate = baseline[0] / baseline[1]
        f_after = sum(f for f, _ in after)
        t_after = sum(t for _, t in after)
        if t_after:
            lines.append("")
            lines.append(
                f"clean context: {base_rate:.1%}   after >=1 error: "
                f"{f_after / t_after:.1%}   n={t_after}"
            )
    # The caution does not depend on a comparison existing. A single row
    # reading "1 / 1 | 100%" is just as easy to over-read as a comparison is,
    # and this project has twice mistaken a small sample for a finding.
    total_failures = sum(f for f, _ in table.values())
    if total_failures < MEANINGFUL_FAILURES:
        lines.append("")
        lines.append(
            f"NOTE: only {total_failures} failures in this data. "
            "A direction to test, not a measured effect."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("transcripts", nargs="+", type=pathlib.Path)
    args = p.parse_args(argv)

    everything: list[Call] = []
    for path in args.transcripts:
        try:
            everything.extend(calls(path.read_text(errors="replace")))
        except OSError as exc:
            logger.error("%s: %s", path, exc)
    sessions = len({c.session for c in everything})
    logger.info(
        "%d tool calls across %d sessions in %d transcripts",
        len(everything),
        sessions,
        len(args.transcripts),
    )
    logger.info("")
    logger.info("%s", report(conditional(everything)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
