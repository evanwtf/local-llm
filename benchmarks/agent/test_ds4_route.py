"""Which Metal route served a row (#149).

ds4's Metal 4 tensor route is not a flag we set and forget. On any device
whose name contains M5 it **enables itself**, both #138 arms ran it, and it
flips the first sampled token on long prompts -- so two rows taken on
different routes are not comparable, and nothing on the row said which one
produced it. The #138 arms were confirmed same-route by hand, once, by a
person who thought to check.

The record is written by whoever starts the server and read when a row is
written. The rule it has to obey is that it must **never guess**: a stale
record from yesterday's server, or a server somebody started outside the
harness, has to read as `unrecorded` rather than as a route.
"""

from __future__ import annotations

import json
import pathlib

import ds4_route

FAST_LOG = """\
ds4-server starting
Metal 4 tensor API enabled for Tensor kernels
loading weights
"""

VANILLA_LOG = """\
ds4-server starting
Metal 4 tensor API available but not enabled (set DS4_METAL_ENABLE_TENSOR=1)
loading weights
"""


def test_the_fast_route_is_read_off_its_own_line():
    assert ds4_route.route_from_log(FAST_LOG) == "fast"


def test_the_vanilla_route_is_read_off_its_own_line():
    assert ds4_route.route_from_log(VANILLA_LOG) == "vanilla"


def test_a_log_with_no_route_line_is_unknown_not_a_default():
    """A server still starting has printed nothing yet. That is not vanilla."""
    assert ds4_route.route_from_log("ds4-server starting\n") is None
    assert ds4_route.route_from_log("") is None


def test_a_log_carrying_both_lines_is_unknown():
    """Two servers appending to one file, or a restart. Refuse to pick."""
    assert ds4_route.route_from_log(FAST_LOG + VANILLA_LOG) is None


def test_a_record_round_trips(tmp_path):
    path = tmp_path / "route.json"
    ds4_route.write_record(path, mode="fast", port=8000, pid=4242, log="/tmp/x.log")
    got = ds4_route.read_record(path)
    assert got["mode"] == "fast"
    assert got["port"] == 8000 and got["pid"] == 4242
    assert got["started_at"], "a record with no timestamp cannot be judged stale"


def test_a_missing_record_reads_as_none_rather_than_raising(tmp_path):
    assert ds4_route.read_record(tmp_path / "absent.json") is None


def test_a_corrupt_record_reads_as_none(tmp_path):
    """A truncated write during a crash must not take a run down."""
    path = tmp_path / "route.json"
    path.write_text('{"mode": "fa')
    assert ds4_route.read_record(path) is None


def test_the_route_is_reported_when_the_recorded_process_is_the_live_one(tmp_path):
    path = tmp_path / "route.json"
    ds4_route.write_record(path, mode="fast", port=8000, pid=4242, log="/tmp/x.log")
    got = ds4_route.route_for(8000, record_path=path, live_pid=lambda port: 4242)
    assert got == "fast"


def test_a_record_for_a_dead_server_does_not_stamp_the_row(tmp_path):
    """The failure this is really guarding.

    Yesterday's record plus today's server started by hand equals a row that
    confidently names the wrong route -- worse than an absent field, because
    nobody would go and check it.
    """
    path = tmp_path / "route.json"
    ds4_route.write_record(path, mode="fast", port=8000, pid=4242, log="/tmp/x.log")
    got = ds4_route.route_for(8000, record_path=path, live_pid=lambda port: 9999)
    assert got == "unrecorded"


def test_a_record_for_another_port_does_not_stamp_the_row(tmp_path):
    path = tmp_path / "route.json"
    ds4_route.write_record(path, mode="fast", port=8000, pid=4242, log="/tmp/x.log")
    got = ds4_route.route_for(8001, record_path=path, live_pid=lambda port: 4242)
    assert got == "unrecorded"


def test_no_record_at_all_reads_as_unrecorded(tmp_path):
    got = ds4_route.route_for(
        8000, record_path=tmp_path / "absent.json", live_pid=lambda port: 4242
    )
    assert got == "unrecorded"


