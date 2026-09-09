"""After ollama 0.33.3, "engine defaults" means something different (#84).

[ollama#16471](https://github.com/ollama/ollama/pull/16471) changed the sampler
precedence so that model-authored defaults -- GGUF KVs and MLX
`generation_config.json` -- now beat Ollama's own built-ins:

    1. API parameters
    2. Modelfile parameters
    3. GGUF KVs / generation_config.json     <- new in 0.33.3
    4. Ollama general defaults

That matters here because #36 measured a sampler default nobody chose halving a
pass rate: `top_p 0.95` gave 20/21 and `top_p 0.90` gave 7/15 on the same task,
model, engine and client. Qwen3.8-Flash-Next's GGUF declares
`general.sampling.top_p 0.9499999880790710` -- the same neighbourhood.

Backends whose Modelfile sets PARAMETER lines are unaffected: precedence 2 still
wins. The exposed rows are the ones recording `engine defaults (unrecorded)`,
which is 35 `ornith15` rows -- our fastest measured backend, quoted in
RECOMMENDATIONS, and the one backend with no Modelfile parameters.

`sampling_source` was honest but is now insufficient: the same string would
describe two different precedence regimes. It has to name which one.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import run


def test_the_regime_boundary_is_0_33_3() -> None:
    """The literal versions from the PR, so a refactor cannot drift the boundary."""
    assert run.ollama_honors_model_defaults("0.33.2") is False
    assert run.ollama_honors_model_defaults("0.33.3") is True


def test_versions_either_side_of_the_boundary() -> None:
    assert run.ollama_honors_model_defaults("0.31.0") is False
    assert run.ollama_honors_model_defaults("0.33.0") is False
    assert run.ollama_honors_model_defaults("0.34.0") is True
    assert run.ollama_honors_model_defaults("1.0.0") is True


def test_a_release_candidate_counts_as_the_release() -> None:
    """0.33.3-rc0 is where the change actually shipped."""
    assert run.ollama_honors_model_defaults("0.33.3-rc0") is True


def test_an_unreadable_version_is_not_assumed_safe() -> None:
    """Unknown must not silently read as the old regime.

    Guessing "old" here would let a post-upgrade row claim the pre-upgrade
    sampler, which is precisely the silent mislabelling this guards against.
    """
    assert run.ollama_honors_model_defaults(None) is None
    assert run.ollama_honors_model_defaults("") is None
    assert run.ollama_honors_model_defaults("not a version") is None


def test_a_modelfile_sampler_is_unaffected_by_the_regime() -> None:
    """Precedence 2 still wins, so these rows stay comparable across the upgrade."""
    show = {"modelfile": "PARAMETER top_p 0.95\nPARAMETER temperature 0.7\n"}
    got = run.parse_ollama_show(show, ollama_version="0.34.0")
    assert got["sampling"] == {"top_p": "0.95", "temperature": "0.7"}
    assert got["sampling_source"] == "modelfile"


def test_an_empty_modelfile_names_the_regime() -> None:
    """This is the row that changes meaning, so it must say which side it is on."""
    old = run.parse_ollama_show({"modelfile": "FROM x\n"}, ollama_version="0.33.2")
    new = run.parse_ollama_show({"modelfile": "FROM x\n"}, ollama_version="0.33.3")
    assert old["sampling_source"] != new["sampling_source"]
    assert "ollama built-in" in old["sampling_source"]
    assert "model-authored" in new["sampling_source"]
    # Both must remain obviously unrecorded -- naming the regime is not the same
    # as having read the resolved values.
    assert "unrecorded" in old["sampling_source"]
    assert "unrecorded" in new["sampling_source"]


def test_an_unknown_version_says_so_rather_than_picking_a_regime() -> None:
    got = run.parse_ollama_show({"modelfile": "FROM x\n"}, ollama_version=None)
    assert "unknown" in got["sampling_source"]


def test_the_version_argument_is_optional() -> None:
    """Existing callers must keep working; the old string is the unknown case."""
    got = run.parse_ollama_show({"modelfile": "FROM x\n"})
    assert got["sampling"] == {}
    assert "unrecorded" in got["sampling_source"]
