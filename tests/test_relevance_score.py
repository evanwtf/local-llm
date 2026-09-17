"""The relevance rubric (#230).

The tests that matter are the ones drawn from claims this project has already
handled, where we know what the right answer turned out to be. A rubric that
agrees with itself is worthless; one that reproduces #171's null, #228's
declined A/B and the ds4#952 pre-emption is doing something.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import relevance_score as rs

# --- the registries come from the repo, not from the rubric ------------------


def test_the_registries_are_read_from_the_repo():
    """If these were hardcoded the rubric would drift the first time a backend
    was added, and nothing would say so."""
    models = rs.known_models()
    engines = rs.known_engines()
    assert "ds4" in engines
    assert "llama.cpp" in engines
    assert models, "tasks.toml defines the models we serve"
    assert rs.this_machine().startswith("MacBook-Pro-M5-Max")


def test_hardware_resolves_by_name_not_by_judgment():
    assert rs.match_hardware("M5 Max") == "exact"
    assert rs.match_hardware("MacBook Pro M5 Max 128GB") == "exact"
    assert rs.match_hardware("M3 Ultra") == "apple-other"
    assert rs.match_hardware("M1 Max") == "apple-other"
    assert rs.match_hardware("RTX 3080 Ti") == "our-other-tier"
    assert rs.match_hardware("GB10 / sm_121") == "foreign"
    assert rs.match_hardware("gfx1151") == "foreign"
    assert rs.match_hardware("unstated") == "unstated"
    assert rs.match_hardware("") == "unstated"


def test_a_model_name_written_the_way_a_person_writes_it_still_resolves():
    """Nobody types the tasks.toml spelling. 'Qwen 3.8 Flash Next' and
    'qwen3.8-flash-next' are the same model and must score the same."""
    models = rs.known_models()
    spaced = rs.match_registry("Qwen 3.8 Flash Next", models)
    hyphenated = rs.match_registry("qwen3.8-flash-next", models)
    assert spaced == hyphenated
    assert spaced in ("exact", "related")


def test_a_model_name_with_the_version_dropped_still_resolves():
    """#239: people write our primary model as 'Qwen Flash Next', without the
    3.8. @ddalcu did, on our exact machine. The version is droppable; the
    distinctive words are what name the model, so it must not read as foreign --
    a false foreign is disqualifying (test_a_foreign_hardware_match...)."""
    models = rs.known_models()
    dropped = rs.match_registry("Qwen Flash Next", models)
    full = rs.match_registry("Qwen 3.8 Flash Next", models)
    assert dropped == full
    assert dropped in ("exact", "related")


def test_dropping_the_version_does_not_match_an_unrelated_model():
    """The version-tolerant match needs a run of two or more shared words, so it
    must not collapse models that share one word or differ by a bare number.

    `Qwen2` is nobody we serve; `Some Other Flash Model` shares only 'flash'.
    Both must stay foreign, or the rubric's most expensive error -- a false
    exact that lets an unrelated claim reach the machine -- is let back in."""
    models = rs.known_models()
    assert rs.match_registry("Qwen2", models) == "foreign"
    assert rs.match_registry("Some Other Flash Model", models) == "foreign"


def test_a_stated_version_is_honoured_not_dropped():
    """Version tolerance is for an OMITTED version, never a different one. A
    claim that states a number keeps it: 'GLM 4 Flash' is not our
    'glm-5.3-flash', and 'Gemma 2 9B' is not our gemma-4. Dropping the digit
    would collapse distinct models and let a foreign one reach the machine."""
    models = rs.known_models()
    assert rs.match_registry("GLM 4 Flash", models) == "foreign"
    assert rs.match_registry("Gemma 2 9B", models) == "foreign"


def test_a_claim_that_adds_words_is_a_different_model():
    """The leading match is directional: a claim may DROP a served model's
    trailing version and build words, never ADD its own. 'GLM Flash
    Experimental' merely starts like 'glm-5.3-flash'; it is not our model."""
    models = rs.known_models()
    assert rs.match_registry("GLM Flash Experimental", models) == "foreign"
    assert rs.match_registry("Ornith B Experimental", models) == "foreign"


