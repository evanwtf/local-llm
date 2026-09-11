"""SOURCES.md's "Repositories to watch" section and `upstream_sweep.WATCHED`
are one list in two places. Both files claimed a test held them together; none
did, so the DGX engines (vLLM, TensorRT-LLM, SGLang) could have landed in the
sweep and never in the document a reader opens. This is that test (#307).

It checks the repo slugs, not the descriptions: the prose is the useful, hand-
written half and must stay free to differ from the one-line `why`. What must
not drift is *which repos exist* -- a repo swept but undocumented, or documented
but unswept, is the failure.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import upstream_sweep as us

SOURCES = ROOT / "SOURCES.md"


def _repo_links() -> set[str]:
    """Every `owner/name` linked to github.com in SOURCES.md."""
    text = SOURCES.read_text()
    return set(re.findall(r"github\.com/([\w.-]+/[\w.-]+)", text))


def test_every_watched_repo_is_documented_in_sources() -> None:
    """A repo swept but absent from SOURCES.md is invisible to a reader."""
    linked = _repo_links()
    missing = set(us.WATCHED) - linked
    assert not missing, f"in WATCHED but not linked in SOURCES.md: {sorted(missing)}"


def test_the_dgx_engines_are_present() -> None:
    """The #307 additions specifically -- guard against a silent revert."""
    linked = _repo_links()
    for repo in ("vllm-project/vllm", "NVIDIA/TensorRT-LLM", "sgl-project/sglang"):
        assert repo in us.WATCHED, f"{repo} dropped from WATCHED"
        assert repo in linked, f"{repo} not linked in SOURCES.md"
