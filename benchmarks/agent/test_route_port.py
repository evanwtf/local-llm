"""Which port the route is looked up on, for a shim-fronted backend (#211).

`ds4_serve.py` records the Metal route against the **ds4-server's** port.
`base_url` for a shim-fronted backend is the **shim's**. Asking `route_for`
about the shim's port returns `unrecorded` by construction, and did -- for
`qwen38fnds4shim` (262 rows) and `qwen38fnds4mtp7shim` (94 rows), two of the
three largest backends in the corpus, and for all 1,646 rows we hold.

#149 measured the fast route at about **21% of agent wall time** and showed it
is not bit-exact, so this is the largest single lever on our published numbers
and a quality difference at once.

The pair of tests that matters is the last two: a declared upstream resolves,
and an undeclared one still reads `unrecorded`. The second is what stops this
being "fixed" by guessing the server's port from the shim's.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ds4_route
import run


def test_a_plain_backend_is_asked_about_its_own_port():
    assert run.route_query_port({"base_url": "http://127.0.0.1:8000"}) == 8000


def test_a_shim_backend_is_asked_about_the_engine_behind_it():
    backend = {
        "base_url": "http://127.0.0.1:8101",
        "engine_url": "http://127.0.0.1:8000",
    }
    assert run.route_query_port(backend) == 8000


def test_a_shim_without_a_declared_engine_is_asked_about_the_shim():
    # Not the server's port guessed from the shim's. The honest answer for
    # this backend is `unrecorded`, and it stays that.
    assert run.route_query_port({"base_url": "http://127.0.0.1:8101"}) == 8101


def test_a_backend_with_no_url_at_all_has_no_port():
    assert run.route_query_port({}) is None


def test_a_declared_engine_url_without_a_port_is_not_invented():
    assert run.route_query_port({"engine_url": "http://127.0.0.1"}) is None


def _record(tmp_path, **kw):
    import json

    got = {"mode": "fast", "port": 8000, "pid": 4242}
    got.update(kw)
    path = tmp_path / "route.json"
    path.write_text(json.dumps(got))
    return path


def test_a_declared_upstream_resolves_the_route(tmp_path):
    backend = {
        "base_url": "http://127.0.0.1:8101",
        "engine_url": "http://127.0.0.1:8000",
    }
    route = ds4_route.route_for(
        run.route_query_port(backend),
        record_path=_record(tmp_path),
        live_pid=lambda port: 4242,
    )
    assert route == "fast"


def test_an_undeclared_upstream_still_reads_unrecorded(tmp_path):
    backend = {"base_url": "http://127.0.0.1:8101"}
    route = ds4_route.route_for(
        run.route_query_port(backend),
        record_path=_record(tmp_path),
        live_pid=lambda port: 4242,
    )
    assert route == ds4_route.UNRECORDED


def test_the_pid_check_is_untouched_by_the_declaration(tmp_path):
    # Loosening either check was explicitly not the fix. A record left by
    # yesterday's server must still not stamp today's rows.
    backend = {
        "base_url": "http://127.0.0.1:8101",
        "engine_url": "http://127.0.0.1:8000",
    }
    route = ds4_route.route_for(
        run.route_query_port(backend),
        record_path=_record(tmp_path, pid=999),
        live_pid=lambda port: 4242,
    )
    assert route == ds4_route.UNRECORDED


def _backends():
    import pathlib
    import tomllib

    path = pathlib.Path(__file__).resolve().parent / "tasks.toml"
    return tomllib.loads(path.read_text())["backend"]


def test_every_shim_fronted_ds4_backend_declares_its_engine():
    # A ds4 backend whose base_url is not the server's own port is behind a
    # shim, and without `engine_url` its rows cannot name the route. This is
    # the assertion that keeps a newly added shim backend from silently
    # rejoining the 1,646 rows that carry no route at all.
    missing = [
        name
        for name, b in _backends().items()
        if b.get("engine") == "ds4"
        and run.route_query_port(b) != 8000
        and not b.get("engine_url")
    ]
    assert missing == []


def test_a_declared_engine_url_names_a_port_the_route_record_can_hold():
    for name, b in _backends().items():
        if declared := b.get("engine_url"):
            assert run.route_query_port(b) is not None, name
            assert declared != b.get("base_url"), name
