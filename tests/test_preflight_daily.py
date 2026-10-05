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
