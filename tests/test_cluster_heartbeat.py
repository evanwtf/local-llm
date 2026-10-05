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
disk_total_b=3940000000000
fans=3600,5400
earlyoom=active
disk_free_b=1267400000000
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
        disk_free_gb=1267.4,
        disk_total_gb=2000.0,
        fans=[2142.0],
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
    assert n.disk_free_gb == pytest.approx(1267.4)  # decimal GB, as df -B1 counts
    assert n.disk_total_gb == pytest.approx(3940.0)
    assert n.fans == [3600, 5400]  # the head's two nvfanread fans, 2026-09-30
    assert (n.earlyoom, n.links_up, n.links_total, n.roce_active) == ("active", 4, 4, 4)


def test_parse_probe_missing_fields_stay_none():
    n = hb.parse_probe("gpu=\nearlyoom=\nfans=\n")
    assert n.power_w is None and n.mem_gib is None and n.earlyoom is None
    assert n.fans == []  # node B before its nvfanread driver
    assert "fans n/a" in hb.node_header("worker", n)


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


def test_render_opens_with_the_time_and_names_both_nodes():
    body = render(
        {
            "issue": 711,
            "task": "#711 (glm), 3/30",
            "next": "x",
            "updated": NOW.isoformat(),
        }
    )
    lines = body.splitlines()
    # the operator's order: time first, then the task (2026-09-30)
    assert lines[0] == "**2026-09-24 09:40** — DGX cluster"
    assert lines[2] == "- **Task:** #711 (glm), 3/30 · on GPU: vllm pid 12, 104 GiB"
    assert (
        "- **Sensors:** **head** 51 W, 60 °C, fans 2,100 rpm, 13.9 GiB avail"
        " · **worker** 44 W" in body
    )
    assert "util" not in body  # utilization is not a heartbeat field
    # both outlets and their sum, always (opener §3a)
    assert "**outlet** 152 W + 143 W = 295 W pair (30-min peaks 188 / 182 W)" in body
    assert "RoCE 4/4 ACTIVE, RTT 0.43 ms" in body
    order = [
        "- **Task:**",
        "- **Sensors:**",
        "- **Disk:**",
        "- **PRs:**",
        "- **Next:** x",
        "- **Questions for you:**",
    ]
    positions = [body.index(o) for o in order]
    assert positions == sorted(positions)


def test_the_2026_09_30_idle_pair_reads_as_idle():
    """10 W and 14 W is the idle floor. The old 10 W line called it working."""
    assert hb.gpus_idle(node(10), node(14)) is True
    assert "idle floor" in hb.power_reason(node(10), node(14), "idle")
    assert hb.gpus_idle(node(45), node(14)) is False


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
    assert "last updated its task 90 min ago" in render({"issue": 711, "updated": old})
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


def test_render_shows_disk_free_and_flags_it_below_600_gb():
    body = render({"issue": 711})
    assert "- **Disk:** head 1,267 GB (63% free) · worker 1,267 GB (63% free)" in body
    assert "Disk critical" not in body
    w = node(44)
    w.disk_free_gb = 598.0
    low = render({"issue": 711}, worker=w)
    assert "worker 598 GB (30% free) **(CRITICAL)**" in low
    assert "**Disk critical:** worker below 600 GB free" in low


def test_a_quiet_log_on_the_head_is_flagged():
    """The quiet-log check reaches the cluster too (Codex review, #851)."""
    busy = {"issue": 840, "task": "#840 serving", "updated": NOW.isoformat()}
    args = (NOW, node(51), node(44), 0.43, {}, "vllm pid 12", busy, None)
    assert "Quiet log" in hb.render(*args, log_age_min=25)
    assert "Quiet log" not in hb.render(*args, log_age_min=5)


# #884: NVIDIA's nvidia-nvme-interrupt-coalescing.service is masked on both
# nodes (docs/dgx-spark-nvme-coalescing.md). A package upgrade or a rebuilt node
# could bring 0x107 back silently; the heartbeat is where that must show.
def test_parse_probe_reads_nvme_coalescing():
    assert hb.parse_probe("nvme_coalescing=00000000\n").nvme_coalescing == 0
    assert hb.parse_probe("nvme_coalescing=0x00000107\n").nvme_coalescing == 0x107
    # No passwordless sudo, or no nvme-cli: unknown, not "off".
    assert hb.parse_probe("nvme_coalescing=\n").nvme_coalescing is None


def test_render_flags_nvme_coalescing_back_on():
    w = node(4)
    w.nvme_coalescing = 0x107
    body = render({"issue": 711}, worker=w)
    assert "**NVMe coalescing ON:** worker (0x107)" in body
    assert "docs/dgx-spark-nvme-coalescing.md" in body


def test_render_is_quiet_when_coalescing_is_off_or_unknown():
    off, unknown = node(4), node(4)
    off.nvme_coalescing = 0
    assert "NVMe coalescing" not in render({"issue": 711}, worker=off)
    assert "NVMe coalescing" not in render({"issue": 711}, worker=unknown)


def test_render_gives_the_inlet_and_each_gpu_rise_over_it():
    """Operator, 2026-10-03: the PWS indoor sensor sits at the DGX's inlet. A
    GPU reading means more as a rise over inlet air than as an absolute."""
    body = hb.render(
        NOW,
        node(51),
        node(44),
        0.43,
        {},
        "vllm pid 12, 104 GiB",
        {"updated": NOW.isoformat()},
        None,
        ambient_f=72.5,
    )
    # node() reads 60 °C; 72.5 °F is 22.5 °C, so each GPU is 37.5 °C over the inlet
    assert "**inlet** 72.5 °F (22.5 °C); GPUs +38 / +38 °C over it" in body


def test_render_inlet_is_na_when_the_sensor_is_silent():
    body = render({"updated": NOW.isoformat()})
    assert "**inlet** n/a" in body


# --- half a cluster is not an idle cluster (review) --------------------------


def test_an_unreachable_worker_does_not_make_the_pair_idle():
    """The head at 10 W says nothing about a worker nobody could read.

    Both functions used to drop the unreachable node and then judge the head
    alone, so the heartbeat said "Both GPUs read the idle floor" and the idle
    bookkeeping started its clock on half an observation.
    """
    lost = hb.Node(reachable=False)
    assert hb.gpus_idle(node(10), lost) is None
    assert hb.gpus_idle(lost, node(10)) is None
    reason = hb.power_reason(node(10), lost, "idle")
    assert "Both GPUs" not in reason
    assert "worker" in reason and "unreachable" in reason
    assert "head" in reason and "10 W" in reason


def test_a_missing_power_read_on_either_node_is_unknown():
    blind = node(10)
    blind.power_w = None
    assert hb.gpus_idle(node(10), blind) is None
    assert "Both GPUs" not in hb.power_reason(node(10), blind, "idle")
