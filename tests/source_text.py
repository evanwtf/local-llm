"""Read a source file the way a linter would, not the way grep does.

A test that asserts `"pgrep" not in path.read_text()` fails on the docstring
that explains why pgrep was removed. It cannot tell a call from an explanation
of a deleted call, and it pushes you toward deleting the explanation -- which
is the one part worth keeping.

I wrote that test twice in one day, in #237 and again in #236, which is why the
helper lives here instead of in either file. It is the same failure as reading
`grep -o 'MTP' | wc -l` as a drafting count: text search does not know what it
is looking at.
"""

from __future__ import annotations

import ast
import pathlib


def code_of(path: pathlib.Path) -> str:
    """`path`'s source with docstrings, comments and trailing notes removed."""
    text = path.read_text()
    tree = ast.parse(text)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and node.body
            and ast.get_docstring(node, clean=False) is not None
        ):
            first = node.body[0]
            doc_lines.update(range(first.lineno, (first.end_lineno or 0) + 1))
    keep = []
    for i, line in enumerate(text.splitlines(), 1):
        if i in doc_lines or line.strip().startswith("#"):
            continue
        keep.append(line.split("  # ")[0])
    return "\n".join(keep)