def test_the_version_tolerance_does_not_leak_into_engine_matching():
    """match_registry scores engines too. A single letters-plus-digit token
    must not resolve a different engine exact -- 'DS5' is not 'ds4'."""
    engines = rs.known_engines()
    assert rs.match_registry("DS5", engines) == "foreign"
    assert rs.match_registry("mlx4", engines) == "foreign"


# --- the three de-subjectivizing rules ---------------------------------------


def test_an_unstated_decisive_axis_is_a_question_not_a_score():
    """2026-09-08: a verified 110 t/s claim on our exact model named no machine.
    Scoring it would have meant inventing the missing axis."""
    r = rs.score(
        hardware="unstated",
        model="Qwen 3.8 Flash Next",
        engine="mtplx",
        metric="decode-tps",
        effect=115.0,
        exclusivity="anyone",
    )
    assert r["color"] == "yellow"
    assert any("which machine" in q for q in r["ask"])
    # And it must not quietly become a low score instead.
    assert any("not a low score" in reason for reason in r["reasons"])


def test_a_foreign_hardware_match_can_never_take_a_machine_slot():
    """#171: -12.23% Q4 prefill on CUDA, matching us on model, engine and use
    case, had no Metal analog -- three runs at +0.5%, -0.9%, +0.0%."""
    r = rs.score(
        hardware="GB10 / sm_121",
        model="deepseek-v4-flash",
        engine="ds4",
        metric="prefill-tps",
        effect=-12.23,
        exclusivity="few",
    )
    assert r["color"] == "red"
    assert any("#171" in reason for reason in r["reasons"])


def test_context_is_not_zero_because_a_cross_backend_result_scopes_our_question():
    """@iammac2's ROCm result on bbf5a796 is what told us the Metal half was
    unanswered, and that set up the -20.6% we then measured."""
    r = rs.score(
        hardware="gfx1151",
        model="deepseek-v4-flash",
        engine="ds4",
        metric="prefill-tps",
        effect=0.6,
        exclusivity="few",
    )
    assert r["color"] == "red"
    assert any("scopes our question" in reason for reason in r["reasons"])


def test_an_effect_below_our_resolution_is_called_out():
    """#228: upstream claimed +3.8% prefill; the agent harness resolves 17-26%
    paired wall, so an A/B there buys a 3.5-hour null."""
    r = rs.score(
        hardware="M5 Max",
        model="deepseek-v4-flash",
        engine="ds4",
        metric="agent-wall",
        effect=3.8,
        exclusivity="few",
    )
    assert r["axes"]["effect"] == "below-resolution"
    assert any("below the" in reason for reason in r["reasons"])
    assert r["color"] == "yellow", "matches on identity but we cannot resolve it"


def test_the_same_effect_is_answerable_on_a_finer_instrument():
    """3.8% is unanswerable by the agent harness and comfortably answerable by
    ds4-bench, whose within-run repeat spread is 1.5-2.6 pp. The claim did not
    change; the instrument did."""
    r = rs.score(
        hardware="M5 Max",
        model="deepseek-v4-flash",
        engine="ds4",
        metric="prefill-tps",
        effect=3.8,
        exclusivity="few",
    )
    assert r["axes"]["effect"] == "above-resolution"
    assert r["color"] == "green"


# --- the ranking factors -----------------------------------------------------


def test_only_we_can_answer_it_is_what_earns_green():
    """ds4#952's c1909040 gates on [g_device.name hasPrefix:@"Apple M5"] and
    says its M5 numbers are unverified. That is why it pre-empted the queue on
    2026-09-08, and the rubric has to reproduce that."""
    r = rs.score(
        hardware="M5 Max",
        model="deepseek-v4-flash",
        engine="ds4",
        metric="prefill-tps",
        effect=40.6,
        exclusivity="only-us",
    )
    assert r["color"] == "green"
    assert any("pre-empting" in reason for reason in r["reasons"])


