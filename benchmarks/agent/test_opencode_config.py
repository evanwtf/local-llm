"""Tests for the #69 config check.

An undeclared `provider/model` makes `opencode run` exit in 0.6s with an empty
stderr_tail, and the harness records a model failure. Six such rows became
GLM-5.3's entire published OpenCode record.
"""

from __future__ import annotations

import json
import pathlib

import opencode_config


def write(tmp_path, providers):
    p = tmp_path / "opencode.json"
    p.write_text(json.dumps({"provider": providers}))
    return p


def test_declared_models_flattens_provider_and_model(tmp_path) -> None:
    cfg = write(tmp_path, {"ds4": {"models": {"a": {}, "b": {}}}})
    assert opencode_config.declared_models(cfg) == {"ds4/a", "ds4/b"}


def test_an_undeclared_model_is_reported(tmp_path) -> None:
    cfg = write(tmp_path, {"ds4": {"models": {"deepseek-v4-flash": {}}}})
    backends = {"glm53ds4": {"opencode_model": "ds4/glm-5.3-flash"}}
    # own_tier is explicit, not probed. Left to the default, this test would
    # pass on the M5 Max and fail on the DGX Spark, because an untiered
    # backend is the Mac's and foreign anywhere else (#345). CI runs on a
    # self-hosted Linux runner, so a machine-dependent test is a red suite
    # waiting for whoever moves it.
    got = opencode_config.missing(backends, cfg, own_tier=None)
    assert got == ["glm53ds4 -> ds4/glm-5.3-flash"]


def test_a_declared_model_is_not_reported(tmp_path) -> None:
    cfg = write(tmp_path, {"ds4": {"models": {"glm-5.3-flash": {}}}})
    backends = {"glm53ds4": {"opencode_model": "ds4/glm-5.3-flash"}}
    assert opencode_config.missing(backends, cfg) == []


def test_a_backend_without_an_opencode_model_is_ignored(tmp_path) -> None:
    cfg = write(tmp_path, {"ds4": {"models": {}}})
    assert opencode_config.missing({"x": {"model": "y"}}, cfg) == []


def test_an_unreadable_config_reports_nothing_missing(tmp_path) -> None:
    """Cannot-tell is not the same as nothing-declared. Reporting every model
    as missing because the file moved would be a false alarm that trains people
    to ignore the check."""
    assert opencode_config.declared_models(tmp_path / "absent.json") is None
    backends = {"glm53ds4": {"opencode_model": "ds4/glm-5.3-flash"}}
    assert opencode_config.missing(backends, tmp_path / "absent.json") == []


def test_malformed_json_is_treated_as_unreadable(tmp_path) -> None:
    p = tmp_path / "opencode.json"
    p.write_text("{not json")
    assert opencode_config.declared_models(p) is None


def test_a_provider_with_no_models_key_does_not_crash(tmp_path) -> None:
    cfg = write(tmp_path, {"ds4": {}})
    assert opencode_config.declared_models(cfg) == set()


def test_the_tracked_reference_config_is_valid_and_declares_providers() -> None:
    """config/opencode.json is the copy a reader can actually review."""
    import pathlib

    ref = pathlib.Path(__file__).resolve().parents[2] / "config/opencode.json"
    data = json.loads(ref.read_text())
    assert data["provider"], "reference config declares no providers"
    assert opencode_config.declared_models(ref)


def test_other_machines_backends_are_not_reported_missing(tmp_path):
    """A desktop-tier backend will never run on this Mac.

    Warning that it is undeclared is noise, and noise in the check that exists
    to catch #69 is how a real warning gets skimmed past.
    """
    config = tmp_path / "opencode.json"
    config.write_text('{"provider": {"ollama": {"models": {"here:1b": {}}}}}')
    backends = {
        "local": {"opencode_model": "ollama/here:1b"},
        "elsewhere": {"opencode_model": "ollama/there:1b", "tier": "desktop-3080ti"},
        "gone": {"opencode_model": "ollama/old:1b", "retired": "superseded"},
        "real": {"opencode_model": "ollama/undeclared:1b"},
    }
    # Explicitly the untiered machine -- see the note in
    # test_an_undeclared_model_is_reported about why this is not probed.
    got = opencode_config.missing(backends, config, own_tier=None)
    assert got == ["real -> ollama/undeclared:1b"]


# --- #301: a static small_model loads a second model beside the measurement --


def _cfg(tmp_path, payload):
    import json

    p = tmp_path / "opencode.json"
    p.write_text(json.dumps(payload))
    return p


def test_a_foreign_small_model_is_reported(tmp_path):
    """The #301 shape exactly: measuring llama.cpp while ollama holds 25 GB."""
    cfg = _cfg(tmp_path, {"small_model": "ollama/qwen3.6:27b-coding"})
    got = opencode_config.small_model_conflict({"llamacpp/qwen3.8-flash-next-q3"}, cfg)
    assert got and "ollama/qwen3.6:27b-coding" in got


def test_no_small_model_is_the_safe_configuration(tmp_path):
    cfg = _cfg(tmp_path, {"model": "vllm/qwen3.6-27b-nvfp4"})
    assert opencode_config.small_model_conflict({"vllm/qwen3.6-27b-nvfp4"}, cfg) is None


