"""Keep a test's root-logger state from leaking into another test (#1417).

pytest's `capsys` gives each test a stream that it closes afterwards. A test
that adds a root handler on that stream (directly, or through a CLI's
`logs.configure`) and does not remove it leaves a handler that raises
"I/O operation on closed file" on every later log line. Python's logging
reports that error on stderr, so a later test that reads stderr fails,
depending only on the order the suite runs in.

`isolated_root_logger()` takes the handlers that are not pytest's own off
the root logger for the test's duration, removes any such handler the test
adds, and puts back the old handlers and level afterwards.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator


def _ours(handler: logging.Handler) -> bool:
    """True for pytest's own capture handlers, which must stay."""
    return type(handler).__module__ == "_pytest.logging"


@contextlib.contextmanager
def isolated_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    level = root.level
    foreign = [h for h in root.handlers if not _ours(h)]
    for handler in foreign:
        root.removeHandler(handler)
    try:
        yield
    finally:
        for handler in [h for h in root.handlers if not _ours(h)]:
            root.removeHandler(handler)
        for handler in foreign:
            root.addHandler(handler)
        root.setLevel(level)
