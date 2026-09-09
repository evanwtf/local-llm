"""The shared arm-alternation loop (#235 stage 3).

The position bias these tests protect is measured, not theoretical: whichever
arm runs first is faster in 9 of 12 reps, median +0.9% and +5.9% on the first
rep of a cold session (#130, #201). Alternation is the only thing that removes
it, and it removes it only when every arm leads equally often.
"""

from __future__ import annotations

import contextlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ab_driver
from source_text import code_of


def arm(name: str, events: list[str] | None = None) -> ab_driver.Arm:
    @contextlib.contextmanager
    def serve(tag: str):
        if events is not None:
            events.append(f"serve:{name}")
        try:
            yield object()
        finally:
            # try/finally, not a bare post-yield append: without it this fake
            # would record no stop when the body raises, and
            # test_a_server_is_stopped_even_when_its_arm_raises would be
            # asserting on the fake's own defect rather than the module's.
            if events is not None:
                events.append(f"stop:{name}")

    return ab_driver.Arm(name=name, backend=f"backend-{name}", serve=serve)


A, B, C = arm("a"), arm("b"), arm("c")


# --- the alternation itself --------------------------------------------------


def test_two_arms_alternate_which_one_leads() -> None:
    assert [x.name for x in ab_driver.order([A, B], 1)] == ["a", "b"]
    assert [x.name for x in ab_driver.order([A, B], 2)] == ["b", "a"]
    assert [x.name for x in ab_driver.order([A, B], 3)] == ["a", "b"]


def test_the_two_arm_order_is_the_shells_A_B_B_A() -> None:
    """`targets_ab.sh` and `strip_toggle_ab.sh` describe their order as
    `A B B A` while `greedy_mtp_ab.sh` calls the identical thing alternation.
    They are the same sequence read two rounds at a time, and this test is
    here so the port cannot quietly change one of them."""
    flat = [x.name for r in (1, 2) for x in ab_driver.order([A, B], r)]
    assert flat == ["a", "b", "b", "a"]


def test_three_arms_rotate_so_each_leads_once() -> None:
    leaders = [ab_driver.order([A, B, C], r)[0].name for r in (1, 2, 3)]
    assert sorted(leaders) == ["a", "b", "c"]


def test_every_arm_runs_every_round() -> None:
    """Rotation must reorder the arms, never drop one."""
    for rounds in range(1, 7):
        names = sorted(x.name for x in ab_driver.order([A, B, C], rounds))
        assert names == ["a", "b", "c"]


def test_each_arm_holds_each_position_equally_over_a_full_cycle() -> None:
    """The property that actually cancels the bias, stated directly rather
    than inferred from the leader alone."""
    for arms, rounds in (([A, B], 4), ([A, B, C], 6)):
        seen = ab_driver.positions(arms, rounds)
        for name, spots in seen.items():
            counts = [spots.count(p) for p in range(len(arms))]
            assert len(set(counts)) == 1, f"{name} is not evenly placed: {spots}"


def test_rounds_are_one_based() -> None:
    with pytest.raises(ValueError):
        ab_driver.order([A, B], 0)


# --- the refusal, which is the whole reason the module exists ----------------


def test_an_uneven_round_count_is_refused_before_anything_starts() -> None:
    """The refusal is worth nothing after the machine time is spent, so it
    must happen before the first server starts."""
    events: list[str] = []
    with pytest.raises(ValueError, match="position bias"):
        ab_driver.run([arm("a", events), arm("b", events)], 3, lambda *a: 0)
    assert events == [], "nothing may start on a run that will be refused"


def test_the_refusal_generalizes_to_three_arms() -> None:
    assert ab_driver.leads_equally(3, 3)
    assert ab_driver.leads_equally(6, 3)
    assert not ab_driver.leads_equally(4, 3)
    assert not ab_driver.leads_equally(2, 3)


def test_for_two_arms_the_refusal_is_exactly_the_shells_odd_check() -> None:
    for rounds in range(1, 11):
        assert ab_driver.leads_equally(rounds, 2) == (rounds % 2 == 0)


def test_zero_rounds_is_not_a_valid_run() -> None:
    assert not ab_driver.leads_equally(0, 2)


def test_an_uneven_count_can_be_forced_and_says_so(caplog) -> None:
    """Refusing is right; refusing with no way through is how a guard gets
    deleted instead of satisfied. But a forced run must be loud, or its rows
    are read later as if the bias had been cancelled."""
    with caplog.at_level("WARNING", logger=ab_driver.logger.name):
        failed = ab_driver.run([A, B], 3, lambda *a: 0, allow_uneven=True)
    assert failed == []
    assert "NOT cancelled" in caplog.text


# --- servers, which must not be shared across arms ---------------------------


def test_each_arm_gets_its_own_server_started_and_stopped() -> None:
    """Two arms sharing a server share its KV cache and its warmup, and the
    second looks faster for a reason that is not the treatment."""
    events: list[str] = []
    ab_driver.run([arm("a", events), arm("b", events)], 2, lambda *a: 0)
    assert events == [
        "serve:a",
        "stop:a",
        "serve:b",
        "stop:b",
        "serve:b",
        "stop:b",
        "serve:a",
        "stop:a",
    ]


