"""Classifying a Hugging Face repo id by whether it can load on Metal.

Most new quants of our own models target CUDA or ROCm. On 2026-09-02 the two
most recent builds of our fastest model were ROCMFP4_STRIX and NVFP4-QSA-FP8 --
neither loadable here. Reporting them as news would make a person filter by eye
every time.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import hf_sweep


def test_metal_formats_are_usable():
    for repo in (
        "unsloth/Qwen3.8-Flash-Next-GGUF",
        "mlx-community/gemma-4-26b-mlx-bf16",
        "someone/Qwen3.6-27B-coding-mxfp8",
        "x/Model-IQ2_XXS-GGUF",
    ):
        assert hf_sweep.classify(repo) == "usable", repo


def test_other_hardware_is_rejected():
    for repo in (
        "pugant/Qwen3.8-Flash-Next-ROCMFP4_STRIX_LEAN-GGUF",
        "leoncca/Qwen3.8-Flash-Next-NVFP4-QSA-FP8-E4M3-KV-Scales",
        "x/Model-AWQ",
        "x/Model-GPTQ-int4",
        "x/Model-exl3",
    ):
        assert hf_sweep.classify(repo) == "unusable", repo


def test_unusable_wins_over_usable_in_the_same_name():
    """A name can say both. The blocker decides.

    `ROCMFP4_STRIX_LEAN-GGUF` contains "gguf" and still cannot load here;
    checking the usable list first would have reported it as news.
    """
    assert hf_sweep.classify("a/Model-ROCMFP4-GGUF") == "unusable"
    assert hf_sweep.classify("a/Model-NVFP4-GGUF") == "unusable"


def test_an_unrecognised_name_is_a_question_not_a_silence():
    """Unknown is reported, not hidden -- silence is how a lead gets skipped."""
    assert hf_sweep.classify("asig23/Qwen3.8-Flash-Next") == "unknown"
    assert (
        hf_sweep.classify("TheDrainFlorist/Qwen3.8-Flash-Next-VQ-4.4bpw") == "unknown"
    )


def test_every_watched_family_says_why():
    assert hf_sweep.WATCHED
    for family, why in hf_sweep.WATCHED.items():
        assert why.strip(), family


def test_params_are_read_when_present():
    assert hf_sweep.params_b({"safetensors": {"total": 2_779_931_837_184}}) == 2779.9
    assert hf_sweep.params_b({}) is None


def test_mxfp8_is_ours_and_fp8_is_not():
    """`mxfp8` contains `fp8`, and the two mean opposite things here.

    mxfp8 is MLX's 8-bit format and two backends we run use it. Bare fp8 is
    NVIDIA. A substring match hid every new build of models we actually run.
    """
    assert hf_sweep.classify("mlx-community/Qwen3.6-27B-coding-mxfp8") == "usable"
    assert hf_sweep.classify("someone/gemma-4-31b-mxfp8") == "usable"
    assert hf_sweep.classify("nvidia/Model-FP8") == "unusable"
    assert hf_sweep.classify("nvidia/Model-fp8-dynamic") == "unusable"


# --- hardware profiles: the format lists invert between machines -------------


def test_the_same_repo_is_judged_differently_per_machine():
    """This is why one global table would be wrong.

    EXL and AWQ are unloadable on Metal and fine on a CUDA card. MLX and mxfp8
    are the reverse. A single list hides real candidates on one machine or
    recommends unloadable ones on the other.
    """
    cuda_only = "quocbao747/Ornith-1.5-9B-AWQ-W4A16-g128"
    mac_only = "mlx-community/gemma-4-26b-mlx-bf16"
    assert hf_sweep.classify(cuda_only, "m5-max") == "unusable"
    assert hf_sweep.classify(cuda_only, "rtx3080ti") == "usable"
    assert hf_sweep.classify(mac_only, "m5-max") == "usable"
    assert hf_sweep.classify(mac_only, "rtx3080ti") == "unusable"


def test_gguf_loads_on_both():
    for profile in ("m5-max", "rtx3080ti"):
        assert hf_sweep.classify("unsloth/Qwen3.8-GGUF", profile) == "usable"


def test_ampere_has_no_fp8_or_nvfp4():
    """sm_86 lacks the hardware; those need Ada or Blackwell (#20)."""
    for repo in ("nvidia/Model-FP8", "nvidia/Model-NVFP4"):
        assert hf_sweep.classify(repo, "rtx3080ti") == "unusable"


def test_mxfp8_survives_the_fp8_match_on_the_mac():
    """The protect list, per profile. Regression on #78's substring bug."""
    assert hf_sweep.classify("someone/Qwen3.6-27B-coding-mxfp8", "m5-max") == "usable"


def test_fit_uses_the_metal_ceiling_not_the_machine_ram():
    """128 GB of unified memory is not 128 GB of Metal working set."""
    assert hf_sweep.fits(90.0, "m5-max") is True
    assert hf_sweep.fits(120.0, "m5-max") is False  # over the 112 GiB ceiling


def test_offload_only_helps_a_discrete_card():
    """A 22 GB MoE does not fit 12 GiB of VRAM, and can stream from host RAM."""
    assert hf_sweep.fits(22.0, "rtx3080ti") is False
    assert hf_sweep.fits(22.0, "rtx3080ti", offload=True) is True
    # Unified memory has nowhere else to stream from; the budget is unchanged.
    assert hf_sweep.fits(120.0, "m5-max", offload=True) is False


def test_unknown_size_is_unknown_not_excluded():
    """A model we cannot size is a question, not a silent exclusion."""
    assert hf_sweep.fits(None, "m5-max") is None
    assert hf_sweep.fits(None, "rtx3080ti") is None


def test_canned_queries_name_families_not_repos():
    assert "coding" in hf_sweep.CANNED and "small" in hf_sweep.CANNED
    for terms in hf_sweep.CANNED.values():
        assert terms and all("/" not in term for term in terms)


# --- the DGX Spark (GB10) profile: NVFP4/FP8 invert the Mac's lists (#307) ---


def test_nvfp4_and_fp8_invert_between_mac_and_dgx():
    """The core of #307: an NVIDIA Blackwell format the Mac cannot load is
    exactly what the DGX runs. If these agreed, the split would be pointless."""
    for repo in ("nvidia/Qwen3.8-27B-NVFP4", "some/model-FP8-dynamic"):
        assert hf_sweep.classify(repo, "m5-max") == "unusable", repo
        assert hf_sweep.classify(repo, "gb10") == "usable", repo


def test_mlx_loads_on_the_mac_and_nothing_else():
    repo = "mlx-community/Qwen3.8-Flash-Next-4bit"
    assert hf_sweep.classify(repo, "m5-max") == "usable"
    assert hf_sweep.classify(repo, "gb10") == "unusable"
    assert hf_sweep.classify(repo, "rtx3080ti") == "unusable"


def test_every_profile_is_internally_consistent():
    """A format cannot be both usable and unusable on one machine."""
    for name, prof in hf_sweep.PROFILES.items():
        usable, unusable = set(prof["usable"]), set(prof["unusable"])
        assert usable.isdisjoint(unusable), f"{name}: {usable & unusable}"


def test_the_dgx_watch_list_adds_nvidias_org_the_mac_list_does_not():
    """The user's ask: watch models NVIDIA ships on its own HF org. A DGX-only
    lane -- on the Mac those builds are hidden, so adding them there only grows
    the hidden count."""
    gb10 = hf_sweep.watched_for("gb10")
    mac = hf_sweep.watched_for("m5-max")
    gb10_terms = {(term, kind) for term, kind, _why in gb10}
    mac_terms = {term for term, _kind, _why in mac}
    assert ("nvidia", "author") in gb10_terms
    assert "nvidia" not in mac_terms
    # The shared families stay in every lane.
    assert set(hf_sweep.WATCHED) <= {t for t, _k, _w in gb10}
    assert set(hf_sweep.WATCHED) <= mac_terms


def test_search_builds_an_author_query(monkeypatch):
    """An author sweep hits `?author=<org>` and does NOT also name-filter, or the
    org page would be intersected with a repo-name match and return nothing."""
    captured: dict[str, str] = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"[]"

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(hf_sweep.urllib.request, "urlopen", fake_urlopen)
    hf_sweep.search("", author="nvidia")
    assert "author=nvidia" in captured["url"]
    assert "search=" not in captured["url"]
    # A name search still passes search= and no author=.
    hf_sweep.search("Qwen3.8-Flash-Next")
    assert "search=Qwen3.8-Flash-Next" in captured["url"]
    assert "author=" not in captured["url"]