def test_a_small_model_already_being_measured_is_not_a_second_model(tmp_path):
    """Pointing it at the resident model loads nothing extra."""
    cfg = _cfg(tmp_path, {"small_model": "vllm/qwen3.6-27b-nvfp4"})
    assert opencode_config.small_model_conflict({"vllm/qwen3.6-27b-nvfp4"}, cfg) is None


def test_an_unreadable_config_says_nothing_rather_than_accusing(tmp_path):
    """Same rule as declared_models: cannot tell is not the same as unsafe."""
    missing_file = tmp_path / "nope.json"
    assert opencode_config.small_model_conflict({"a/b"}, missing_file) is None


def test_the_tracked_reference_copy_declares_no_small_model():
    """config/opencode.json is what someone restores from. #301 shipped in it."""
    import json
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[2]
    data = json.loads((repo / "config/opencode.json").read_text())
    assert "small_model" not in data, (
        "the tracked reference copy must not carry a small_model: restoring "
        "from it would reintroduce #301's 109 GB across two engines"
    )


# --- #345: a tier names hardware, and one of them is this machine -----------


def _tiered_backends() -> dict[str, dict]:
    """Two machines' backends plus an untiered one, all undeclared."""
    return {
        "ours": {"opencode_model": "llamacpp/not-declared", "tier": "gb10-spark"},
        "theirs": {"opencode_model": "mlx/not-declared", "tier": "desktop-3080ti"},
        "untiered": {"opencode_model": "ollama/not-declared"},
        "retired": {"opencode_model": "x/gone", "retired": "superseded"},
    }


def test_a_backend_on_this_machines_tier_is_checked(tmp_path):
    """#345. The guard was off on the DGX Spark, where every backend is tiered.

    `missing()` skipped anything with a `tier`, on the reasoning that a tier
    named another machine. True while `desktop-3080ti` was the only tier;
    false once `gb10-spark` named the machine doing the checking. It cost five
    trials: `opencode run` exits in ~1s with an empty stderr when its model is
    undeclared, and the rows record as model failures.
    """
    cfg = write(tmp_path, {"llamacpp": {"models": {"declared": {}}}})
    gaps = opencode_config.missing(_tiered_backends(), cfg, own_tier="gb10-spark")
    assert any(g.startswith("ours ->") for g in gaps), gaps


def test_another_machines_tier_is_still_skipped(tmp_path):
    """The original reasoning holds for hardware this is not.

    A backend that cannot run here produces a warning nobody can act on, and
    noise in the check that exists to catch #69 is how a real warning gets
    skimmed past.
    """
    cfg = write(tmp_path, {"llamacpp": {"models": {"declared": {}}}})
    gaps = opencode_config.missing(_tiered_backends(), cfg, own_tier="gb10-spark")
    assert not any(g.startswith("theirs ->") for g in gaps), gaps


def test_untiered_backends_belong_to_the_untiered_machine(tmp_path):
    """Symmetry: "untiered" is the M5 Max's tier, not everyone's.

    On a tiered machine an untiered backend is as foreign as any other tier --
    checking the 34 untiered backends from the DGX produced 34 unactionable
    warnings, which is the same defect this issue is about, mirrored.
    """
    cfg = write(tmp_path, {"llamacpp": {"models": {"declared": {}}}})
    on_dgx = opencode_config.missing(_tiered_backends(), cfg, own_tier="gb10-spark")
    on_mac = opencode_config.missing(_tiered_backends(), cfg, own_tier=None)
    assert not any(g.startswith("untiered ->") for g in on_dgx), on_dgx
    assert any(g.startswith("untiered ->") for g in on_mac), on_mac


def test_an_untiered_machine_behaves_exactly_as_before(tmp_path):
    """No regression on the Mac: untiered checked, every tier skipped."""
    cfg = write(tmp_path, {"llamacpp": {"models": {"declared": {}}}})
    gaps = opencode_config.missing(_tiered_backends(), cfg, own_tier=None)
    assert [g.split(" ->")[0] for g in gaps] == ["untiered"], gaps


def test_a_retired_backend_is_skipped_on_its_own_tier(tmp_path):
    """Retirement outranks the tier match; its rows are history, not a run."""
    cfg = write(tmp_path, {"llamacpp": {"models": {"declared": {}}}})
    backends = _tiered_backends()
    backends["retired"]["tier"] = "gb10-spark"
    gaps = opencode_config.missing(backends, cfg, own_tier="gb10-spark")
    assert not any(g.startswith("retired ->") for g in gaps), gaps


def test_every_tier_in_tasks_toml_has_an_owning_machine():
    """A tier nobody owns is a backend nothing will ever check.

    scripts/machines.py is what decides whose a tier is; a tier added to
    tasks.toml without a machine claiming it silently disables this guard
    for every backend carrying it.
    """
    import sys
    import tomllib

    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    import machines

    cfg = tomllib.loads((root / "benchmarks/agent/tasks.toml").read_text())
    used = {b["tier"] for b in cfg["backend"].values() if b.get("tier")}
    owned = {m.tier for m in machines.MACHINES if m.tier}
    assert used <= owned, f"tiers with no owning machine: {sorted(used - owned)}"


def test_every_managed_machine_has_at_most_one_tier():
    """Two machines claiming one tier would make "whose is this" ambiguous."""
    import sys

    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    import machines

    tiers = [m.tier for m in machines.MACHINES if m.tier]
    assert len(tiers) == len(set(tiers)), f"a tier is claimed twice: {tiers}"
