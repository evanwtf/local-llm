"""An MTP arm must prove it drafted, or the run is refused (#148, #151).

Two reports on 2026-09-06, neither looking for the other: an oMLX recipe whose
apparent 2x was mostly repairing an MTP config enabled with no usable draft
head, and ivanfioravanti saying *"In ds4 I've not cooked support for MTP in ds4
chat"* while measuring MTPLX 25 t/s against ds4 18 t/s on an M5 Max.

We run `qwen38fnds4mtp7shim` with an MTP gguf on disk and have never asserted
the draft head is used -- only that the flag was passed. #148 built the
per-trial counters. What was missing is the part that makes them binding:

- the counters were **opt-in**, so a run could measure an MTP arm with the
  assertion switched off and nobody would know;
- a trial with **no counters at all** was only a warning, because "counters
  off" and "the engine never entered the speculative path" look identical --
  and that ambiguity is exactly the state #151 describes.

The fix for the ambiguity is to refuse the run before it starts unless the
counters are switched on. Then silence means one thing.
"""

from __future__ import annotations

import pytest

import run


def spec(**kw):
    got = {"base_url": "http://127.0.0.1:8000", "model": "m"}
    got.update(kw)
    return got


def test_a_backend_declaring_speculation_is_found():
    backends = {
        "plain": spec(),
        "mtp7": spec(speculative="mtp"),
    }
    assert run.speculative_backends(backends) == ["mtp7"]


def test_the_declaration_is_structural_not_a_name_match():
    """`qwen38fnds4mtp7shim` says MTP in its name; that is not a field.

    A name convention cannot be relied on -- `qwen38fnds4shim` differs from the
    MTP arm by four characters, and the arm that carries the treatment must be
    identifiable without parsing English.
    """
    backends = {"qwen38fnds4mtp7shim": spec()}
    assert run.speculative_backends(backends) == []


def test_no_counter_switch_refuses_before_the_run(monkeypatch):
    """The precondition that removes the ambiguity.

    With the switch off, a silent arm is unreadable: it might have drafted
    nothing, or it might have drafted perfectly with the counters off. Refusing
    up front means silence later has exactly one meaning.
    """
    monkeypatch.delenv("DS4_MTP_TIMING", raising=False)
    why = run.speculative_preconditions(
        {"mtp7": spec(speculative="mtp", draft_engine="ds4")}, server_log=None
    )
    assert why and "DS4_MTP_TIMING" in why


def test_a_switch_with_no_log_still_refuses(monkeypatch):
    """The counters go somewhere. Without --server-log nothing reads them."""
    monkeypatch.setenv("DS4_MTP_TIMING", "1")
    why = run.speculative_preconditions(
        {"mtp7": spec(speculative="mtp", draft_engine="ds4")}, server_log=None
    )
    assert why and "--server-log" in why


def test_both_present_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("DS4_MTP_TIMING", "1")
    log = tmp_path / "server.log"
    log.write_text("")
    assert (
        run.speculative_preconditions(
            {"mtp7": spec(speculative="mtp", draft_engine="ds4")}, server_log=str(log)
        )
        is None
    )


def test_a_run_with_no_speculative_arm_needs_nothing(monkeypatch):
    monkeypatch.delenv("DS4_MTP_TIMING", raising=False)
    assert run.speculative_preconditions({"plain": spec()}, server_log=None) is None


def test_mtplx_is_checked_against_its_own_switch(monkeypatch):
    """The two engines expose different mechanisms; neither substitutes."""
    monkeypatch.setenv("DS4_MTP_TIMING", "1")
    monkeypatch.delenv("MTPLX_DECODE_TRACE_JSONL", raising=False)
    why = run.speculative_preconditions(
        {"mtplx": spec(speculative="mtp", draft_engine="mtplx")}, server_log="/tmp/x"
    )
    assert why and "MTPLX_DECODE_TRACE_JSONL" in why


def test_requiring_draft_is_the_default_for_a_declared_arm():
    """#148 shipped the refusal switched off. An assertion nobody turns on is
    documentation, not a gate."""
    backends = {"mtp7": spec(speculative="mtp")}
    assert run.require_draft_default(backends) is True


def test_requiring_draft_stays_off_when_no_arm_declares_speculation():
    assert run.require_draft_default({"plain": spec()}) is False


# ------------------------------------------ the declaration is real, in config
#
# The three arms this project actually runs. A test rather than a comment,
# because the whole mechanism above is one missing key away from not applying,
# and the failure is silent: the arm runs, the rows look normal, and nothing
# asserts the treatment.


def test_the_three_mtp_arms_declare_themselves():
    import pathlib
    import tomllib

    cfg = tomllib.loads(
        (pathlib.Path(run.__file__).parent / "tasks.toml").read_text()
    )
    declared = {
        name: b.get("draft_engine")
        for name, b in cfg["backend"].items()
        if b.get("speculative")
    }
    assert declared == {
        "mtplx": "mtplx",
        "qwen38fnds4mtp7": "ds4",
        "qwen38fnds4mtp7shim": "ds4",
    }


def test_every_declared_arm_names_an_engine_the_probe_can_read():
    import pathlib
    import tomllib

    cfg = tomllib.loads(
        (pathlib.Path(run.__file__).parent / "tasks.toml").read_text()
    )
    for name, b in cfg["backend"].items():
        if not b.get("speculative"):
            continue
        engine = b.get("draft_engine")
        assert engine in run.DraftProbe.SWITCHES, (
            f"{name} declares draft_engine={engine!r}, which no counter "
            f"mechanism can read -- the arm would refuse every run"
        )


def test_the_non_mtp_twin_is_not_declared():
    """`qwen38fnds4shim` is arm A: same shim, no MTP. It must stay undeclared,
    or every run of the control arm would demand draft counters."""
    import pathlib
    import tomllib

    cfg = tomllib.loads(
        (pathlib.Path(run.__file__).parent / "tasks.toml").read_text()
    )
    assert not cfg["backend"]["qwen38fnds4shim"].get("speculative")
    assert not cfg["backend"]["qwen38fnds4kimat"].get("speculative")
