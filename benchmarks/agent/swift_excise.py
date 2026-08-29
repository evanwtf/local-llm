"""Remove a Swift function body, leaving the signature in place.

The Python side gets its spans from `ast`. Swift has no equivalent available
here, so this matches braces — and a brace scanner that does not understand
strings and comments cuts in the wrong place and leaves a file that may still
compile. That is the failure mode to engineer against: a wrong span does not
crash, it silently changes what the task is.

The interface mirrors `excise.py` deliberately, because `run.py` and `grade.py`
call both the same way and `restored_verbatim` depends on `body_source`
returning **exactly** the span `excise` removes.
"""
from __future__ import annotations

import pathlib
import re


class TargetNotFound(Exception):
    pass


# `public static func buckets(` — modifiers vary and are not worth enumerating
# beyond "words before `func`".
def _func_pattern(name: str) -> re.Pattern[str]:
    return re.compile(r"(?m)^[ \t]*(?:[\w@()]+[ \t]+)*func[ \t]+"
                      + re.escape(name) + r"\b")


def _type_pattern(name: str) -> re.Pattern[str]:
    """`enum Downsample {`, `struct X {`, `final class Y: Z {`."""
    return re.compile(r"(?m)^[ \t]*(?:[\w@()]+[ \t]+)*"
                      r"(?:enum|struct|class|actor|extension|protocol)[ \t]+"
                      + re.escape(name) + r"\b")


def _skip_to_body_open(source: str, start: int) -> int:
    """Index of the `{` that opens the body, from the start of a declaration.

    The signature can span lines and contain braces of its own only inside
    strings or comments, so the same scanner rules apply while looking for it.
    """
    i, n = start, len(source)
    while i < n:
        ch = source[i]
        if ch == "{":
            return i
        if ch == '"':
            i = _skip_string(source, i)
            continue
        if source.startswith("//", i):
            i = source.find("\n", i)
            if i == -1:
                break
            continue
        if source.startswith("/*", i):
            i = _skip_block_comment(source, i)
            continue
        i += 1
    raise TargetNotFound("no body brace found after the declaration")


def _skip_string(source: str, i: int) -> int:
    """Past a string literal, handling escapes and `\"\"\"` blocks."""
    if source.startswith('"""', i):
        end = source.find('"""', i + 3)
        return len(source) if end == -1 else end + 3
    i += 1
    while i < len(source):
        if source[i] == "\\":
            i += 2
            continue
        if source[i] == '"':
            return i + 1
        i += 1
    return i


def _skip_block_comment(source: str, i: int) -> int:
    """Past `/* ... */`. Swift block comments nest, unlike C's."""
    depth = 0
    while i < len(source):
        if source.startswith("/*", i):
            depth += 1
            i += 2
            continue
        if source.startswith("*/", i):
            depth -= 1
            i += 2
            if depth == 0:
                return i
            continue
        i += 1
    return i


def _matching_close(source: str, open_idx: int) -> int:
    """Index just past the `}` matching the `{` at `open_idx`.

    Skips strings and comments, so a `}` in `let brace = "}"` or in a comment
    does not end the body. Closures inside the body simply raise and lower the
    depth like any other braces.
    """
    depth, i, n = 0, open_idx, len(source)
    while i < n:
        ch = source[i]
        if ch == '"':
            i = _skip_string(source, i)
            continue
        if source.startswith("//", i):
            nl = source.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if source.startswith("/*", i):
            i = _skip_block_comment(source, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise TargetNotFound("unbalanced braces; body never closes")


def _doc_comment_start(source: str, decl_start: int) -> int:
    """Start of the `///` block immediately above a declaration, else decl_start."""
    lines_before = source[:decl_start].splitlines(keepends=True)
    take = 0
    for line in reversed(lines_before):
        if line.strip().startswith("///"):
            take += 1
            continue
        if not line.strip() and take:
            break
        break
    if not take:
        return decl_start
    return decl_start - sum(len(x) for x in lines_before[-take:])


def _span(source: str, symbol: str, keep_docstring: bool = True) -> tuple[int, int]:
    """(start, end) character span of what to remove for `symbol`.

    `Type.method` searches only inside that type's braces, so `Other.buckets`
    cannot silently match `Downsample.buckets`.
    """
    parts = symbol.split(".")
    region_start, region_end = 0, len(source)
    if len(parts) == 2:
        tmatch = _type_pattern(parts[0]).search(source)
        if not tmatch:
            raise TargetNotFound(f"no type {parts[0]!r}")
        region_start = _skip_to_body_open(source, tmatch.start())
        region_end = _matching_close(source, region_start)
    elif len(parts) != 1:
        raise TargetNotFound(f"cannot address {symbol!r}")

    fmatch = _func_pattern(parts[-1]).search(source, region_start, region_end)
    if not fmatch:
        raise TargetNotFound(f"no func {symbol!r}")

    open_idx = _skip_to_body_open(source, fmatch.start())
    close_idx = _matching_close(source, open_idx)
    if keep_docstring:
        # Body only: after the opening brace, up to the closing one.
        return open_idx + 1, close_idx - 1
    # Signature keeps its place; the doc comment above it goes with the body.
    return _doc_comment_start(source, fmatch.start()), close_idx - 1


def body_source(path: pathlib.Path, symbol: str,
                keep_docstring: bool = True) -> str:
    """Return what `excise` would remove, without changing the file."""
    source = path.read_text()
    start, end = _span(source, symbol, keep_docstring)
    return source[start:end]


def excise(path: pathlib.Path, symbol: str,
           keep_docstring: bool = True) -> str:
    """Replace the body of `symbol` in `path`. Returns the removed source."""
    source = path.read_text()
    start, end = _span(source, symbol, keep_docstring)
    removed = source[start:end]
    if keep_docstring:
        stub = '\n        fatalError("removed for benchmark")\n    '
        path.write_text(source[:start] + stub + source[end:])
    else:
        # The doc comment and body both went; put the signature back with a stub.
        sig_start = _func_pattern(symbol.split(".")[-1]).search(source, start)
        head = source[sig_start.start():source.index("{", sig_start.start()) + 1]
        stub = head + '\n        fatalError("removed for benchmark")\n    '
        path.write_text(source[:start] + stub + source[end:])
    return removed
