"""Has the die stopped cooling? A slope test, not a difference of means. #276

## Why not a difference of consecutive means

The first version of this gate compared two consecutive 30-second medians and
called the machine settled when they differed by no more than 0.3 C. That
tests whether the change is **small**, and the question is whether the change
is **over**. They are not the same, and the gap is not a tuning problem: a
difference-of-means test is satisfied indefinitely by any slow steady fall.

The arithmetic is unforgiving. A die cooling at a constant `r` C/hour moves
`r/120` C between consecutive 30-second windows, so the 0.3 C bar passed for
every rate below **36 C/hour**. A phase beginning on a die falling at 30 C/hour
starts from a machine still shedding heat, and the gate would have called it
settled after its 60-second floor.

The office ambient watcher hit the identical failure the same evening: its
`delta10` fell under a 0.2 C bar while the room was still falling monotonically
at 1.0 C/hour. Two independent instruments, one wrong instrument shape.

## What this measures instead

The least-squares slope over a trailing window, in C/minute. A slope is zero
only when the quantity has stopped moving, whatever its level, so the test
distinguishes "small change" from "no change" -- which is the whole question.

It is also self-calibrating in the way a margin is not: it needs no idea of
the floor, and it survives the room being cooled underneath it.
"""

from __future__ import annotations

import statistics as st
from collections.abc import Sequence

#: A slope estimate needs enough points that noise does not dominate it. Three
#: is the arithmetic minimum; this is the practical one.
MIN_POINTS = 6


def slope_c_per_min(samples: Sequence[tuple[float, float]]) -> float | None:
    """Least-squares slope of (seconds, celsius), in C/minute. None if unfit.

    Returns None rather than 0.0 when there is not enough to fit or every
    sample shares a timestamp: a slope of zero is the settled answer, and
    inventing it from no data is the one wrong answer that stops the wait.
    """
    if len(samples) < MIN_POINTS:
        return None
    times = [t for t, _ in samples]
    temps = [c for _, c in samples]
    mean_t, mean_c = st.fmean(times), st.fmean(temps)
    denominator = sum((t - mean_t) ** 2 for t in times)
    if denominator <= 0.0:  # every sample at one instant
        return None
    numerator = sum(
        (t - mean_t) * (c - mean_c) for t, c in zip(times, temps, strict=True)
    )
    return (numerator / denominator) * 60.0


def window(
    samples: Sequence[tuple[float, float]], now: float, width_s: float
) -> list[tuple[float, float]]:
    """The samples inside the trailing `width_s` seconds."""
    return [(t, c) for t, c in samples if t > now - width_s]


def settled(
    samples: Sequence[tuple[float, float]],
    now: float,
    *,
    width_s: float,
    max_slope_c_per_min: float,
) -> tuple[bool, float | None]:
    """(is it settled, the slope it was judged on).

    The slope comes back either way so a caller can log what it saw rather
    than only what it concluded -- a wait that ends has to say on what
    evidence, and a wait that times out has to say how far off it was.
    """
    got = slope_c_per_min(window(samples, now, width_s))
    if got is None:
        return False, None
    return abs(got) < max_slope_c_per_min, got
