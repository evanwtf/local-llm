"""Claim both Sparks for a two-node batch, and refuse without earlyoom. #680

The harness runs on a separate client, inside `client_container.py`. Its run
lock lives in the container and dies with it, so during a remote batch
`machine_state.py` on either Spark read FREE, and the "machine is empty"
preflight looked at the client (#680 findings 5 and 6). Nothing stopped
another session from loading a model onto a node mid-batch.

The fix (operator, 2026-11-07): the client ssh's to each node and takes a
`machine_claim.py` session claim there before the first trial. It holds both
for the whole batch and releases both on every exit path. If either claim
fails, the batch is refused, and a claim already taken is released.

#680 finding 9 (operator, 2026-11-07): earlyoom is the memory guard for
two-node servers. The 14 GiB server floor does not fit recipes that idle at
3 to 4 GiB, and no per-backend floor is set. So a two-node batch refuses to
start unless earlyoom is active on both nodes.

The node names come from `dgx_hosts` (env or the untracked hosts file), never
from the repo (#1153).
"""

from __future__ import annotations

import contextlib
import json
import logging
import shlex
import subprocess
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

import agent_identity
import dgx_hosts

logger = logging.getLogger(__name__)

#: `run(argv, timeout)` -> (exit status, stdout and stderr). Tests pass a fake,
#: so no test reaches a real Spark.
Runner = Callable[[Sequence[str], float], tuple[int, str]]

#: BatchMode: a password prompt would hang the batch; fail instead.
SSH = ("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
#: The checkout on each node (hardware/agent-opener-prompt.md, step 4).
REMOTE_REPO = "~/git/local-llm"
#: The run lock on each node (preflight.LOCK_PATH).
REMOTE_LOCK = "~/.local-llm-bench/run-lock.json"
#: A claim is a short `uv run`; the first one on a node may sync the venv.
CLAIM_TIMEOUT_S = 120.0
EARLYOOM_TIMEOUT_S = 30.0


class ClaimError(RuntimeError):
    """A node could not be checked or claimed, so the batch must not start."""


def _run(argv: Sequence[str], timeout: float) -> tuple[int, str]:
    try:
        p = subprocess.run(
            list(argv), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 255, str(exc)
    return p.returncode, (p.stdout + p.stderr).strip()


def is_two_node(facts: Mapping[str, Any]) -> bool:
    """Whether the server facts describe a two-node server.

    `server_facts.py --cluster-peer` writes `env.cluster.nodes = 2` (#680
    finding 3). A single-node facts file has no `cluster` key.
    """
    env = facts.get("env")
    cluster = env.get("cluster") if isinstance(env, Mapping) else None
    return isinstance(cluster, Mapping) and cluster.get("nodes") == 2


def nodes(
    env: Mapping[str, str] | None = None, path: Any = None
) -> list[tuple[str, str]]:
    """[(role, host)] for the head and the worker. Raises ClaimError."""
    try:
        return [(role, dgx_hosts.lookup(role, env, path)) for role in dgx_hosts.ROLES]
    except dgx_hosts.HostUnknown as exc:
        raise ClaimError(str(exc)) from exc


def check_earlyoom(
    targets: Sequence[tuple[str, str]], run: Runner | None = None
) -> str | None:
    """None if earlyoom is active on every node, else the refusal."""
    run = run or _run
    down = []
    for role, host in targets:
        status, out = run(
            [*SSH, host, "systemctl is-active earlyoom"], EARLYOOM_TIMEOUT_S
        )
        state = out.splitlines()[-1].strip() if out else ""
        if status != 0 or state != "active":
            down.append(f"{role} ({state or f'ssh exit {status}'})")
    if not down:
        return None
    return (
        f"preflight: earlyoom is not active on the {', '.join(down)}. It is the "
        "only memory guard for a two-node server (#680 finding 9). Start it "
        "with `sudo -E uv run python scripts/setup_earlyoom.py --apply` on "
        "that node, then rerun."
    )


def _claim_command(action: str, what: str | None = None) -> str:
    """The remote shell command for `machine_claim.py <action>`.

    The identity goes with it: the node has none of the client's environment,
    and `machine_claim.py` refuses an unidentified claim and releases a
    session claim only for the agent that took it.
    """
    agent, model, effort = agent_identity.identity()
    ident = " ".join(
        f"{var}={shlex.quote(value)}"
        for var, value in (
            (agent_identity.AGENT_VAR, agent),
            (agent_identity.MODEL_VAR, model),
            (agent_identity.EFFORT_VAR, effort),
        )
    )
    tail = f" --what {shlex.quote(what)}" if what is not None else ""
    return (
        f"cd {REMOTE_REPO} && {ident} "
        f"uv run python scripts/machine_claim.py {action}{tail}"
    )


def _release(role: str, host: str, run: Runner) -> bool:
    status, out = run([*SSH, host, _claim_command("release")], CLAIM_TIMEOUT_S)
    if status == 0:
        logger.info("released the claim on the %s", role)
        return True
    logger.error(
        "could NOT release the claim on the %s (exit %d): %s. Release it by "
        "hand there: `uv run python scripts/machine_claim.py release`",
        role,
        status,
        out,
    )
    return False


def _reconcile(role: str, host: str, what: str, run: Runner) -> None:
    """Release the claim on a node whose acquire ended without an answer.

    An ssh that times out, drops, or is stopped by a signal can end after the
    node wrote the claim. A session claim never expires, so that node would
    read BUSY until someone cleared it by hand. The claim is released only
    when its `what` is this batch's own, token included, so a claim another
    session held before is never touched.
    """
    status, out = run([*SSH, host, f"cat {REMOTE_LOCK}"], CLAIM_TIMEOUT_S)
    try:
        lock = json.loads(out) if status == 0 else None
    except ValueError:
        lock = None
    if isinstance(lock, dict) and lock.get("what") == what:
        _release(role, host, run)


@contextlib.contextmanager
def held(
    targets: Sequence[tuple[str, str]], what: str, run: Runner | None = None
) -> Iterator[None]:
    """Hold a session claim on every node for the body of the `with`.

    Raises ClaimError before the body runs when any claim fails, after it
    releases the claims already taken. The release runs on every exit path,
    in reverse order. A signal must reach this as an exception (SIGINT does;
    the caller turns SIGTERM into one), or no `finally` runs.
    """
    if not agent_identity.is_identified():
        raise ClaimError(
            "the agent identity is "
            f"{agent_identity.log_label()}, so no node can be claimed. Set "
            f"{agent_identity.AGENT_VAR}, {agent_identity.MODEL_VAR} and "
            f"{agent_identity.EFFORT_VAR} on the client"
        )
    run = run or _run
    # A token unique to this batch, so an uncertain acquire can be told from a
    # claim somebody else already held (_reconcile).
    what = f"{what} [batch {uuid.uuid4().hex[:12]}]"
    taken: list[tuple[str, str]] = []
    pending: tuple[str, str] | None = None
    try:
        for role, host in targets:
            pending = (role, host)
            status, out = run(
                [*SSH, host, _claim_command("acquire", what)], CLAIM_TIMEOUT_S
            )
            if status != 0:
                raise ClaimError(
                    f"cannot claim the {role} (exit {status}): {out}. The batch "
                    "needs a claim on both nodes (#680)"
                )
            pending = None
            logger.info("claimed the %s for %r", role, what)
            taken.append((role, host))
        yield
    finally:
        if pending is not None:
            _reconcile(*pending, what, run)
        for role, host in reversed(taken):
            _release(role, host, run)