def test_the_operators_worked_example_is_green():
    """The operator's own test case: 'I got prefill times reduced 10% on my
    M5 Max MacBook Pro on ds4 with qwen'."""
    r = rs.score(
        hardware="M5 Max MacBook Pro",
        model="qwen3.8-flash-next",
        engine="ds4",
        metric="prefill-tps",
        effect=10.0,
        exclusivity="only-us",
    )
    assert r["color"] == "green"


def test_the_same_claim_from_anyone_is_yellow_not_green():
    """Identical on all four axes; the only difference is that anyone could
    have run it. Exclusivity is urgency, not relevance -- it is what separates
    "spend the machine now" from "worth doing, will keep"."""
    common = {
        "hardware": "M5 Max MacBook Pro",
        "model": "qwen3.8-flash-next",
        "engine": "ds4",
        "metric": "prefill-tps",
        "effect": 10.0,
    }
    exclusive = rs.score(**common, exclusivity="only-us")
    reproducible = rs.score(**common, exclusivity="anyone")
    assert exclusive["color"] == "green"
    assert reproducible["color"] == "yellow"
    assert any("keeps" in reason for reason in reproducible["reasons"])


def test_each_rate_metric_carries_only_the_caveat_it_earned():
    """Decode rate has a measured negative result behind it. Prefill does not,
    and citing #138/#146/#225 for prefill would be citing them for something
    they never tested -- the failure #182 exists to stop."""
    common = {
        "hardware": "M5 Max",
        "model": "qwen3.8-flash-next",
        "engine": "ds4",
        "effect": 30.0,
        "exclusivity": "few",
    }
    decode = rs.score(**common, metric="decode-tps")
    prefill = rs.score(**common, metric="prefill-tps")
    assert any("failed three times" in reason for reason in decode["reasons"])
    assert any("#138, #146, #225" in reason for reason in decode["reasons"])
    # The prefill caveat must exist and must NOT claim the measured result.
    assert any("never been shown here" in reason for reason in prefill["reasons"])
    assert not any("failed three times" in reason for reason in prefill["reasons"])
    assert not any("#138" in reason for reason in prefill["reasons"])


def test_an_apple_other_result_is_a_lead_not_a_result():
    """The sweep skill's standing rule: most developers have no M5, and an
    improvement on an M3 usually shows up here. It must not be discarded, and
    it must not outrank the real thing."""
    r = rs.score(
        hardware="M3 Ultra",
        model="qwen3.8-flash-next",
        engine="ds4",
        metric="prefill-tps",
        effect=5.0,
        exclusivity="few",
    )
    assert r["color"] == "yellow"
    assert any("a lead, not a result" in reason for reason in r["reasons"])


# --- refusals ----------------------------------------------------------------


def test_an_unknown_exclusivity_is_refused_rather_than_defaulted():
    with pytest.raises(SystemExit):
        rs.score(
            hardware="M5 Max",
            model="qwen3.8-flash-next",
            engine="ds4",
            metric="prefill-tps",
            effect=10.0,
            exclusivity="probably",
        )


def test_every_result_states_its_reasons():
    """A tier with no reasons is a number nobody can argue with, which is the
    failure mode a rubric invites."""
    r = rs.score(
        hardware="M5 Max",
        model="qwen3.8-flash-next",
        engine="ds4",
        metric="prefill-tps",
        effect=10.0,
        exclusivity="only-us",
    )
    assert r["reasons"], "a tier must carry its reasons"
    rendered = rs.render(r, "https://example.invalid/post")
    assert any("GREEN" in line for line in rendered)
    assert any("evidence:" in line for line in rendered)
