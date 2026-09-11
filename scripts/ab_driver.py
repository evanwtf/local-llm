"""The arm-alternation loop every A/B driver in this repo re-implements. #235 stage 3.

Nine shell drivers contain their own copy of the same three decisions, and the
copies have already drifted: two spell the knob `REPS` and two `ROUNDS`, two
guard it with `ALLOW_ODD_REPS` and one with `ALLOW_ODD_ROUNDS`, and one
describes its order as `A B B A` while another calls the identical order
"alternating". They are the same loop written five times, which is five places
for the position bias to be got wrong.

## What this module owns

**The alternation.** Whichever arm runs first is faster in 9 of 12 reps, median
+0.9% and +5.9% on the first rep of a cold session (#130, #201). The fix is to
rotate which arm leads, and it cancels only when every arm has led the same
number of times -- so the round count must be a multiple of the arm count, and
a count that is not is refused rather than silently reported.

**The failed-arm accounting.** A driver that loses an arm must not exit 0. The
shell piped each arm through `tee` and lost the status to the pipe; that is how
the 2026-09-08 run reported nothing wrong while holding no control arm.

## What this module must NOT own

Anything that depends on what the arms *are*. The drafting-share gates that
`greedy_mtp_ab` applies (#235's checks 2 and 4) belong to the driver that knows
there is an MTP head to have a share of; a decode-rate or a Metal-knob driver
would inherit a gate it cannot satisfy. This module knows an arm has a name, a
backend and a server; it does not know why the arms differ.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import logging
import pathlib
from collections.abc import Callable, Iterator, Sequence

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Arm:
    """One side of a comparison.

    `serve` is a factory, not a running server: it is called once per round
    with that round's tag, and must return a context manager that has the
    server up on entry and stopped on exit. Restarting between arms is not
    optional -- two arms that share a server share its KV cache and its warmup,
    and the second one looks faster for a reason that has nothing to do with
    the treatment.

    It takes the tag because the server's log path must carry the round. A
    factory that names its log by arm alone has round 2 overwrite round 1, and
    then the graph assertion reads the previous round's line -- a control arm
    passing an MTP graph check it never ran.
    """

    name: str
    backend: str
    serve: Callable[[str], contextlib.AbstractContextManager[object]]


def leads_equally(rounds: int, arms: int) -> bool:
    """Whether every arm leads the same number of times over `rounds`.

    This is the condition alternation needs to cancel position bias, and it is
    the generalization of the shell's "an odd round count is refused": with two
    arms it is exactly `rounds % 2 == 0`.
    """
    if arms <= 0:
        raise ValueError("a comparison needs at least one arm")
    return rounds > 0 and rounds % arms == 0


def order(arms: Sequence[Arm], round_number: int) -> list[Arm]:
    """The arms for one round, rotated so each leads in turn.

    Rounds are 1-based. For two arms this is A B, B A, A B, ... -- the order
    `targets_ab.sh` and `strip_toggle_ab.sh` both describe as `A B B A`, which
    is the same sequence read two rounds at a time. For three it is a rotation,
    so each arm leads once every three rounds.
    """
    if round_number < 1:
        raise ValueError("rounds are 1-based")
    if not arms:
        raise ValueError("a comparison needs at least one arm")
    shift = (round_number - 1) % len(arms)
    return list(arms[shift:]) + list(arms[:shift])


def positions(arms: Sequence[Arm], rounds: int) -> dict[str, list[int]]:
    """{arm name: the positions it ran in, over `rounds`}. For tests and logs.

    A driver can assert on this before spending machine time, rather than
    discovering at read-out that one arm led every round.
    """
    seen: dict[str, list[int]] = {arm.name: [] for arm in arms}
    for round_number in range(1, rounds + 1):
        for position, arm in enumerate(order(arms, round_number)):
            seen[arm.name].append(position)
    return seen


def run(
    arms: Sequence[Arm],
    rounds: int,
    run_arm: Callable[[Arm, str, int], int],
    *,
    allow_uneven: bool = False,
    tag_for: Callable[[Arm, int], str] | None = None,
) -> list[str]:
    """Run every arm once per round, alternating. Returns the tags that failed.

    `run_arm(arm, tag, round_number)` returns a process exit code; anything
    non-zero marks that arm's tag failed. A failure does not stop the sweep --
    the surviving arms are still worth having, and stopping early throws away
    good rows -- but the caller gets the list and must not report a clean run.

    **A server that fails to start does stop the sweep, and that asymmetry is
    deliberate.** A non-zero `run_arm` is usually about that arm: its client
    died, its tasks failed. A server that will not start is usually about the
    machine -- a missing model file, a busy port, an engine tree that no longer
    builds -- and repeating it once per arm per round produces nothing but a
    slower way to learn the same thing. Raised by @deepseek reviewing #249.

    Refuses an uneven round count before anything starts, because the refusal
    is worth nothing after the machine time is spent.
    """
    if not leads_equally(rounds, len(arms)) and not allow_uneven:
        raise ValueError(
            f"rounds={rounds} over {len(arms)} arms does not let each arm lead "
            f"equally often, so alternation cannot cancel the position bias "
            f"(#130, #201). Use a multiple of {len(arms)}, or pass "
            f"allow_uneven=True when that is the stated intent."
        )
    if not leads_equally(rounds, len(arms)):
        logger.warning(
            "rounds=%d over %d arms is uneven; position bias (#130, #201) is "
            "NOT cancelled. Proceeding because allow_uneven was set.",
            rounds,
            len(arms),
        )

    failed: list[str] = []
    for round_number in range(1, rounds + 1):
        logger.info("=== round %d of %d ===", round_number, rounds)
        for arm in order(arms, round_number):
            # `tag_for` exists because a tag is not only a log-file name: a
            # report script parses it. #149's read-out splits `t-sweep3` into a
            # prefix and an index, and the completed run is on disk under those
            # names, so a driver whose tags the report cannot parse produces
            # rows nothing can attribute.
            tag = (
                tag_for(arm, round_number)
                if tag_for is not None
                else f"r{round_number}-{arm.name}"
            )
            with arm.serve(tag):
                logger.info("round %d arm %s (%s)", round_number, arm.name, arm.backend)
                if run_arm(arm, tag, round_number) != 0:
                    failed.append(tag)
    return failed


def report(failed: Sequence[str], rounds: int, arms: int) -> int:
    """Turn the failed-tag list into an exit code, and say what was lost."""
    if not failed:
        return 0
    logger.error(
        "INCOMPLETE: %d of %d arms failed (%s). The rows that exist are still "
        "rows, but this is not a paired run -- read it as such.",
        len(failed),
        rounds * arms,
        ", ".join(failed),
    )
    return 1


@contextlib.contextmanager
def nothing(tag: str = "") -> Iterator[None]:
    """A server that is already running, for a driver that manages its own.

    Named rather than inlined so a driver that genuinely shares one server
    across arms has to write it down, and a reader can see that it did.
    """
    yield


@contextlib.contextmanager
def machine_lock(what: str, owner_pid: int, preflight_module: object) -> Iterator[None]:
    """Hold the machine lock for a whole driver run, released last (#268).

    A driver runs many `run.py` children with the server stopped between arms,
    so the lock must span the run and not each child: a process scan in a
    server-down window truthfully reports the machine free, and the lock is the
    only thing that says a measurement is in progress. `run.py` gets `--no-lock`
    because this holds it instead.

    The release is in the `finally`, which runs after the body -- and the body's
    `child.run` does not return until the measurement child is reaped. So the
    lock outlives the child, never the other way round. That ordering is the
    whole of #268: a lock released while `run.py` kept writing left three rows
    on a machine advertised as free.

    Takes the preflight module rather than importing it, so a test can pass a
    fake and this stays importable without benchmarks/agent on the path -- the
    same contract as `batch.machine`.
    """
    taken, why = preflight_module.acquire_lock(what, pid=owner_pid)
    if not taken:
        raise RuntimeError(f"could not claim the machine: {why}")
    logger.info("machine lock held: %s", why)
    try:
        yield
    finally:
        released, why = preflight_module.release_lock(pid=owner_pid)
        logger.info("machine lock released=%s: %s", released, why)


def stamp() -> str:
    """A local, timezone-aware stamp for a log directory name.

    Local and aware on purpose: the directory is read by a person against a
    wall clock, and this project records times in New York.
    """
    return datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y%m%d-%H%M%S")


def logdir_for(root: pathlib.Path, name: str, when: str | None = None) -> pathlib.Path:
    """`<root>/<name>-<stamp>`. Not created -- the caller may only be parsing.

    The stamp is what keeps a re-run from pooling with the run it is replacing.
    A re-run that lands in the same directory, under the same batch label, is
    unreadable afterwards: the rows are in the ledger together and nothing
    distinguishes them. `greedy_mtp_ab.sh` learned that at `9464e30`.
    """
    return root / f"{name}-{when or stamp()}"
