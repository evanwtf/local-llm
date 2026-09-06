"""Agent identity from the environment, never from introspection. #160

The operator sets three variables when a session starts:

    LOCAL_LLM_AGENT   the session name, e.g. "opus-llama"
    LOCAL_LLM_MODEL   the model id, e.g. "claude-opus-5"
    LOCAL_LLM_EFFORT  the reasoning-effort setting, e.g. "high"

An agent cannot introspect its own model or effort. Asked, it will answer
confidently and may be wrong -- which is exactly the kind of value this
project refuses to record elsewhere. `metal_route` is written `unrecorded`
rather than guessed when the recorded pid is not the process on the port, and
#78 refuses a run rather than publish a row that cannot name its server. So
the identity comes from configuration, not belief.

When a variable is unset the tooling writes `unidentified` loudly -- a
warning on first use, and the literal string in every line -- and
`evidence.py` refuses to write a finding at all, because an unattributed
finding is worth less than no finding: it looks like the attributed kind.

There is deliberately no fallback that infers the model from anything the
process can see. There is not one that is trustworthy, and a plausible wrong
answer is worse here than a blank.
"""

from __future__ import annotations

import logging
import os

AGENT_VAR = "LOCAL_LLM_AGENT"
MODEL_VAR = "LOCAL_LLM_MODEL"
EFFORT_VAR = "LOCAL_LLM_EFFORT"

UNIDENTIFIED = "unidentified"

_warned = False


def identity() -> tuple[str, str, str]:
    """(agent, model, effort) from the environment. Unset -> "unidentified"."""
    return (
        os.environ.get(AGENT_VAR) or UNIDENTIFIED,
        os.environ.get(MODEL_VAR) or UNIDENTIFIED,
        os.environ.get(EFFORT_VAR) or UNIDENTIFIED,
    )


def is_identified() -> bool:
    """Whether all three identity fields are set."""
    return all(part != UNIDENTIFIED for part in identity())


def log_label() -> str:
    """The agent field for log lines: "session/model/effort=X"."""
    agent, model, effort = identity()
    return f"{agent}/{model}/effort={effort}"


class AgentFilter(logging.Filter):
    """Inject the agent label into every record as `record.agent`.

    A filter, not a parameter threaded through call sites: every logger in the
    peer tooling carries the identity without any call site having to pass it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.agent = log_label()
        return True


def install(logger: logging.Logger) -> None:
    """Attach the identity filter, and warn once when the identity is unknown.

    The warning fires on first use only; every line after it still carries the
    literal `unidentified` label, because a blank is what prompts someone to go
    and find out who produced the line.
    """
    global _warned
    logger.addFilter(AgentFilter())
    if not is_identified() and not _warned:
        _warned = True
        logger.warning(
            "agent identity is %s -- set %s, %s, %s to attribute this session",
            log_label(),
            AGENT_VAR,
            MODEL_VAR,
            EFFORT_VAR,
        )
