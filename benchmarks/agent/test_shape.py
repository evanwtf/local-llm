"""Structural proxies must distinguish two passing solutions (#4)."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import shape as shp

QUADRATIC = """
def dupes(items):
    out = []
    for a in items:
        for b in items:
            if a == b:
                out.append(a)
    return out
"""

LINEAR = """
def dupes(items):
    seen = set()
    out = []
    for a in items:
        if a in seen:
            out.append(a)
        seen.add(a)
    return out
"""


def test_two_passing_solutions_are_distinguishable():
    """#4's whole ask: same behaviour, different shape, different numbers."""
    slow, fast = shp.shape(QUADRATIC), shp.shape(LINEAR)
    assert slow["max_loop_depth"] == 2
    assert fast["max_loop_depth"] == 1
    assert slow != fast


def test_a_leaked_file_handle_is_counted():
    leaky = "def f(p):\n    fh = open(p)\n    return fh.read()\n"
    clean = "def f(p):\n    with open(p) as fh:\n        return fh.read()\n"
    assert shp.shape(leaky)["open_without_with"] == 1
    assert shp.shape(clean)["open_without_with"] == 0


def test_a_method_call_named_open_still_counts():
    """`self.open(...)` outside a with is the same leak."""
    assert (
        shp.shape("def f(s):\n    return s.open('x').read()\n")["open_without_with"]
        == 1
    )


def test_bare_except_is_counted_separately():
    got = shp.shape("def f():\n    try:\n        pass\n    except:\n        pass\n")
    assert got["bare_except"] == 1


def test_a_typed_except_is_not_bare():
    got = shp.shape(
        "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n"
    )
    assert got["bare_except"] == 0
    assert got["branches"] >= 1


def test_boolean_operators_count_as_branches():
    one = shp.shape("def f(a, b):\n    return a and b\n")["branches"]
    two = shp.shape("def f(a, b, c):\n    return a and b and c\n")["branches"]
    assert two > one


def test_comprehension_conditions_count_as_branches():
    plain = shp.shape("def f(xs):\n    return [x for x in xs]\n")["branches"]
    filtered = shp.shape("def f(xs):\n    return [x for x in xs if x]\n")["branches"]
    assert filtered == plain + 1


def test_unparseable_source_yields_nothing_not_zero():
    """Zero would sort alongside a genuinely simple solution. An absent
    measurement must not read as a good one."""
    assert shp.shape("def f(:\n  bad") == {}


def test_patch_shape_reads_only_added_lines():
    patch = (
        "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,4 @@\n"
        "-def f():\n-    pass\n"
        "+def f():\n+    for a in x:\n+        for b in y:\n+            pass\n"
    )
    assert shp.shape_of_patch(patch)["max_loop_depth"] == 2


def test_a_bare_body_is_retried_wrapped():
    """Added lines are rarely a valid module: a body without its def."""
    patch = "+++ b/x.py\n+    for a in x:\n+        pass\n"
    assert shp.shape_of_patch(patch)["max_loop_depth"] == 1


def test_an_empty_patch_yields_nothing():
    assert shp.shape_of_patch("--- a/x\n+++ b/x\n") == {}


def test_save_solution_records_shape_beside_the_patch(tmp_path, monkeypatch):
    """#4's proxies must ride with the row, not need a separate pass."""
    import subprocess

    import grade

    patch = "+++ b/x.py\n+def f(xs):\n+    for a in xs:\n+        for b in xs:\n+            pass\n"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: type(
            "R", (), {"returncode": 0, "stdout": patch, "stderr": ""}
        )(),
    )
    got = grade.save_solution(tmp_path, "t", tmp_path)
    assert got["shape_max_loop_depth"] == 2
    assert "solution_sha256" in got


def test_shape_failure_never_costs_the_trial(tmp_path, monkeypatch):
    """A measurement must not take down a run that already cost 20 minutes."""
    import subprocess

    import grade

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: type(
            "R",
            (),
            {"returncode": 0, "stdout": "+++ b/x\n+not (valid python\n", "stderr": ""},
        )(),
    )
    got = grade.save_solution(tmp_path, "t", tmp_path)
    assert "solution_sha256" in got
