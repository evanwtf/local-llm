"""#288: a child must not inherit whatever a library did to the C environ.

`import readline` calls setenv("COLUMNS","80") and setenv("LINES","24") at the
C level. Python's `os.environ` is a snapshot and never reflects that, so the
keys are invisible to every Python-level check -- which is why #288's
investigation looked at the environment and found nothing.

A child spawned with `env=None` inherits them. A child handed an explicit dict
does not. pytest imports readline unconditionally
(`_pytest/capture.py:161` -> `_readline_workaround`), so under the suite the
keys are always in the C environ, and whether a differential passes then
depends on which readline the interpreter links -- GNU sets them, libedit does
not. That is the real variable #288 mistook for "the checkout".
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from lib import child

PROBE = (
    "import os,json,sys;"
    "sys.stdout.write(json.dumps("
    "{k: os.environ.get(k) for k in ('COLUMNS','LINES')}))"
)


def _child_env(tmp_path, **kwargs) -> dict:
    import json

    log = tmp_path / "child.log"
    child.run(
        [sys.executable, "-c", PROBE],
        cwd=tmp_path,
        log=log,
        timeout=60,
        **kwargs,
    )
    return json.loads(log.read_text())


def test_the_c_environ_is_not_visible_to_python(tmp_path):
    """The premise. If this ever fails, the rest of the file is moot."""
    import readline  # noqa: F401

    assert os.environ.get("COLUMNS") is None
    assert os.environ.get("LINES") is None


def test_a_child_does_not_inherit_what_readline_set(tmp_path):
    """#288's six differentials, in one assertion.

    Before the fix this failed with COLUMNS=80 / LINES=24, because `child.run`
    built an explicit environment only when it had something to merge and
    otherwise let the child inherit the C environ.
    """
    import readline  # noqa: F401

    got = _child_env(tmp_path)
    assert got == {"COLUMNS": None, "LINES": None}, (
        "the child inherited COLUMNS/LINES from the C environ -- readline set "
        "them behind Python's back and this child was spawned with env=None"
    )


def test_an_explicit_env_still_merges_and_unset_still_removes(tmp_path):
    """The fix must not break the contract `unitctl.start` depends on."""
    os.environ["X288_KEEP"] = "kept"
    try:
        import json

        log = tmp_path / "c.log"
        child.run(
            [
                sys.executable,
                "-c",
                (
                    "import os,json,sys;sys.stdout.write(json.dumps("
                    "{k: os.environ.get(k) for k in "
                    "('X288_KEEP','X288_ADDED','X288_GONE')}))"
                ),
            ],
            cwd=tmp_path,
            log=log,
            timeout=60,
            env={"X288_ADDED": "added", "X288_GONE": "doomed"},
            unset=["X288_GONE"],
        )
        got = json.loads(log.read_text())
        assert got["X288_KEEP"] == "kept", "inherited keys must survive"
        assert got["X288_ADDED"] == "added", "env= must merge"
        assert got["X288_GONE"] is None, "unset= must remove"
    finally:
        os.environ.pop("X288_KEEP", None)
