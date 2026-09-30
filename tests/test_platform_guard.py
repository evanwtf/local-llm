"""#325: the ds4 Metal drivers refuse to run off macOS."""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import make_scripts_readme
import platform_guard

#: The drivers #325 named, plus the ones that set DS4_METAL_* since.
METAL_DRIVERS = (
    "bitexact_ab.py",
    "decode_ab.py",
    "decode_ab_engine.py",
    "decode_ab_stack.py",
    "ds4_serve.py",
    "metal_knob_ab.py",
    "prefill_chunk_ab.py",
    "route_agent_ab.py",
)


def test_linux_is_refused(monkeypatch):
    monkeypatch.delenv(platform_guard.ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit) as refused:
        platform_guard.require_darwin("metal_knob_ab.py")
    assert "REFUSING: metal_knob_ab.py is a Metal-lane tool" in str(refused.value)


def test_macos_passes(monkeypatch):
    monkeypatch.delenv(platform_guard.ENV, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    platform_guard.require_darwin("metal_knob_ab.py")


def test_the_suite_escape_passes_on_linux(monkeypatch):
    monkeypatch.setenv(platform_guard.ENV, "1")
    monkeypatch.setattr(sys, "platform", "linux")
    platform_guard.require_darwin("metal_knob_ab.py")


def _main_calls_the_guard(path: pathlib.Path) -> bool:
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            first = node.body[0]
            return (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Call)
                and ast.unparse(first.value.func) == "platform_guard.require_darwin"
            )
    return False


@pytest.mark.parametrize("name", METAL_DRIVERS)
def test_each_metal_driver_guards_first(name):
    assert _main_calls_the_guard(SCRIPTS / name)


def test_a_new_mac_script_that_sets_a_metal_knob_must_guard():
    """So the next Metal driver cannot forget: any `mac` script in the index
    that names a DS4_METAL_ knob and has a `main` calls the guard first."""
    missing = [
        path.name
        for path in sorted(SCRIPTS.glob("*.py"))
        if make_scripts_readme.platform_for(path.name) == "mac"
        and "DS4_METAL_" in path.read_text()
        and "def main(" in path.read_text()
        and not _main_calls_the_guard(path)
    ]
    assert not missing
