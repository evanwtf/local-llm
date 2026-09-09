"""Read a `ds4-eval` trace: pass rate, and tokens spent reaching each answer.

Written after a comparison of two builds of one model nearly published a
quality difference that did not exist. The new build read 5/6 against the old
one's 6/6, which fitted the story. Its one failure had generated **exactly
2500 tokens -- the `--tokens` cap passed on the command line**. It was cut off
mid-reasoning, not mistaken; at 8000 tokens it answers correctly and both
builds are 6/6.

So the rule this module enforces: **a failure whose generated-token count
equals the cap is a truncation, not a wrong answer.** A budget the operator
chose is a property of the harness, and counting it as a model error converts
"slower to reason" into "wrong" -- the most damaging substitution available,
because the two argue for opposite decisions.

The second thing it reports is the one that mattered on #138. Decode rate does
not predict session time, and this project has recorded that three times; a
model that decodes 9.5% faster while spending 5% more tokens to reach the same
answer has given most of it back. `tokens_to_answer` belongs beside any tok/s
claim, so it is computed here rather than left to whoever remembers.

    uv run python scripts/eval_trace.py a.trace [b.trace]
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import re
import statistics
import sys

logger = logging.getLogger(__name__)

CASE = re.compile(r"^===== CASE \d+/\d+ (.+?) =====$", re.MULTILINE)
FIELD = re.compile(r"^(\w+):[ \t]*(.*)$", re.MULTILINE)


@dataclasses.dataclass(frozen=True)
class Case:
    name: str
    status: str
    picked: str
    expected: str
    generated: int
    #: True when the answer ran into the harness's own token budget. Such a
    #: case is evidence about the budget, not about the model.
    truncated: bool


@dataclasses.dataclass(frozen=True)
class Trace:
    model: str
    max_tokens: int
    cases: list[Case]

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.status == "PASSED")

    @property
    def failed(self) -> int:
        """Wrong answers only. Truncations are counted separately, by design."""
        return sum(1 for c in self.cases if c.status == "FAILED" and not c.truncated)

    @property
    def truncated(self) -> int:
        return sum(1 for c in self.cases if c.truncated)

    @property
    def tokens_to_answer(self) -> int:
        """Total generated tokens over cases that actually reached an answer."""
        return sum(c.generated for c in self.cases if not c.truncated)


def _fields(block: str) -> dict[str, str]:
    return {k: v.strip() for k, v in FIELD.findall(block)}


def read(path: pathlib.Path) -> Trace:
    text = path.read_text(errors="replace")
    parts = CASE.split(text)
    header = _fields(parts[0])
    max_tokens = int(header.get("max_tokens") or 0)
    cases: list[Case] = []
    # split() yields [header, name1, body1, name2, body2, ...]
    for name, body in zip(parts[1::2], parts[2::2], strict=False):
        f = _fields(body)
        status = f.get("status", "")
        generated = int(f.get("generated_tokens") or 0)
        # A PASSED case that happens to end at the cap still answered, so it
        # is not a truncation. Only a failure at the cap is ambiguous, and
        # ambiguous is exactly what must not be silently scored as wrong.
        truncated = bool(max_tokens and generated >= max_tokens and status == "FAILED")
        cases.append(
            Case(
                name=name.strip(),
                status=status,
                picked=f.get("picked", ""),
                expected=f.get("expected", ""),
                generated=generated,
                truncated=truncated,
            )
        )
    return Trace(model=header.get("model", "?"), max_tokens=max_tokens, cases=cases)


@dataclasses.dataclass(frozen=True)
class Pair:
    name: str
    left: int
    right: int

    @property
    def ratio(self) -> float:
        return self.right / self.left if self.left else 0.0


def compare(left: Trace, right: Trace) -> list[Pair]:
    """Per-case token ratio, over cases both sides answered.

    Cases only one side ran, and cases either side truncated, are dropped:
    a truncated case has no tokens-to-answer to compare, and including it
    would report the cap as a property of the model.
    """
    by_name = {c.name: c for c in right.cases if not c.truncated}
    out: list[Pair] = []
    for case in left.cases:
        if case.truncated or case.name not in by_name:
            continue
        out.append(Pair(case.name, case.generated, by_name[case.name].generated))
    return out


def describe(trace: Trace, path: pathlib.Path) -> list[str]:
    out = [
        (
            f"{path.name}: {trace.passed} passed, {trace.failed} wrong, "
            f"{trace.truncated} truncated of {len(trace.cases)} "
            f"(cap {trace.max_tokens})"
        ),
        f"    model: {trace.model}",
        f"    tokens to answer: {trace.tokens_to_answer}",
    ]
    for c in trace.cases:
        if c.truncated:
            out.append(
                f"    TRUNCATED {c.name}: generated {c.generated} = the cap. "
                "Not a wrong answer -- re-run it with a larger budget."
            )
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("traces", nargs="+", type=pathlib.Path)
    args = p.parse_args(argv)

    traces = [(path, read(path)) for path in args.traces]
    for path, trace in traces:
        for line in describe(trace, path):
            logger.info("%s", line)
        logger.info("")
    if len(traces) != 2:
        return 0

    (lp, left), (rp, right) = traces
    pairs = compare(left, right)
    if not pairs:
        logger.warning("no case was answered by both sides; nothing to compare")
        return 0
    logger.info("-- tokens to answer, %s -> %s --", lp.name, rp.name)
    for pair in pairs:
        logger.info(
            "%-52s %6d -> %6d  %.3f", pair.name, pair.left, pair.right, pair.ratio
        )
    ratios = [p.ratio for p in pairs]
    total_l = sum(p.left for p in pairs)
    total_r = sum(p.right for p in pairs)
    logger.info("")
    logger.info(
        "median per-case ratio %.3f (%+.1f%%) over %d cases; totals %d -> %d (%+.1f%%)",
        statistics.median(ratios),
        (statistics.median(ratios) - 1) * 100,
        len(pairs),
        total_l,
        total_r,
        (total_r / total_l - 1) * 100 if total_l else 0.0,
    )
    logger.info(
        "A decode-rate gain is not a session-time gain if the model spends "
        "more tokens reaching the same answer."
    )
    # The per-case spread is the honest bound on that median, and on the
    # first real comparison it was ten times the effect: ratios from 0.724 to
    # 1.352 around a 1.030 median over five cases. Printing the median alone
    # invites exactly the reading the data cannot support, so the spread goes
    # on the next line and says so itself.
    lo, hi = min(ratios), max(ratios)
    if (hi - lo) > abs(statistics.median(ratios) - 1) * 2:
        logger.warning(
            "per-case ratio spans %.3f to %.3f -- wider than the median "
            "departs from 1.0, so these %d cases do not establish a "
            "direction. More cases, not a bolder sentence.",
            lo,
            hi,
            len(pairs),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
