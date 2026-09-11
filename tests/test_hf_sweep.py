"""The Hugging Face sweep judges loadability per machine, and the format lists
INVERT between machines. NVFP4 and FP8 are unloadable on Metal and are exactly
the DGX Spark's target; MLX is the reverse. A classifier that judged every hit
against the Mac -- which the watch loop did before #307 -- hid the DGX's whole
candidate set. These pin the profile split so it cannot silently regress.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import hf_sweep as hs


def test_nvfp4_and_fp8_invert_between_mac_and_dgx() -> None:
    """The core of #307: an NVIDIA Blackwell format the Mac cannot load is
    exactly what the DGX runs. If these ever agreed the split would be pointless.
    """
    for repo in ("nvidia/Qwen3.8-27B-NVFP4", "some/model-FP8-dynamic"):
        assert hs.classify(repo, "m5-max") == "unusable", repo
        assert hs.classify(repo, "gb10") == "usable", repo


def test_mlx_inverts_the_other_way() -> None:
    """MLX loads on the Mac and on nothing else here."""
    repo = "mlx-community/Qwen3.8-Flash-Next-4bit"
    assert hs.classify(repo, "m5-max") == "usable"
    assert hs.classify(repo, "gb10") == "unusable"
    assert hs.classify(repo, "rtx3080ti") == "unusable"


def test_mxfp8_is_protected_on_the_mac() -> None:
    """`mxfp8` contains the substring "fp8" (which means NVIDIA) but is MLX's own
    8-bit format. It must classify usable on the Mac, not be caught as FP8 (#78).
    """
    assert hs.classify("mlx-community/gemma-4-26b-mxfp8", "m5-max") == "usable"


def test_every_profile_is_internally_consistent() -> None:
    """A format cannot be both usable and unusable on one machine, and the
    protect list only makes sense against a real usable substring."""
    for name, prof in hs.PROFILES.items():
        usable = set(prof["usable"])
        unusable = set(prof["unusable"])
        assert usable.isdisjoint(unusable), f"{name}: {usable & unusable}"


def test_the_dgx_watch_list_adds_nvidias_org_the_mac_list_does_not() -> None:
    """The user's ask: watch models NVIDIA ships on its own HF org. That is a
    DGX-only lane -- on the Mac those builds are hidden, so adding them there
    would only grow the hidden count."""
    gb10 = hs.watched_for("gb10")
    mac = hs.watched_for("m5-max")
    gb10_terms = {term for term, _kind, _why in gb10}
    mac_terms = {term for term, _kind, _why in mac}
    assert "nvidia" in gb10_terms
    assert ("nvidia", "author") in {(t, k) for t, k, _ in gb10}
    assert "nvidia" not in mac_terms
    # The shared families are watched on both.
    assert set(hs.WATCHED) <= gb10_terms
    assert set(hs.WATCHED) <= mac_terms


def test_search_builds_an_author_query(monkeypatch) -> None:
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

    monkeypatch.setattr(hs.urllib.request, "urlopen", fake_urlopen)
    hs.search("", author="nvidia")
    assert "author=nvidia" in captured["url"]
    assert "search=" not in captured["url"]


def test_a_name_search_still_passes_search_not_author(monkeypatch) -> None:
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

    monkeypatch.setattr(hs.urllib.request, "urlopen", fake_urlopen)
    hs.search("Qwen3.8-Flash-Next")
    assert "search=Qwen3.8-Flash-Next" in captured["url"]
    assert "author=" not in captured["url"]


@pytest.mark.parametrize("profile", ["m5-max", "rtx3080ti", "gb10"])
def test_classify_never_raises_on_a_bare_name(profile: str) -> None:
    """A repo with no format hint is `unknown`, never a crash -- an unclassified
    repo is a question, not noise to drop."""
    assert hs.classify("someorg/a-plain-model-name", profile) == "unknown"
