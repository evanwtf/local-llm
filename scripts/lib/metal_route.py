"""The Metal 4 TensorOps route markers, in one place. #149, #235

ds4 prints which Metal route it took at startup. Two scripts read those lines
for opposite purposes: the driver asserts per sweep that the arm took the route
it claims, and the report re-reads the same logs at read-out to attribute rows.

They had their own copies of the strings. That is the drift this project has
been burned by before -- an assertion and a read-out that disagree about what
they are looking for both keep passing, and the disagreement is only visible in
a result nobody can explain. One definition, imported twice.

`FAST_PATH_LINE` is required of both arms and is not about the route at all: it
says the Qwen fast path is in use. An arm that fell back to the slow path is not
a slower version of the treatment, it is a different experiment.
"""

from __future__ import annotations

# Read out of real server logs, not from the source:
#
#   Metal 4 tensor API enabled for Tensor kernels
#   Metal 4 tensor API available but not enabled (numerics); set \
#       DS4_METAL_ENABLE_TENSOR=1 to override
#
# `TENSOR_LINE` is the whole line. `WITHHOLD_LINE` is deliberately the middle
# of the second one: the override hint after the semicolon names an env var,
# and matching it would tie us to ds4's advice about how to flip the route
# rather than to the fact that it declined.
#
# Both halves had their own copy before this module: `route_ab_report` matched
# the tail and `ds4_serve` matched the head (`Metal 4 tensor API available but
# not enabled`). Neither contains the other, both match the same line today,
# and neither would have noticed the other half being reworded.
TENSOR_LINE = "Metal 4 tensor API enabled for Tensor kernels"
WITHHOLD_LINE = "available but not enabled (numerics)"
FAST_PATH_LINE = "complete fast path"

#: Enough to say the server reached the route decision at all, without saying
#: which way it went. `ds4_serve` waits on this before reading the two above.
ANY_MARKER = "Metal 4 tensor API"

TENSOR = "t"
WITHHELD = "r"
ARMS = (TENSOR, WITHHELD)


def route_of(text: str) -> tuple[bool, bool]:
    """(the tensor route is enabled, the withhold line is present)."""
    return TENSOR_LINE in text, WITHHOLD_LINE in text


def arm_route_ok(text: str, arm: str) -> bool:
    """Whether a server log shows the route `arm` is defined by.

    The withheld arm needs both halves: the withhold line present **and** the
    tensor line absent. Checking only the withhold line would pass a server
    that printed both, which is what a stale binary beside a fresh tree looks
    like.
    """
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, not {arm!r}")
    enabled, withheld = route_of(text)
    if arm == TENSOR:
        return enabled
    return withheld and not enabled


def why_not(text: str, arm: str) -> str:
    """Why `arm`'s route assertion failed, in the words of what was found.

    Separate from `arm_route_ok` so the check stays a predicate and the message
    stays readable. A refusal that says only "assertion failed" sends the
    reader to the source instead of to the log.
    """
    enabled, withheld = route_of(text)
    if FAST_PATH_LINE not in text:
        return f"the log lacks {FAST_PATH_LINE!r}, so no Qwen fast path ran"
    if arm == TENSOR and not enabled:
        return f"a {TENSOR} sweep whose log lacks {TENSOR_LINE!r}"
    if arm == WITHHELD and enabled:
        return f"an {WITHHELD} sweep whose log shows {TENSOR_LINE!r}"
    if arm == WITHHELD and not withheld:
        return f"an {WITHHELD} sweep whose log lacks {WITHHOLD_LINE!r}"
    return ""


def fast_path_ok(text: str) -> bool:
    """Whether the Qwen complete fast path ran. Required of every arm."""
    return FAST_PATH_LINE in text
