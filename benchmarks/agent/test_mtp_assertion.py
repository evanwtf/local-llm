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

import json

import opencode_config
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


PS_NO_SERVER = "  PID    RSS  ELAPSED COMMAND\n"
PS_MTP_SERVER = (
    "  PID    RSS  ELAPSED COMMAND\n"
    "8110 77957862 17:44 ./ds4-server --metal -m q.gguf --mtp-draft 7 --mtp-timing\n"
)
PS_PLAIN_SERVER = (
    "  PID    RSS  ELAPSED COMMAND\n"
    "8110 77957862 17:44 ./ds4-server --metal -m q.gguf --port 8000\n"
)


def test_the_server_s_own_flag_counts_as_the_switch(monkeypatch):
    """`restart_between_trials_armB.sh` passes --mtp-timing on the server.

    Checking only this process's environment would have refused the one script
    in the repo that actually runs the arm -- an assertion that fires on the
    correct configuration is worse than none, because it gets switched off.
    """
    monkeypatch.delenv("DS4_MTP_TIMING", raising=False)
    assert run.counters_on("ds4", PS_MTP_SERVER) is True
    assert run.counters_on("ds4", PS_PLAIN_SERVER) is False


def test_the_environment_form_also_counts(monkeypatch):
    monkeypatch.setenv("DS4_MTP_TIMING", "1")
    assert run.counters_on("ds4", PS_PLAIN_SERVER) is True


def test_no_counter_switch_refuses_before_the_run(monkeypatch):
    """The precondition that removes the ambiguity.

    With the switch off, a silent arm is unreadable: it might have drafted
    nothing, or it might have drafted perfectly with the counters off. Refusing
    up front means silence later has exactly one meaning.
    """
    monkeypatch.delenv("DS4_MTP_TIMING", raising=False)
    why = run.speculative_preconditions(
        {"mtp7": spec(speculative="mtp", draft_engine="ds4")},
        server_log=None,
        ps_text=PS_PLAIN_SERVER,
    )
    assert why and "DS4_MTP_TIMING" in why


def test_a_switch_with_no_log_still_refuses(monkeypatch):
    """The counters go somewhere. Without --server-log nothing reads them."""
    monkeypatch.setenv("DS4_MTP_TIMING", "1")
    why = run.speculative_preconditions(
        {"mtp7": spec(speculative="mtp", draft_engine="ds4")},
        server_log=None,
        ps_text=PS_PLAIN_SERVER,
    )
    assert why and "--server-log" in why


def test_both_present_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("DS4_MTP_TIMING", "1")
    log = tmp_path / "server.log"
    log.write_text("")
    assert (
        run.speculative_preconditions(
            {"mtp7": spec(speculative="mtp", draft_engine="ds4")},
            server_log=str(log),
            ps_text=PS_PLAIN_SERVER,
        )
        is None
    )


def test_a_run_with_no_speculative_arm_needs_nothing(monkeypatch):
    monkeypatch.delenv("DS4_MTP_TIMING", raising=False)
    assert (
        run.speculative_preconditions(
            {"plain": spec()}, server_log=None, ps_text=PS_NO_SERVER
        )
        is None
    )


