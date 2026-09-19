"""The remote client's preflight reports each check and fails closed. #562/#579"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import client_preflight as cp


def test_a_failed_check_is_counted_and_printed(capsys):
    rep = cp.Report()
    assert rep.check("a", True, "fine") is True
    assert rep.check("b", False, "broken") is False
    out = capsys.readouterr().out
    assert "PASS  a: fine" in out and "FAIL  b: broken" in out
    assert rep.failed == 1


def test_an_unknown_backend_fails_closed(monkeypatch, capsys):
    monkeypatch.delenv("LOCAL_LLM_SERVER_HOST", raising=False)
    monkeypatch.delenv("LOCAL_LLM_SERVER_FACTS", raising=False)
    assert cp.main(["--backend", "no-such-backend"]) == 1
    assert "FAIL  backend" in capsys.readouterr().out