def test_the_server_factory_is_told_which_round_it_is_in() -> None:
    """A regression, caught the first time this module was adopted. The server
    factory names its own log, and a factory that names it by arm alone has
    round 2 overwrite round 1 -- after which the graph assertion reads the
    previous round's line, and a control arm passes an MTP graph check it never
    ran. The tag is the only thing that carries the round into the factory."""
    seen: list[str] = []

    def make(name: str) -> ab_driver.Arm:
        @contextlib.contextmanager
        def serve(tag: str):
            seen.append(tag)
            yield object()

        return ab_driver.Arm(name=name, backend=name, serve=serve)

    ab_driver.run([make("a"), make("b")], 2, lambda *a: 0)
    assert sorted(seen) == ["r1-a", "r1-b", "r2-a", "r2-b"]
    assert len(set(seen)) == 4, "two rounds must not share a server log"


def test_a_server_is_stopped_even_when_its_arm_raises() -> None:
    events: list[str] = []

    def explode(*args: object) -> int:
        raise RuntimeError("the arm fell over")

    with pytest.raises(RuntimeError):
        ab_driver.run([arm("a", events), arm("b", events)], 2, explode)
    assert events == ["serve:a", "stop:a"], "a leaked server holds the GPU"


# --- failed arms, which must not read as a clean run -------------------------


def test_a_failed_arm_is_reported_and_the_rest_still_run() -> None:
    """The 2026-09-08 failure: the shell piped each arm through `tee` and lost
    the exit status to the pipe, so a run that lost an arm exited 0."""
    codes = iter([0, 3, 0, 0])
    failed = ab_driver.run([A, B], 2, lambda *a: next(codes))
    assert failed == ["r1-b"]
    assert ab_driver.report(failed, 2, 2) == 1


def test_a_clean_run_reports_success() -> None:
    assert ab_driver.report([], 2, 2) == 0


def test_the_failure_report_names_which_arms_were_lost(caplog) -> None:
    with caplog.at_level("ERROR", logger=ab_driver.logger.name):
        ab_driver.report(["r1-b", "r2-a"], 2, 2)
    assert "r1-b" in caplog.text and "r2-a" in caplog.text
    assert "not a paired run" in caplog.text


def test_the_tag_identifies_the_round_and_the_arm() -> None:
    """One log and one ledger row per arm per round. A tag that collides
    across rounds makes `assert_graph` read the previous round's line."""
    tags: list[str] = []
    ab_driver.run([A, B], 2, lambda a, tag, r: tags.append(tag) or 0)
    assert tags == ["r1-a", "r1-b", "r2-b", "r2-a"]
    assert len(set(tags)) == 4


# --- the log directory, which keeps a re-run from pooling --------------------


def test_a_logdir_carries_a_stamp_so_a_rerun_does_not_pool() -> None:
    """A re-run that lands in the same directory under the same batch label is
    unreadable afterwards: the rows sit in the ledger together and nothing
    distinguishes them. `greedy_mtp_ab.sh` learned that at `9464e30`."""
    root = pathlib.Path("/tmp/bench")
    first = ab_driver.logdir_for(root, "greedy-mtp-ab", "20260909-034500")
    second = ab_driver.logdir_for(root, "greedy-mtp-ab", "20260909-041500")
    assert first != second
    assert first.parent == root
    assert first.name.startswith("greedy-mtp-ab-")


def test_logdir_for_does_not_create_anything() -> None:
    """A `--help` run must not litter `~/bench-logs` with an empty directory,
    and argparse builds the default before it knows the command will run."""
    root = pathlib.Path("/tmp/definitely-not-created-by-a-default")
    ab_driver.logdir_for(root, "x")
    assert not root.exists()


def test_the_stamp_is_timezone_aware() -> None:
    """Naive local time is what makes two machines' logs unorderable."""
    assert len(ab_driver.stamp()) == len("20260909-034500")


# --- the boundary the peer flagged: what must NOT live here ------------------


def test_this_module_knows_nothing_about_mtp() -> None:
    """#235's checks 2 and 4 -- the treatment arm's drafting share, and the
    control arm's being zero -- belong to the driver that knows there is an MTP
    head to have a share of. A decode-rate or Metal-knob driver would inherit a
    gate it cannot satisfy. Raised by @deepseek reviewing #248."""
    code = code_of(ROOT / "scripts" / "ab_driver.py")
    for term in ("mtp", "drafting", "draft"):
        assert term not in code.lower(), f"{term!r} is per-driver, not shared"
    # The prose may still cite greedy_mtp_ab by name -- explaining where the
    # loop came from is the point of the docstring. Asserting on raw text
    # would forbid the explanation, which is the same word-for-a-metric
    # confusion this repo has now made three times.
    assert "greedy_mtp_ab" in (ROOT / "scripts" / "ab_driver.py").read_text()


def test_a_driver_can_name_its_own_tags() -> None:
    """A tag is an interface when a report script parses it (#149).

    `route_ab_report` splits `t-sweep3` into a prefix and an index, and a
    completed run is on disk under those names. The default `r3-t` would be
    unparseable there, so the hook is not cosmetic.
    """
    arms = [arm("t"), arm("r")]
    seen: list[str] = []

    def run_arm(a, tag, n):
        seen.append(tag)
        return 0

    ab_driver.run(arms, 2, run_arm, tag_for=lambda a, n: f"{a.name}-sweep{n}")
    assert seen == ["t-sweep1", "r-sweep1", "r-sweep2", "t-sweep2"]


def test_the_default_tag_is_unchanged() -> None:
    # Four drivers already read these names off disk.
    arms = [arm("mtp"), arm("plain")]
    seen: list[str] = []
    ab_driver.run(arms, 2, lambda a, tag, n: seen.append(tag) or 0)
    assert seen == ["r1-mtp", "r1-plain", "r2-plain", "r2-mtp"]