def test_mtplx_is_checked_against_its_own_switch(monkeypatch):
    """The two engines expose different mechanisms; neither substitutes."""
    monkeypatch.setenv("DS4_MTP_TIMING", "1")
    monkeypatch.delenv("MTPLX_DECODE_TRACE_JSONL", raising=False)
    why = run.speculative_preconditions(
        {"mtplx": spec(speculative="mtp", draft_engine="mtplx")},
        server_log="/tmp/x",
        ps_text=PS_MTP_SERVER,
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


def test_the_mtp_arms_declare_themselves():
    import pathlib
    import tomllib

    cfg = tomllib.loads((pathlib.Path(run.__file__).parent / "tasks.toml").read_text())
    declared = {
        name: b.get("draft_engine")
        for name, b in cfg["backend"].items()
        if b.get("speculative")
    }
    assert declared == {
        "mtplx": "mtplx",
        "qwen38fnds4mtp7": "ds4",
        "qwen38fnds4mtp7shim": "ds4",
        # #151: the first of these that can actually draft. The three above
        # are driven by clients that send no temperature, and ds4 reaches its
        # Qwen MTP path only at temperature <= 0.
        "qwen38fnds4mtp7greedy": "ds4",
        # #319: draftless n-gram speculation on vLLM. No draft model, so no
        # draft-model engine -- but vLLM counts acceptance, so the arm can
        # still prove it drafted and is declared like any other.
        "qwen36nvfp4specdgx": "vllm",
    }


def test_every_declared_arm_names_an_engine_the_probe_can_read():
    import pathlib
    import tomllib

    cfg = tomllib.loads((pathlib.Path(run.__file__).parent / "tasks.toml").read_text())
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

    cfg = tomllib.loads((pathlib.Path(run.__file__).parent / "tasks.toml").read_text())
    assert not cfg["backend"]["qwen38fnds4shim"].get("speculative")
    assert not cfg["backend"]["qwen38fnds4kimat"].get("speculative")


# --- #210: the row must carry the split, not only the average ---------------
#
# ds4's scheduler measures MTP against plain decode and switches it off when it
# loses, so one trial can hold two populations of cycles. `accept_rate` is
# their average and looks healthy either way. The reader has computed the split
# since #148; the row discarded it, which is how 124 zero-draft cycles out of
# 244 left no trace anywhere in 119 MTP rows.


class FakeCounters:
    """Only what `draft_fields` reads. Shaped like `mtp_timing.Counters`."""

    def __init__(self, cycles, drafting, accepted=10, proposed=20):
        self.cycles = tuple(range(cycles))
        self.drafting = drafting
        self.bypassed = cycles - drafting
        self.accepted = accepted
        self.proposed = proposed
        self.accept_rate = accepted / proposed if proposed else None
        self.used = accepted > 0
        self.drafting_share = drafting / cycles if cycles else None
        self.spec_misses = 0


def test_the_row_records_how_many_cycles_bypassed_the_draft_head():
    fields = run.draft_fields(FakeCounters(cycles=244, drafting=120))
    assert fields["bypassed"] == 124
    assert fields["drafting"] == 120


def test_the_row_records_the_drafting_share():
    fields = run.draft_fields(FakeCounters(cycles=244, drafting=120))
    assert fields["drafting_share"] == pytest.approx(120 / 244)


def test_two_arms_with_the_same_accept_rate_are_told_apart():
    # This is the whole point. Identical `accept_rate`, opposite treatments:
    # one drafted throughout, the other drafted in a fifth of its cycles.
    whole = run.draft_fields(FakeCounters(cycles=100, drafting=100))
    partial = run.draft_fields(FakeCounters(cycles=100, drafting=20))
    assert whole["accept_rate"] == partial["accept_rate"]
    assert whole["drafting_share"] != partial["drafting_share"]


def test_a_reader_without_cycles_is_not_given_a_zero():
    # mtplx counts trace records and has no notion of a bypassed cycle.
    # Inventing 0 here would read as "it never drafted", which is a claim
    # this reader cannot make.
    class TraceCounters:
        accepted = 5
        proposed = 6
        accept_rate = 5 / 6
        used = True
        records = 12
        requests = 3

    fields = run.draft_fields(TraceCounters())
    assert "bypassed" not in fields
    assert "drafting_share" not in fields
    assert fields["records"] == 12


def test_a_trial_with_no_cycles_records_no_share():
    # `drafting_share` is None with nothing to divide by, and None is dropped
    # rather than stored -- absent and zero are different facts.
    fields = run.draft_fields(
        FakeCounters(cycles=0, drafting=0, accepted=0, proposed=0)
    )
    assert "drafting_share" not in fields


# --- #210: the verdict the gate acts on -------------------------------------


def test_no_counters_is_ambiguous_and_says_so():
    assert run.draft_verdict(None) == "no-counters"
    assert run.draft_verdict(FakeCounters(cycles=0, drafting=0)) == "no-counters"


def test_accepting_nothing_is_not_used():
    counters = FakeCounters(cycles=10, drafting=10, accepted=0, proposed=20)
    assert run.draft_verdict(counters) == "not-used"


def test_accepting_tokens_while_drafting_in_no_cycle_is_bypassed():
    # The failure `used` cannot see: a healthy `accepted` earned during warmup,
    # and a measured run that decoded plainly under an MTP label.
    counters = FakeCounters(cycles=244, drafting=0, accepted=800, proposed=1000)
    assert counters.used
    assert run.draft_verdict(counters) == "bypassed"


def test_drafting_in_some_cycles_is_partial_not_ok():
    assert run.draft_verdict(FakeCounters(cycles=244, drafting=120)) == "partial"


def test_drafting_in_every_cycle_is_ok():
    assert run.draft_verdict(FakeCounters(cycles=244, drafting=244)) == "ok"


def test_a_reader_with_no_cycle_notion_is_ok_not_bypassed():
    # mtplx has records, not cycles. Calling that "bypassed" would refuse every
    # MTPLX run on the strength of a field its reader does not have.
    class TraceCounters:
        records = 12
        accepted = 5
        used = True

    assert run.draft_verdict(TraceCounters()) == "ok"


def test_every_verdict_is_one_the_gate_knows():
    cases = [
        None,
        FakeCounters(cycles=0, drafting=0),
        FakeCounters(cycles=10, drafting=10, accepted=0, proposed=20),
        FakeCounters(cycles=10, drafting=0, accepted=8),
        FakeCounters(cycles=10, drafting=5),
        FakeCounters(cycles=10, drafting=10),
    ]
    assert {run.draft_verdict(c) for c in cases} <= set(run.DRAFT_VERDICTS)


# --- #210: silence is only ambiguous when the counters are not proven on ----
#
# On 2026-09-07 six agent trials produced no speculative cycle at all, on a
# server whose argv carried --mtp-timing and which had written 340 cycles
# minutes earlier. The verdict was `no-counters` -- the ambiguous one -- while
# `counters_on` had already answered the question from the server's own
# command line before the run started.


def test_silence_with_counters_proven_on_is_decisive():
    assert run.draft_verdict(None, counters_on=True) == "silent"
    assert (
        run.draft_verdict(FakeCounters(cycles=0, drafting=0), counters_on=True)
        == "silent"
    )


def test_silence_without_that_proof_stays_ambiguous():
    assert run.draft_verdict(None, counters_on=False) == "no-counters"
    assert run.draft_verdict(FakeCounters(cycles=0, drafting=0)) == "no-counters"


def test_counters_on_does_not_change_a_verdict_that_saw_work():
    # It only disambiguates silence. An arm that drafted is judged on what it
    # did, whatever the switch says.
    counters = FakeCounters(cycles=244, drafting=120)
    assert run.draft_verdict(counters, counters_on=True) == "partial"
    assert run.draft_verdict(counters, counters_on=False) == "partial"


def test_the_row_keeps_both_answers_apart():
    # `counters_requested` is the env-var claim about intent; `counters_on` is
    # read from the server's argv. A server started with the flag records
    # false and true, and both belong on the row.
    fields = run.draft_fields(
        FakeCounters(cycles=0, drafting=0),
        source="ds4-mtp-timing",
        counters_requested=False,
        counters_on=True,
    )
    assert fields["counters_requested"] is False
    assert fields["counters_on"] is True


def test_silent_is_a_verdict_the_gate_knows():
    assert "silent" in run.DRAFT_VERDICTS


# --- the sampler the client will actually send (#151) ------------------------


CONFIGURED = "ds4qwenshim/qwen3.8-flash-next-q4"


def backend(**kw):
    got = {"speculative": "mtp", "opencode_model": CONFIGURED}
    got.update(kw)
    return got


def config_with(options, tmp_path):
    """An OpenCode config declaring one model with `options`."""
    path = tmp_path / "opencode.json"
    spec = {"models": {"qwen3.8-flash-next-q4": {}}}
    if options is not None:
        spec["models"]["qwen3.8-flash-next-q4"] = {"options": options}
    path.write_text(json.dumps({"provider": {"ds4qwenshim": spec}}))
    return path


def test_an_mtp_arm_whose_client_sends_no_temperature_is_refused(monkeypatch, tmp_path):
    """Measured 2026-09-08: 119 MTP rows were taken on arms that never
    speculated, because ds4 enters the Qwen MTP path only at temperature <= 0
    and OpenCode sends no temperature. The post-trial gate catches it after a
    full trial and says only that the engine emitted nothing -- the same
    message a dozen causes produce. This says it first, and names the cause.
    """
    monkeypatch.setattr(opencode_config, "CONFIG", config_with(None, tmp_path))
    why = run.greedy_precondition("mtp7", backend(), ("opencode",))
    assert why is not None
    assert "no temperature" in why
    assert "#151" in why


def test_a_positive_temperature_is_refused_too(monkeypatch, tmp_path):
    monkeypatch.setattr(
        opencode_config, "CONFIG", config_with({"temperature": 0.7}, tmp_path)
    )
    why = run.greedy_precondition("mtp7", backend(), ("opencode",))
    assert why is not None
    assert "temperature=0.7" in why


def test_temperature_zero_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        opencode_config, "CONFIG", config_with({"temperature": 0}, tmp_path)
    )
    assert run.greedy_precondition("mtp7", backend(), ("opencode",)) is None


