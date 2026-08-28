"""Remove a function body, leaving the signature and docstring in place.

The agent is given a real repository with one function hollowed out. Keeping
the signature and docstring means the task is "implement this contract", not
"guess what was here" -- which is the situation a coding agent actually faces.

Uses the AST to find the target, so it works on methods and nested defs and
does not care about formatting.
"""
import ast
import pathlib


class TargetNotFound(Exception):
    pass


def find(tree: ast.Module, symbol: str) -> ast.FunctionDef:
    """Locate `func` or `Class.method` in a parsed module."""
    parts = symbol.split(".")
    if len(parts) == 1:
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == parts[0]:
                return node
        raise TargetNotFound(f"no top-level function {symbol!r}")

    cls_name, func_name = parts
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef | ast.AsyncFunctionDef) and sub.name == func_name:
                    return sub
            raise TargetNotFound(f"class {cls_name!r} has no method {func_name!r}")
    raise TargetNotFound(f"no class {cls_name!r}")


def _span(source: str, symbol: str,
          keep_docstring: bool = True) -> tuple[int, int, str]:
    """Locate the body of `symbol`, after any docstring.

    With `keep_docstring=False` the docstring goes too, and the agent gets a
    signature and nothing else. See issue #4, direction 4.

    Returns (start, end, indent) as a slice over `source.splitlines(True)`.
    One function computes this because two callers depend on the answer being
    the same span: `excise` removes it, and `body_source` reads back what the
    agent wrote in its place. If they drifted, `restored_verbatim` would compare
    a body against a body-plus-docstring and never fire.
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

    start = first.lineno - 1                      # 0-indexed, inclusive
    end = node.body[-1].end_lineno                # 1-indexed, exclusive once used as a slice
    return start, end, " " * first.col_offset


def body_source(path: pathlib.Path, symbol: str,
                keep_docstring: bool = True) -> str:
    """Return the body of `symbol` without changing the file.

    The read half of `excise`. Comparing this against what `excise` removed is
    how a trial reports `restored_verbatim` -- a model that reproduces the
    original body byte for byte is recalling it, not solving the task, and
    gmail-archive was written with Claude. See issue #4.
    """
    source = path.read_text()
    start, end, _ = _span(source, symbol, keep_docstring)
    return "".join(source.splitlines(keepends=True)[start:end])


def excise(path: pathlib.Path, symbol: str, keep_docstring: bool = True) -> str:
    """Replace the body of `symbol` in `path`. Returns the removed source."""
    source = path.read_text()
    lines = source.splitlines(keepends=True)
    start, end, indent = _span(source, symbol, keep_docstring)

    removed = "".join(lines[start:end])
    stub = f'{indent}raise NotImplementedError("removed for benchmark")\n'
    path.write_text("".join(lines[:start]) + stub + "".join(lines[end:]))
    return removed


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        raise SystemExit("usage: excise.py <file> <symbol>")
    print(excise(pathlib.Path(sys.argv[1]), sys.argv[2]))
