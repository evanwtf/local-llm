"""Let the harness disbelieve its own results while a batch is still running.

#55. `RECOMMENDATIONS.md` carried "do not use OpenCode" on 13/29, then 1/15,
then 5/15. Every one of those measured a harness bug -- a client that was never
told which directory to work in (#67). The numbers were published twice, and
nothing in the suite was built to notice that a widely-used tool failing 93% of
edit tasks is not a result, it is a symptom.

The gate calibrates against **this project's own record**, not against outside
claims. If a backend passes comfortably under one client and collapses under
another, the contradiction is internal and checkable without trusting anybody's
blog post. That is exactly the shape the --dir bug made, and it sat in
results.jsonl for two weeks:

    ds4 x claude    46/46
    ds4 x opencode   4/14      <- same weights, same server

Absence is never evidence. With no prior record the gate says nothing: a new
backend legitimately has no history, and crying wolf on every first run is how
a check gets switched off.

**What it catches, replayed against the archived pre---dir rows:**

    qwen38fnq3     halted after 4 of 12 trials
    ds4anthropic   halted after 10 of 26 trials
    ds4            never halted (4/14)
    qwen38fnq3lms  never halted (4/14)

Two of four, saving 8 and 16 trials. It misses the other two honestly: on the
five tasks those cells actually ran, `ds4 x claude` was **16/32 = 50%** in the
same window, so the project's own record held no contradiction to raise. The
thresholds were NOT tuned until all four tripped -- that would be fitting the
archive rather than building a check. A gate that catches half of this class
early is worth having; one calibrated to a single past incident is not.
"""

from __future__ import annotations

import collections
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Enough trials that a bad streak is not just variance. #23 puts a 3-trial
# median at +/-27.9%, so three is a screening run and four is the earliest a
# collapse is worth interrupting for.
MIN_TRIALS = 4

# The signal is the CONTRAST, not an absolute floor. Calibrated against the
# real case: the archived `ds4 x opencode` cell is 4/14 = 28.6%, which sits
# just above an intuitively-chosen 25% floor and would have sailed through.
# A cell scoring at most half of what the same weights manage under another
# client is the shape a harness bug makes.
COLLAPSE_FRACTION = 0.5

# ...and it still has to be bad in absolute terms. Half of a 76% prior is 38%,
# which is a poor result but not necessarily a broken one.
COLLAPSE_CEILING = 0.5

# The comparison arm has to be solid enough to argue with.
MIN_PRIOR = 8
STRONG = 0.75


def rate(rows: list[dict[str, Any]]) -> float:
    return sum(1 for r in rows if r.get("passed")) / len(rows) if rows else 0.0


def prior_by_client(
    history: list[dict[str, Any]],
    backend: str,
    exclude_client: str,
    tasks: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Past trials for this backend under every OTHER client.

    Restricted to `tasks` when given. A backend's overall record mixes task
    difficulties, and the arms must be comparable: `ds4 x claude` is 41/80
    across everything but 46/46 on the five tasks the archived `ds4 x opencode`
    cell actually ran. Comparing against the wrong denominator is how the first
    version of this gate failed to halt the very run it was written for.
    """
    out = collections.defaultdict(list)
    for r in history:
        if r.get("backend") != backend or r.get("client") == exclude_client:
            continue
        if tasks is not None and r.get("task") not in tasks:
            continue
        if r.get("client"):
            out[r["client"]].append(r)
    return dict(out)


def implausible(
    current: list[dict[str, Any]],
    history: list[dict[str, Any]],
    backend: str,
    client: str,
) -> str | None:
    """Reason to stop, or None.

    `current` is this batch's finished trials for one (backend, client) cell.
    `history` is every trustworthy row already on disk -- callers pass rows
    already filtered through `results.is_excluded`.
    """
    if len(current) < MIN_TRIALS:
        return None
    here = rate(current)
    if here > COLLAPSE_CEILING:
        return None

    tasks = {r.get("task") for r in current if r.get("task")}
    prior = prior_by_client(history, backend, client, tasks or None)
    for other, rows in sorted(prior.items()):
        if len(rows) < MIN_PRIOR:
            continue
        there = rate(rows)
        if there >= STRONG and here <= there * COLLAPSE_FRACTION:
            passed = sum(1 for r in current if r.get("passed"))
            return (
                f"{backend} x {client} is {passed}/{len(current)} "
                f"({here:.0%}) in this batch, but {backend} x {other} is "
                f"{sum(1 for r in rows if r.get('passed'))}/{len(rows)} "
                f"({there:.0%}) on record. Same weights, same server, different "
                f"client -- that is the shape a harness bug makes, not a model "
                f"difference. Find the cause before spending more trials; "
                f"pass --allow-implausible to measure a knowingly broken setup."
            )
    return None
