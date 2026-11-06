"""#1153: the Prometheus power check names the head from local config."""

from __future__ import annotations

import logging
import pathlib
import subprocess
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import dgx_hosts
import gpu_utilization as gu


def test_the_query_carries_the_configured_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_DGX_HEAD", "head.example")
    argvs: list[list[str]] = []

    def fake_run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        argvs.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='"12.5"\n', stderr="")

    monkeypatch.setattr(gu.subprocess, "run", fake_run)
    assert gu.prometheus_p90() == 12.5
    query = argvs[0][argvs[0].index("-d") + 2]
    assert query == (
        "quantile_over_time(0.9, "
        'DCGM_FI_DEV_POWER_USAGE{instance="head.example:9400"}[15m])'
    )


def test_an_unnamed_head_is_a_loud_unknown_not_a_query(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No name: no query, None ("unknown"), and a WARNING that says the fix."""
    monkeypatch.delenv("LOCAL_LLM_DGX_HEAD", raising=False)
    monkeypatch.setattr(dgx_hosts, "HOSTS_FILE", tmp_path / "absent.toml")

    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("queried Prometheus with no head name")

    monkeypatch.setattr(gu.subprocess, "run", refuse)
    with caplog.at_level(logging.WARNING):
        assert gu.prometheus_p90() is None
    assert "LOCAL_LLM_DGX_HEAD" in caplog.text