def test_nothing_listening_reads_as_unrecorded(tmp_path):
    path = tmp_path / "route.json"
    ds4_route.write_record(path, mode="fast", port=8000, pid=4242, log="/tmp/x.log")
    got = ds4_route.route_for(8000, record_path=path, live_pid=lambda port: None)
    assert got == "unrecorded"


def test_recording_from_a_log_reads_the_route_out_of_it(tmp_path):
    """The path the shell runners use: they have a log, not a mode."""
    log = tmp_path / "server.log"
    log.write_text(FAST_LOG)
    path = tmp_path / "route.json"
    assert ds4_route.record_from_log(log, port=8000, pid=4242, record_path=path)
    assert ds4_route.read_record(path)["mode"] == "fast"


def test_recording_from_a_log_with_no_route_line_writes_nothing(tmp_path):
    """Better no record than a record that had to guess."""
    log = tmp_path / "server.log"
    log.write_text("ds4-server starting\n")
    path = tmp_path / "route.json"
    assert not ds4_route.record_from_log(log, port=8000, pid=4242, record_path=path)
    assert not path.exists()


def test_the_markers_come_from_ds4_serve_so_there_is_one_owner():
    """Two copies of the marker strings would drift, and the drift is silent."""
    import sys

    sys.path.insert(0, str(pathlib.Path(ds4_route.__file__).parent.parent.parent / "scripts"))
    import ds4_serve

    assert ds4_route.MARKERS is ds4_serve.MARKERS


def test_the_record_is_json_a_person_can_read(tmp_path):
    path = tmp_path / "route.json"
    ds4_route.write_record(path, mode="vanilla", port=8000, pid=1, log="/tmp/x.log")
    assert json.loads(path.read_text())["mode"] == "vanilla"


# ------------------------------------------------------- the row it lands on
#
# The module above is only useful if a row carries what it reports. These
# drive run.py's env builder with a fake route lookup, because the real one
# needs a server.


def _env(monkeypatch, backends, routes):
    import run

    monkeypatch.setattr(ds4_route, "route_for", lambda port, **kw: routes.get(port, "unrecorded"))
    monkeypatch.setattr(run.ds4_route, "route_for", lambda port, **kw: routes.get(port, "unrecorded"))
    monkeypatch.setattr(run, "probe_server", lambda b: {"served_model_id": "x"})
    monkeypatch.setattr(run, "probe_ollama", lambda b: {})
    monkeypatch.setattr(run, "probe_openai_models", lambda b: {})
    return run.capture_versions({"base_commit": "abc"}, backends)


def test_the_route_reaches_the_row(monkeypatch):
    got = _env(
        monkeypatch,
        {"ds4": {"base_url": "http://127.0.0.1:8000", "model": "x"}},
        {8000: "fast"},
    )
    assert got["metal_route"] == "fast"
    assert got["servers"]["ds4"]["metal_route"] == "fast"


def test_an_unrecorded_route_is_written_down_as_unrecorded(monkeypatch):
    """The honest answer for a server the harness did not start."""
    got = _env(
        monkeypatch,
        {"ds4": {"base_url": "http://127.0.0.1:8000", "model": "x"}},
        {},
    )
    assert got["servers"]["ds4"]["metal_route"] == "unrecorded"
    assert "metal_route" not in got, "a run-level route must not be invented"


def test_two_routes_in_one_run_void_the_comparison(monkeypatch):
    """#137's two client versions in one cell, in a different column."""
    got = _env(
        monkeypatch,
        {
            "a": {"base_url": "http://127.0.0.1:8000", "model": "x"},
            "b": {"base_url": "http://127.0.0.1:8001", "model": "x"},
        },
        {8000: "fast", 8001: "vanilla"},
    )
    assert got["metal_route"] == "MIXED"


def test_a_hosted_backend_with_no_port_is_not_asked_for_a_route(monkeypatch):
    got = _env(monkeypatch, {"hosted": {"model": "opus"}}, {})
    assert "metal_route" not in got
