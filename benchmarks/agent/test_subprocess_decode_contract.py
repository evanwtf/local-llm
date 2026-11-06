"""No harness subprocess call decodes text strictly (#1189).

On 2026-10-20 an agent's edit held the byte 0xfa. A `text=True` capture
decoded it strictly, raised UnicodeDecodeError out of the trial loop, and
ended a 42-trial batch at trial 24 (#1091). The error happens inside
`communicate()`, before any `except` near the call can help. This guard
parses the modules that capture agent or agent-code output and refuses a
subprocess call that decodes text without `errors=`. The behavior tests that
feed 0xfa through each path are in test_subprocess_decode.py.
"""

from __future__ import annotations

import ast
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

#: The modules that capture agent or agent-code output.
GUARDED = ("run.py", "grade.py", "timeout_policy.py", "memcap.py")

SUBPROCESS_CALLS = {"run", "check_output", "Popen"}


def _decodes_text(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg in ("text", "universal_newlines"):
            return not (isinstance(kw.value, ast.Constant) and not kw.value.value)
        if kw.arg == "encoding":
            return True
    return False


def _strict_text_calls(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in SUBPROCESS_CALLS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        if _decodes_text(node) and not any(kw.arg == "errors" for kw in node.keywords):
            bad.append(f"{path.name}:{node.lineno}")
    return bad


def test_no_strict_text_capture_in_the_harness():
    bad = [loc for name in GUARDED for loc in _strict_text_calls(HERE / name)]
    assert bad == [], (
        "subprocess call decodes text without errors= (pass errors='replace', "
        f"#1189): {bad}"
    )


def test_the_contract_check_catches_a_strict_call(tmp_path):
    # The guard must refuse the shape it exists for, or it passes vacuously.
    src = tmp_path / "m.py"
    src.write_text(
        "import subprocess\n"
        "subprocess.run(['x'], capture_output=True, text=True)\n"
        "subprocess.run(['x'], capture_output=True, text=True, errors='replace')\n"
        "subprocess.run(['x'], capture_output=True)\n"
    )
    assert _strict_text_calls(src) == ["m.py:2"]
