"""scripts/cluster_heartbeat.py: the timer-driven cluster heartbeat (#691).

The subprocess and gh boundaries are untested; the parsers, the reason line and
the rendered body are the logic, pinned here so the heartbeat cannot silently
change shape or drop a node.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import cluster_heartbeat as hb

EDT = dt.timezone(dt.timedelta(hours=-4), "EDT")
NOW = dt.datetime(2026, 9, 24, 9, 40, tzinfo=EDT)

# Captured from the head, 2026-09-24, idle.
PROBE_IDLE = """gpu=0,3.88,39
mem_kib=121424384
earlyoom=active
links_up=4
links_total=4
roce_active=4
"""
PING = """--- 10.0.0.2 ping statistics ---
5 packets transmitted, 5 received, 0% packet loss, time 4005ms
rtt min/avg/max/mdev = 0.110/0.599/1.268/0.493 ms
"""
MACHINE_STATE = (
    "2026-09-24T09:10:35-0400 __main__ INFO Currently on GPU: idle\n"
    "2026-09-24T09:10:35-0400 __main__ INFO VERDICT FREE -- no lock is held\n"
)


def node(power: float) -> hb.Node:
    return hb.Node(
        util=90,
        power_w=power,
        temp_c=60,
        mem_gib=13.9,
        earlyoom="active",
        links_up=4,
        links_total=4,
        roce_active=4,
    )


def test_parse_probe_reads_every_field():
    n = hb.parse_probe(PROBE_IDLE)
    assert (n.util, n.power_w, n.temp_c) == (0, 3.88, 39)
    assert n.mem_gib == pytest.approx(115.8, abs=0.05)  # 121424384 KiB, a known value
    assert (n.earlyoom, n.links_up, n.links_total, n.roce_active) == ("active", 4, 4, 4)


def test_parse_probe_missing_fields_stay_none():
    n = hb.parse_probe("gpu=\nearlyoom=\n")
    assert n.power_w is None and n.mem_gib is None and n.earlyoom is None


def test_parse_rtt_takes_the_average():
    assert hb.parse_rtt(PING) == 0.599
    assert hb.parse_rtt("100% packet loss") is None


def test_parse_occupant():
    assert hb.parse_occupant(MACHINE_STATE) == "idle"
    assert hb.parse_occupant("") is None


def test_power_reason_idle_pair():
    assert "idle floor" in hb.power_reason(node(4), node(5), "idle")


def test_power_reason_resident_but_low():
    assert "loading" in hb.power_reason(node(4), node(5), "vllm pid 12, 104 GiB")


def test_power_reason_flags_asymmetry():
    assert "Asymmetric" in hb.power_reason(node(45), node(4), "vllm pid 12")


def test_power_reason_working():
    assert "working" in hb.power_reason(node(45), node(43), "vllm pid 12")


def render(state: dict, worker: hb.Node | None = None) -> str:
    outlet = {
        "wall_w": 152.0,
        "worker_wall_w": 143.0,
        "wall_peak_w": 188.0,
        "worker_wall_peak_w": 182.0,
    }
    return hb.render(
        NOW,
        node(51),
        worker or node(44),
        0.43,
        outlet,
        "vllm pid 12, 104 GiB",
        state,
        None,
    )


def test_render_opens_with_the_occupant_and_names_both_nodes():
    body = render(
        {
            "issue": 711,
            "task": "#711 (glm), 3/30",
            "next": "x",
            "updated": NOW.isoformat(),
        }
    )
    first, header = body.split("\n\n")[:2]
    assert first == "**Currently on GPU:** vllm pid 12, 104 GiB (#711)"
    assert header.startswith(
        "**09:40 EDT** — **head** util 90%, 51W, 60ºC, 13.9 GiB avail"
    )
    assert "**worker** util 90%, 44W" in header
    # both outlets and their sum, always (opener §3a)
    assert "**outlet** 152W + 143W = 295W pair" in header
    assert "RoCE 4/4 ACTIVE, RTT 0.43 ms" in header
    assert "**task** #711 (glm), 3/30" in header


def test_render_reports_an_unreachable_worker():
    w = hb.Node(reachable=False)
    body = render({"issue": 711}, worker=w)
    assert "**worker** UNREACHABLE" in body
    assert "worker unreachable" in body


def test_render_flags_earlyoom_down():
    w = hb.Node(
        util=0,
        power_w=4,
        temp_c=40,
        mem_gib=100,
        earlyoom="inactive",
        links_up=4,
        links_total=4,
        roce_active=4,
    )
    assert "worker inactive — **DOWN" in render({"issue": 711}, worker=w)


def test_render_flags_stale_notes():
    old = (NOW - dt.timedelta(minutes=90)).isoformat()
    assert "last updated 90 min ago" in render({"issue": 711, "updated": old})
    fresh = NOW.isoformat()
    assert "Stale" not in render({"issue": 711, "updated": fresh})


def test_write_state_merges_and_reads_back(tmp_path):
    p = tmp_path / "hb.json"
    hb.write_state(p, {"issue": 711, "task": "a"}, NOW)
    s = hb.write_state(p, {"task": "b"}, NOW)
    assert s == {"issue": 711, "task": "b", "updated": "2026-09-24T09:40:00-04:00"}
    assert hb.read_state(p) == s


def test_the_peer_address_is_never_rendered():
    # The post goes to a public issue; only the RTT number may appear.
    body = render({"issue": 711})
    assert "10.0." not in body and "spark-b" not in body
