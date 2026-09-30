"""The #762 Stage 0 field: active parameters per backend, in billions.

Decode reads the active weights once per token, so active parameters predict a
stack's speed better than anything else on a model card. Stage 0 records the
value before any download (METHODOLOGY.md, "Screening a new stack"). The field
is optional; a wrong value is worse than a missing one, so these tests pin the
values the model name or the repo already states.
"""

from __future__ import annotations

import pathlib
import re
import tomllib
from typing import Any

import pytest

CONFIG = pathlib.Path(__file__).resolve().parent / "tasks.toml"


@pytest.fixture(scope="module")
def backends() -> dict[str, dict[str, Any]]:
    return tomllib.loads(CONFIG.read_text())["backend"]


def test_every_value_is_a_positive_number(backends):
    bad = {
        name: b["active_params_b"]
        for name, b in backends.items()
        if "active_params_b" in b
        and (
            isinstance(b["active_params_b"], bool)
            or not isinstance(b["active_params_b"], int | float)
            or b["active_params_b"] <= 0
        )
    }
    assert not bad


def test_a_name_that_states_active_params_agrees(backends):
    """`...-a12b` in the model name means 12B active; the field must match."""
    wrong = {}
    for name, b in backends.items():
        found = re.search(r"-a(\d+)b", b.get("model", "").lower())
        if found and b.get("active_params_b") != int(found.group(1)):
            wrong[name] = (b.get("model"), b.get("active_params_b"))
    assert not wrong


def test_known_values(backends):
    """Anchors: Qwen3.8-Flash-Next is 125B-A6B; GLM-5.3-Flash is 18B active."""
    assert backends["qwen38fnmlxserve"]["active_params_b"] == 6
    glm = [b for b in backends.values() if "glm-5.3-flash" in b["model"].lower()]
    assert glm
    assert {b["active_params_b"] for b in glm} == {18}