def test_the_refusal_says_that_pinning_it_changes_the_regime():
    """A greedy MTP arm needs a greedy control beside it, or a win is
    unattributable between speculation and greedy decoding. Saying so in the
    refusal is the only place the person who hits it will read it."""
    why = run.greedy_precondition("mtp7", backend(), ("opencode",))
    assert why is None or "greedy control" in why


def test_an_unreadable_config_does_not_refuse_the_run(monkeypatch, tmp_path):
    """ "Cannot tell" is not "sends nothing". Refusing on a missing file would
    be worse than the hole it closes."""
    monkeypatch.setattr(opencode_config, "CONFIG", tmp_path / "absent.json")
    assert run.greedy_precondition("mtp7", backend(), ("opencode",)) is None


def test_a_model_the_config_does_not_declare_does_not_refuse(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode_config, "CONFIG", config_with(None, tmp_path))
    assert (
        run.greedy_precondition(
            "mtp7", backend(opencode_model="other/thing"), ("opencode",)
        )
        is None
    )


def test_the_check_is_about_opencode_and_not_about_every_client(monkeypatch, tmp_path):
    """Another client may send its own temperature. The gate knows OpenCode's
    config and nothing else, and must not refuse an arm it cannot see."""
    monkeypatch.setattr(opencode_config, "CONFIG", config_with(None, tmp_path))
    assert run.greedy_precondition("mtp7", backend(), ("claude",)) is None
    assert run.greedy_precondition("mtp7", backend(), ()) is None


