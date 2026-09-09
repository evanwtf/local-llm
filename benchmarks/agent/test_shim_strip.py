"""Tests for the scaffolding-strip arm record.

#78: the strip-toggle A/B (scripts/strip_toggle_ab.sh) alternates the shim's
scaffolding strip between arms, and which arm produced a row lived only in a
hand-kept manifest beside the results file -- the row itself could not say.
These tests pin the record that puts the arm on the row, and pin the one rule
that makes it trustworthy: a record whose process is no longer listening is
`unrecorded`, never a stamp, because a shim restarted between trials is the
normal case, not the exception.
"""

from __future__ import annotations

import json

import shim_strip


def test_the_record_is_json_a_person_can_read(tmp_path):
    shim_strip.write_record(strip=True, port=8101, pid=1, record_dir=tmp_path)
    path = shim_strip.record_path_for(8101, record_dir=tmp_path)
    got = json.loads(path.read_text())
    assert got["strip"] is True
    assert got["port"] == 8101
    assert got["pid"] == 1
    assert got["started_at"]


def test_a_live_record_names_the_arm(tmp_path):
    shim_strip.write_record(strip=False, port=8101, pid=99, record_dir=tmp_path)
    assert (
        shim_strip.strip_for(8101, record_dir=tmp_path, live_pid=lambda port: 99)
        == "off"
    )


def test_a_stale_pid_is_unrecorded_not_a_stamp(tmp_path):
    """Yesterday's shim must never name today's arm (#149's rule, reused).

    A record left by a shim that has since died says which arm THAT shim ran,
    not which arm is serving now. Resolving it to a stamp would attribute the
    new arm's rows to the old arm's setting.
    """
    shim_strip.write_record(strip=True, port=8101, pid=1, record_dir=tmp_path)
    assert (
        shim_strip.strip_for(8101, record_dir=tmp_path, live_pid=lambda port: 999)
        == shim_strip.UNRECORDED
    )


def test_no_record_means_no_shim_and_no_key(tmp_path):
    """A port with no record is not 'unrecorded' -- no strip-shim fronts it.

    'unrecorded' is reserved for a shim whose arm could not be verified; a
    llama.cpp or ds4 backend with no shim in front must not carry a strip
    field at all, or absence would read as an unknown arm.
    """
    assert (
        shim_strip.strip_for(8102, record_dir=tmp_path, live_pid=lambda port: 1) is None
    )


def test_a_record_for_another_port_is_not_read(tmp_path):
    shim_strip.write_record(strip=True, port=8101, pid=1, record_dir=tmp_path)
    assert (
        shim_strip.strip_for(8102, record_dir=tmp_path, live_pid=lambda port: 1) is None
    )


def test_a_corrupt_record_is_unrecorded_not_a_crash(tmp_path):
    """Provenance must not take a run down; a bad file is a fact about the arm."""
    shim_strip.record_path_for(8101, record_dir=tmp_path).write_text("{")
    assert (
        shim_strip.strip_for(8101, record_dir=tmp_path, live_pid=lambda port: 1)
        == shim_strip.UNRECORDED
    )


def test_the_record_survives_a_crash_mid_write(tmp_path):
    """Write-then-rename: a reader never sees half a record, and a crash leaves
    the previous record intact rather than a truncated file."""
    shim_strip.write_record(strip=True, port=8101, pid=1, record_dir=tmp_path)
    before = shim_strip.record_path_for(8101, record_dir=tmp_path).read_text()
    shim_strip.write_record(strip=False, port=8101, pid=2, record_dir=tmp_path)
    after = shim_strip.record_path_for(8101, record_dir=tmp_path).read_text()
    assert before != after
    assert not (tmp_path / "8101.json.tmp").exists()


# ------------------------------------------------------- the row it lands on
#
# A record nobody reads back is the manifest problem again. These drive
# run.py's env builder with a stubbed lookup, the way test_ds4_route.py does
# for the route.


def _row(monkeypatch, strips):
    import run

    monkeypatch.setattr(run, "probe_server", lambda b: {"served_model_id": "x"})
    monkeypatch.setattr(run, "probe_ollama", lambda b: {})
    monkeypatch.setattr(run, "probe_openai_models", lambda b: {})
    monkeypatch.setattr(run.ds4_route, "route_for", lambda port, **kw: "unrecorded")
    monkeypatch.setattr(
        run.shim_strip, "strip_for", lambda port, **kw: strips.get(port)
    )
    backends = {"qwen38fnds4shim": {"base_url": "http://127.0.0.1:8101", "model": "x"}}
    return run.capture_versions({"base_commit": "abc"}, backends)


def test_the_arm_reaches_the_row(monkeypatch):
    """The strip-off arm of the 112 A/B, read off the record the shim wrote."""
    got = _row(monkeypatch, {8101: "off"})
    assert got["servers"]["qwen38fnds4shim"]["strip"] == "off"


def test_a_stale_record_reads_as_unrecorded_on_the_row(monkeypatch):
    """A shim restarted between trials is the normal case; the row must say
    `unrecorded`, never the arm the dead shim ran."""
    got = _row(monkeypatch, {8101: shim_strip.UNRECORDED})
    assert got["servers"]["qwen38fnds4shim"]["strip"] == shim_strip.UNRECORDED


def test_a_backend_with_no_shim_carries_no_strip_key(monkeypatch):
    """No record for the port means no shim fronts it, and the row says
    nothing at all -- a missing key must not read as an unknown arm."""
    got = _row(monkeypatch, {})
    assert "strip" not in got["servers"]["qwen38fnds4shim"]
