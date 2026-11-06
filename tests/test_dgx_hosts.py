"""#1153: the DGX node names come from local config, never from the repo."""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import dgx_hosts

SELECTOR = 'DCGM_FI_DEV_SM_CLOCK{instance="$head:9400"}'


def test_the_environment_names_the_head(tmp_path: pathlib.Path) -> None:
    env = {dgx_hosts.ENV["head"]: "head.example"}
    got = dgx_hosts.fill(SELECTOR, env=env, path=tmp_path / "absent.toml")
    assert got == 'DCGM_FI_DEV_SM_CLOCK{instance="head.example:9400"}'


def test_the_local_file_names_both_nodes(tmp_path: pathlib.Path) -> None:
    hosts = tmp_path / "hosts.toml"
    hosts.write_text('[dgx]\nhead = "a.example"\nworker = "b.example"\n')
    got = dgx_hosts.fill(
        'x{instance="$head"} y{instance="$worker"}', env={}, path=hosts
    )
    assert got == 'x{instance="a.example"} y{instance="b.example"}'


def test_the_environment_wins_over_the_file(tmp_path: pathlib.Path) -> None:
    hosts = tmp_path / "hosts.toml"
    hosts.write_text('[dgx]\nworker = "file.example"\n')
    env = {dgx_hosts.ENV["worker"]: "env.example"}
    assert dgx_hosts.lookup("worker", env=env, path=hosts) == "env.example"


def test_an_unconfigured_node_is_refused_with_the_fix(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "absent.toml"
    with pytest.raises(dgx_hosts.HostUnknown) as err:
        dgx_hosts.fill(SELECTOR, env={}, path=path)
    assert dgx_hosts.ENV["head"] in str(err.value)
    assert str(path) in str(err.value)


def test_only_the_roles_named_are_looked_up(tmp_path: pathlib.Path) -> None:
    """A head-only selector works on a machine that knows no worker."""
    env = {dgx_hosts.ENV["head"]: "head.example"}
    assert dgx_hosts.fill("$head", env=env, path=tmp_path / "x.toml") == "head.example"


def test_an_unknown_placeholder_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(KeyError):
        dgx_hosts.fill("$nodes", env={}, path=tmp_path / "x.toml")
