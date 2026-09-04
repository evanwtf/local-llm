"""Structural facts about a solution, for the ceiling problem (#4).

The oracle is binary and stays that way. When seven of eight backends score
100%, pass/fail has stopped separating them, and #4 asks for something that
distinguishes "a solution that passes tests while being O(n^2) or leaking a
file handle" from one that does not.

These are **proxies, deliberately named as such**, not a quality score:

* `max_loop_depth` -- nested `for`/`while` depth. Two nested loops over the
  same input is the usual shape of an accidental O(n^2), and it is visible in
  the syntax. It is not a complexity analysis: a nested loop over a fixed
  3-element constant is depth 2 and costs nothing.
* `open_without_with` -- calls to `open()` that are not the context expression
  of a `with`. The common file-handle leak, and the one CPython will not warn
  about until the object is collected.
* `branches` -- `if`/`elif`/`and`/`or`/`except`/comprehension conditions. A
  cyclomatic proxy. More branches is not worse code; a large *difference*
  between two solutions to the same excision is worth looking at.
* `bare_except` -- `except:` with no type, which swallows `KeyboardInterrupt`.
* `returns`, `statements` -- size, so the others can be read against it.

Nothing here is a verdict and nothing feeds `results.verdict()`. The point is
that two solutions which both pass get different numbers, so a human can ask
why. A rubric invented in this repo would be a judgement, and the harness's
claim is that it does not judge.

Parsing is by `ast`, so it sees the code as Python does rather than by regex,
and a solution that does not parse yields `{}` rather than a guess.
"""

from __future__ import annotations

import ast
import logging

logger = logging.getLogger(__name__)

BRANCHING = (ast.If, ast.IfExp, ast.ExceptHandler, ast.Assert)
LOOPS = (ast.For, ast.AsyncFor, ast.While)


def _loop_depth(node: ast.AST, depth: int = 0) -> int:
    """Deepest nesting of loops anywhere under `node`."""
    best = depth
    for child in ast.iter_child_nodes(node):
        step = depth + 1 if isinstance(child, LOOPS) else depth
        best = max(best, _loop_depth(child, step))
    return best


def _open_without_with(tree: ast.AST) -> int:
    """`open()` calls that are not the context expression of a `with`."""
    managed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                managed.add(id(item.context_expr))
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name == "open" and id(node) not in managed:
            count += 1
    return count


def shape(source: str) -> dict[str, int]:
    """Structural proxies for one piece of Python. `{}` if it does not parse.

    A solution that does not parse is not scored zero -- zero would sort
    alongside a genuinely simple solution. An absent measurement must not be
    readable as a good one.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        logger.debug("cannot parse solution: %s", exc)
        return {}
    branches = sum(1 for n in ast.walk(tree) if isinstance(n, BRANCHING))
    branches += sum(
        len(n.values) - 1 for n in ast.walk(tree) if isinstance(n, ast.BoolOp)
    )
    branches += sum(
        len(c.ifs) for n in ast.walk(tree) for c in getattr(n, "generators", [])
    )
    return {
        "max_loop_depth": _loop_depth(tree),
        "branches": branches,
        "open_without_with": _open_without_with(tree),
        "bare_except": sum(
            1
            for n in ast.walk(tree)
            if isinstance(n, ast.ExceptHandler) and n.type is None
        ),
        "returns": sum(1 for n in ast.walk(tree) if isinstance(n, ast.Return)),
        "statements": sum(1 for n in ast.walk(tree) if isinstance(n, ast.stmt)),
    }


def shape_of_patch(patch: str) -> dict[str, int]:
    """Shape of the lines a patch ADDS, which is the agent's contribution.

    Reads `+` lines from a unified diff and parses them alone. Added lines are
    rarely a valid module on their own -- a function body without its `def`
    will not parse -- so the body is retried wrapped in a synthetic function
    before giving up.
    """
    added = [
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    if not added:
        return {}
    text = "\n".join(added)
    got = shape(text)
    if got:
        return got
    indented = "\n".join("    " + line for line in added)
    return shape(f"def _wrapper():\n{indented}\n")
