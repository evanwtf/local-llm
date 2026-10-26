"""scripts/preflight_daily.py: the daily currency gate (#726).

The gate is not advisory, so "could not look" must refuse. These pin the one
place it read a failure as an empty answer: the client-image inventory.
"""

from __future__ import annotations

import logging
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import currency
import preflight_daily as pd

DOCKER = "/usr/bin/docker"


def answering(out: str | None) -> currency.Runner:
    """A docker runner that returns `out` for every command."""

    def run(argv: list[str], timeout: int = 30) -> str | None:
        return out

    return run


def test_a_failed_inventory_is_none_not_empty() -> None:
    """Docker present but the daemon down or access denied (review)."""
    got = pd.client_images(answering(None), which=lambda _: DOCKER)
    assert got is None


def test_an_empty_inventory_is_an_empty_set() -> None:
    got = pd.client_images(answering(""), which=lambda _: DOCKER)
    assert got == set()


def test_no_docker_at_all_is_nothing_to_check() -> None:
    """A machine with no docker holds no client image to be stale."""
    got = pd.client_images(answering(None), which=lambda _: None)
    assert got == set()


def test_the_inventory_names_only_client_images() -> None:
    out = "local-llm-client:1.18.33\nlocal-llm-client:1.18.32\nother:1\n"
    got = pd.client_images(answering(out), which=lambda _: DOCKER)
    assert got == {"local-llm-client:1.18.33", "local-llm-client:1.18.32"}


def test_a_client_machine_whose_docker_fails_refuses(
    tmp_path, monkeypatch, caplog
) -> None:
    """No recipes here, docker unreadable: the gate used to exit 0."""
    tasks = tmp_path / "tasks.toml"
    tasks.write_text("")
    monkeypatch.setattr(pd, "client_images", lambda: None)
    monkeypatch.setattr(pd.client_container, "image_pins", lambda *a, **k: None)
    monkeypatch.setattr(pd.logs, "configure", lambda *a, **k: None)
    caplog.set_level(logging.INFO, logger=pd.__name__)
    assert pd.main(["--tasks-file", str(tasks)]) == 1
    assert "cannot list the client images" in caplog.text


def test_a_retired_backend_is_not_a_daily_target(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    cfg = {
        "backend": {
            "old": {
                "recipe_dir": str(recipe),
                "image": "vllm/vllm-openai:v0.30.0",
                "retired": "superseded",
            },
            "new": {"recipe_dir": str(recipe), "image": "vllm/vllm-openai:v0.31.0"},
        }
    }
    recipes, images = pd.targets(cfg)
    assert recipes == {recipe}
    assert images == {"vllm/vllm-openai:v0.31.0"}


def _recipe_at(behind: str) -> currency.Runner:
    """A recipe checkout at 943912c, `behind` commits short of upstream."""
    answers = {
        "fetch --quiet origin": "",
        "rev-parse --short HEAD": "943912c",
        "rev-parse --abbrev-ref origin/HEAD": "origin/main",
        "rev-parse --short origin/main": "94ae731",
        "rev-list --count HEAD..origin/main": behind,
    }

    def run(argv: list[str], timeout: int = 30) -> str | None:
        return answers.get(" ".join(argv[3:]))

    return run


def test_recipe_pins_name_each_live_backends_pin(tmp_path):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    cfg = {
        "backend": {
            "pick": {
                "recipe_dir": str(recipe),
                "recipe_pin": "943912c",
                "recipe_pin_reason": "the pick (#1255)",
            },
            "latest": {"recipe_dir": str(recipe)},
            "gone": {
                "recipe_dir": str(recipe),
                "recipe_pin": "0000000",
                "retired": "x",
            },
        }
    }
    assert pd.recipe_pins(cfg) == {recipe: {"943912c"}}


def test_a_recipe_at_a_backends_pin_passes_the_daily_gate(tmp_path):
    # #1257: the pick pins ~/dsv41_recipe at cc59c36 while v2.0 is upstream.
    (tmp_path / ".git").mkdir()
    assert pd.recipe_refusal(tmp_path, {"943912c"}, _recipe_at("2")) is None


def test_a_recipe_behind_and_off_every_pin_refuses(tmp_path):
    (tmp_path / ".git").mkdir()
    why = pd.recipe_refusal(tmp_path, {"1234567"}, _recipe_at("2"))
    assert why is not None
    assert "behind" in why.lower()


def test_an_unpinned_recipe_behind_upstream_still_refuses(tmp_path):
    (tmp_path / ".git").mkdir()
    assert pd.recipe_refusal(tmp_path, set(), _recipe_at("2")) is not None
    assert pd.recipe_refusal(tmp_path, set(), _recipe_at("0")) is None