def test_a_non_ds4_draft_engine_is_out_of_scope(monkeypatch, tmp_path):
    """The temperature branch is ds4's. mtplx has its own rules and this
    check would be a guess about them."""
    monkeypatch.setattr(opencode_config, "CONFIG", config_with(None, tmp_path))
    assert (
        run.greedy_precondition("mtplx", backend(draft_engine="mtplx"), ("opencode",))
        is None
    )


def test_a_backend_can_declare_its_shim_pins_the_temperature(monkeypatch, tmp_path):
    """OpenCode's config says `"temperature": false` for this model -- it is
    told the model takes none. Changing that changes it for every backend
    sharing the model, so the greedy arm gets its own shim instance with
    SHIM_TEMPERATURE=0 and declares it here."""
    monkeypatch.setattr(opencode_config, "CONFIG", config_with(None, tmp_path))
    assert (
        run.greedy_precondition("greedy", backend(pinned_temperature=0), ("opencode",))
        is None
    )


def test_a_declared_pin_above_zero_is_refused():
    """The declaration is checked, not trusted. A pinned_temperature of 0.2
    is a legitimate arm and is not an MTP arm."""
    why = run.greedy_precondition(
        "greedy", backend(pinned_temperature=0.2), ("opencode",)
    )
    assert why is not None
    assert "above zero" in why


def test_the_declaration_does_not_replace_the_post_trial_proof():
    """A declaration is what someone wrote in a config file. #210's gate is
    what the engine did. Both, or the first one is a claim."""
    assert run.require_draft_default({"greedy": backend(pinned_temperature=0)})
