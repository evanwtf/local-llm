"""The generated tables name the engine build behind a row (#192).

`engine_caveat` is the read-out side of the engine identity recorded on each
row: when a table's rows span more than one engine build, the reader must be
told, the way `client_caveat` names a client split (#137).
"""

from __future__ import annotations

import gen_tables


def _row(backend: str, engine: str, version: str) -> dict:
    return {
        "backend": backend,
        "client_version": "1.18.27",
        "servers": {backend: {"engine_name": engine, "engine_version": version}},
    }


def test_one_engine_build_means_no_caveat():
    rows = [_row("a", "ds4", "ffd85d42"), _row("b", "ds4", "ffd85d42")]
    assert gen_tables.engine_caveat(rows) == []


def test_two_engine_builds_are_named():
    rows = [_row("a", "ds4", "ffd85d42"), _row("b", "llama.cpp", "2092353c8")]
    out = gen_tables.engine_caveat(rows)
    assert len(out) == 2
    assert "not all taken under one engine build" in out[1]
    assert "ds4 ffd85d42" in out[1]
    assert "llama.cpp 2092353c8" in out[1]


def test_a_row_with_no_engine_version_is_ignored():
    rows = [_row("a", "ds4", "ffd85d42"), {"backend": "b", "servers": {}}]
    assert gen_tables.engine_caveat(rows) == []
