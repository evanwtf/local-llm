#!/usr/bin/env python3
"""Remove a function body, leaving the signature and docstring in place.

The agent is given a real repository with one function hollowed out. Keeping
the signature and docstring means the task is "implement this contract", not
"guess what was here" -- which is the situation a coding agent actually faces.

Uses the AST to find the target, so it works on methods and nested defs and
does not care about formatting.
"""

import ast
import pathlib
from typing import NamedTuple


class TargetNotFound(Exception):
    pass


def find(tree: ast.Module, symbol: str) -> ast.FunctionDef:
    """Locate `func` or `Class.method` in a parsed module."""
    parts = symbol.split(".")
    if len(parts) == 1:
        for node in tree.body:
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == parts[0]
            ):
                return node
        raise TargetNotFound(f"no top-level function {symbol!r}")

    cls_name, func_name = parts
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for sub in node.body:
                if (
                    isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef)
                    and sub.name == func_name
                ):
                    return sub
            raise TargetNotFound(f"class {cls_name!r} has no method {func_name!r}")
    raise TargetNotFound(f"no class {cls_name!r}")


STUB = 'raise NotImplementedError("removed for benchmark")'


class _Plan(NamedTuple):
    """One excision: lines[start:end] become `replacement`; `removed` is the body."""

    start: int
    end: int
    removed: str
    replacement: str


def _chars(line: str, col_offset: int) -> int:
    """`ast` columns count UTF-8 bytes; slicing a str needs characters."""
    return len(line.encode()[:col_offset].decode())


def _plan(source: str, symbol: str, keep_docstring: bool = True) -> _Plan:
    """Locate the body of `symbol`, after any docstring, and its stub.

    With `keep_docstring=False` the docstring goes too, and the agent gets a
    signature and nothing else. See issue #4, direction 4.

    One function computes this because two callers depend on the answer being
    the same span: `excise` removes it, and `body_source` reads back what the
    agent wrote in its place. If they drifted, `restored_verbatim` would compare
    a body against a body-plus-docstring and never fire.

    A body can share a line with code before it: `def f(): return 1`, or
    `"Doc."; return 1`. Replacing that whole line used to drop the
    signature and leave an indented `raise` at module scope -- an invalid
    target (code review, 2026-10-05). Such a line keeps what precedes the body,
    and an inline suite becomes a block.
    """
    node = find(ast.parse(source), symbol)
    body = node.body
    # By default keep a leading docstring: it is the contract the agent
    # implements against. A task can ask for it to go as well.
    if (
        keep_docstring
        and body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        if len(body) == 1:
            raise TargetNotFound(f"{symbol!r} is only a docstring; nothing to remove")
        first = body[1]
    else:
        first = body[0]

    lines = source.splitlines(keepends=True)
    end = node.body[-1].end_lineno  # 1-indexed, exclusive once used as a slice
    assert end is not None
    line = lines[first.lineno - 1]
    head = line[: _chars(line, first.col_offset)]
    if not head.strip():
        # The common layout: the body starts its own line.
        start = first.lineno - 1  # 0-indexed, inclusive
        removed = "".join(lines[start:end])
        return _Plan(start, end, removed, f"{' ' * first.col_offset}{STUB}\n")

    removed = line[len(head) :] + "".join(lines[first.lineno : end])
    lead = body[0]
    lead_line = lines[lead.lineno - 1]
    margin = lead_line[: len(lead_line) - len(lead_line.lstrip())]
    if lead_line[: _chars(lead_line, lead.col_offset)].strip():
        # An inline suite, `def f(): ...`: the header keeps its line, and
        # anything kept (the docstring) moves into a block under it.
        indent = margin + "    "
        kept = [
            f"{indent}{ast.get_source_segment(source, stmt)}\n"
            for stmt in body[: body.index(first)]
        ]
        header = lead_line[: _chars(lead_line, lead.col_offset)].rstrip()
        replacement = header + "\n" + "".join(kept) + f"{indent}{STUB}\n"
        return _Plan(lead.lineno - 1, end, removed, replacement)
    # `"""Doc."""; return a`: the docstring keeps its line, the body goes.
    kept_head = head.rstrip().rstrip(";").rstrip()
    replacement = f"{kept_head}\n{margin}{STUB}\n"
    return _Plan(first.lineno - 1, end, removed, replacement)


def _span(source: str, symbol: str, keep_docstring: bool = True) -> tuple[int, int]:
    """(start, end) of the lines `excise` replaces, as `swift_excise._span`
    gives its character span: test_task_definitions checks every target
    through either one by this name."""
    plan = _plan(source, symbol, keep_docstring)
    return plan.start, plan.end


def body_source(path: pathlib.Path, symbol: str, keep_docstring: bool = True) -> str:
    """Return the body of `symbol` without changing the file.

    The read half of `excise`. Comparing this against what `excise` removed is
    how a trial reports `restored_verbatim` -- a model that reproduces the
    original body byte for byte is recalling it, not solving the task, and
    gmail-archive was written with Claude. See issue #4.
    """
    return _plan(path.read_text(), symbol, keep_docstring).removed


def excise(path: pathlib.Path, symbol: str, keep_docstring: bool = True) -> str:
    """Replace the body of `symbol` in `path`. Returns the removed source."""
    source = path.read_text()
    lines = source.splitlines(keepends=True)
    plan = _plan(source, symbol, keep_docstring)
    path.write_text(
        "".join(lines[: plan.start]) + plan.replacement + "".join(lines[plan.end :])
    )
    return plan.removed


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        raise SystemExit("usage: excise.py <file> <symbol>")
    print(excise(pathlib.Path(sys.argv[1]), sys.argv[2]))
