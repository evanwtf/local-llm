"""No test may write a unit record onto the real machine.

`unitctl.STATE_DIR` is bound at import time from the environment, so
`monkeypatch.setenv("LOCAL_LLM_UNIT_DIR", ...)` does nothing and a test that
drives `unitctl.start` or either `serving()` helper writes into
`~/.local-llm-bench/units` for real. The autouse fixture in the root conftest
redirects the constant; these assert it is actually redirected, because a
fixture that silently stopped applying would look exactly like one that works.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import unitctl

REAL = pathlib.Path.home() / ".local-llm-bench" / "units"


def test_the_default_record_path_is_not_on_the_real_machine() -> None:
    got = unitctl.record_path("some-unit")
    assert REAL not in got.parents, f"{got} is the real machine's unit state"


def test_the_default_listing_is_not_the_real_machines() -> None:
    """`units()` defaults to STATE_DIR too, and a differential that lists
    units would otherwise read whatever a real benchmark left behind."""
    assert REAL != unitctl.STATE_DIR


def test_setenv_alone_would_not_have_worked() -> None:
    """The trap, stated so nobody re-derives it.

    STATE_DIR is computed at import. Setting the variable afterwards cannot
    move it, which is why the fixture patches the attribute instead.
    """
    source = (ROOT / "scripts" / "unitctl.py").read_text()
    assert 'os.environ.get("LOCAL_LLM_UNIT_DIR"' in source
    assert "STATE_DIR = pathlib.Path(" in source
