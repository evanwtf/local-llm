"""Tests for the preflight check.

This parses `ps` and `lsof` output, which is exactly the kind of code that
fails silently: a format drift makes every check pass vacuously and the warning
that should have fired never does. The samples below are real output captured
on this machine on 2026-08-28, with a GLM llama-server holding 77.6 GiB.
"""

from __future__ import annotations

import preflight

# `ps -eo pid,rss,command`. RSS is KiB on macOS. The header is present and must
# be skipped; the command column contains spaces and must not be split on them.
PS = """\
  PID    RSS COMMAND
43967 81330176 ./build/bin/llama-server --model /Users/e/models/GLM-5.3-Flash-GGUF/x.gguf -c 65536
44957   9184 /opt/homebrew/.../Python.app/Contents/MacOS/Python shim.py --port 11501
  501   4096 /sbin/launchd
83210 2097152 ollama serve
"""

# `lsof -nP -iTCP -sTCP:LISTEN`. The name column is truncated to 9 characters.
LSOF = """\
COMMAND     PID   USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
llama-ser 43967      e   28u  IPv4 0x1234      0t0    TCP 127.0.0.1:8030 (LISTEN)
Python    44957      e    6u  IPv4 0x5678      0t0    TCP 127.0.0.1:11501 (LISTEN)
ollama    83210      e    9u  IPv4 0x9abc      0t0    TCP 127.0.0.1:11434 (LISTEN)
rapportd    701      e    4u  IPv4 0xdef0      0t0    TCP *:49152 (LISTEN)
"""


def test_only_inference_processes_are_reported():
    got = {p.pid: p for p in preflight.parse_ps(PS)}
    assert set(got) == {43967, 83210}, "launchd and the shim are not inference"


def test_resident_memory_is_converted_from_kib_to_gib():
    got = {p.pid: p for p in preflight.parse_ps(PS)}
    assert round(got[43967].rss_gib, 1) == 77.6
    assert round(got[83210].rss_gib, 1) == 2.0


def test_the_command_line_survives_its_spaces():
    got = {p.pid: p for p in preflight.parse_ps(PS)}
    assert "GLM-5.3-Flash" in got[43967].command
    assert got[43967].command.endswith("-c 65536")


def test_a_header_only_listing_yields_nothing_rather_than_crashing():
    assert preflight.parse_ps("  PID    RSS COMMAND\n") == []
    assert preflight.parse_ps("") == []


def test_listening_ports_are_read_off_lsof():
    got = preflight.parse_lsof(LSOF)
    assert got[8030] == 43967
    assert got[11501] == 44957
    assert got[11434] == 83210


def test_a_wildcard_listener_is_still_a_port():
    """`*:49152` has no address to split on the way `127.0.0.1:8030` does."""
    assert preflight.parse_lsof(LSOF)[49152] == 701


def test_backend_ports_come_from_the_selected_backends_only():
    backends = {
        "glm53": {"base_url": "http://127.0.0.1:11501"},
        "ds4": {"base_url": "http://127.0.0.1:8000"},
        "opus5": {},  # hosted; no port at all
    }
    assert preflight.backend_ports(backends) == {11501, 8000}


def test_a_props_url_port_counts_as_expected_too():
    """A backend behind the shim names the real server; both ports are ours."""
    backends = {
        "glm53": {
            "base_url": "http://127.0.0.1:11501",
            "props_url": "http://127.0.0.1:8030",
        }
    }
    assert preflight.backend_ports(backends) == {11501, 8030}


# --- the check itself -----------------------------------------------------


def test_a_server_on_an_unselected_port_is_flagged_with_its_memory():
    """The case that prompted this: GLM left up, holding 77.6 GiB, unused."""
    got = preflight.check(PS, LSOF, expected_ports={8000})
    assert [p.pid for p in got.stale] == [43967]
    assert round(got.stale[0].rss_gib, 1) == 77.6


def test_an_idle_daemon_on_an_unselected_port_is_not_worth_a_warning():
    """Ollama sits at ~2 GiB with nothing loaded and is usually up on purpose.

    Warning about it every run would make the warning routine, and a routine
    warning is one nobody reads. It still counts toward the memory total.
    """
    got = preflight.check(PS, LSOF, expected_ports={8000})
    assert 83210 not in [p.pid for p in got.stale]
    assert round(got.total_gib, 1) == 79.6


def test_the_same_daemon_is_flagged_once_it_has_a_model_loaded():
    """The threshold is about resident weights, not about which process it is."""
    loaded = PS.replace("83210 2097152 ollama serve", "83210 62914560 ollama serve")
    got = preflight.check(loaded, LSOF, expected_ports={8000})
    assert 83210 in [p.pid for p in got.stale]


def test_a_server_that_is_a_selected_backend_is_not_stale():
    got = preflight.check(PS, LSOF, expected_ports={8030, 11434})
    assert got.stale == []


def test_total_resident_counts_every_inference_process_not_just_stale_ones():
    got = preflight.check(PS, LSOF, expected_ports={8030})
    assert round(got.total_gib, 1) == 79.6  # 77.6 GLM + 2.0 ollama


def test_headroom_is_what_is_left_under_the_metal_ceiling():
    got = preflight.check(PS, LSOF, expected_ports={8030}, ceiling_gib=112.0)
    assert round(got.headroom_gib, 1) == 32.4


def test_a_shell_that_merely_mentions_a_server_is_not_a_server():
    """The self-match trap. `pgrep -f` has bitten this project once already."""
    ps = "  PID    RSS COMMAND\n87535   9184 /bin/zsh -c grep llama-server /var/log/x\n"
    assert preflight.parse_ps(ps) == []


def test_an_inference_process_with_no_listener_is_still_counted():
    """A server still loading has not bound its port yet. It holds memory now."""
    ps = "  PID    RSS COMMAND\n99 52428800 ./build/bin/llama-server --model x.gguf\n"
    got = preflight.check(
        ps, "COMMAND PID USER FD TYPE DEVICE SIZE NODE NAME\n", expected_ports={8000}
    )
    assert round(got.total_gib, 1) == 50.0
    # No port, so it cannot be matched to a backend -- report it, do not guess.
    assert got.stale == [] and len(got.unmatched) == 1


def test_a_clean_machine_produces_no_warnings():
    empty_ps = "  PID    RSS COMMAND\n  501   4096 /sbin/launchd\n"
    got = preflight.check(empty_ps, LSOF, expected_ports={8000})
    assert got.total_gib == 0.0
    assert got.warnings() == []


def test_the_warning_names_the_pid_and_the_memory_so_it_can_be_acted_on():
    got = preflight.check(PS, LSOF, expected_ports={8000})
    text = " ".join(got.warnings())
    assert "43967" in text and "77.6" in text


def test_standalone_use_judges_nothing_stale():
    """`expected_ports=None` means no run was planned, so nothing conflicts.

    An empty set would be a different claim -- "a run is planned and uses no
    ports" -- and would mark every healthy server stale. Running the tool by
    hand warned about ds4-server while ds4-server was the intended backend.
    """
    got = preflight.check(PS, LSOF, expected_ports=None)
    assert got.stale == []
    assert round(got.total_gib, 1) == 79.6  # still reports what is held


def test_an_empty_set_still_means_everything_is_unexpected():
    """The distinction must survive: a planned run with no ports is a conflict."""
    assert len(preflight.check(PS, LSOF, expected_ports=set()).stale) == 1


# --- the Metal ceiling is machine state that does not survive a reboot ------


def test_a_raised_ceiling_is_read_from_the_sysctl():
    """`iogpu.wired_limit_mb` reports the override in MB, or 0 for the default."""
    assert preflight.parse_wired_limit("iogpu.wired_limit_mb: 114688") == 112.0


def test_an_unset_limit_reports_none_not_zero_gib():
    """0 means "no override", not "no memory". Reporting 0.0 GiB would be a lie."""
    assert preflight.parse_wired_limit("iogpu.wired_limit_mb: 0") is None
    assert preflight.parse_wired_limit("") is None
    assert preflight.parse_wired_limit("sysctl: unknown oid") is None


def test_the_ceiling_falls_back_to_the_stock_default_when_unset():
    """Measured on this machine at 107.52 GiB before #30's sysctl was applied.

    preflight hardcoded 112.0, which is only true *because* the sysctl is set --
    and until 2026-09-01 it did not survive a reboot. On a fresh boot the old constant would
    have overstated headroom by 4.5 GiB, which is the difference between a
    model fitting and ds4 planning a working set the device cannot honour
    (antirez/ds4#890).
    """
    assert preflight.ceiling_gib("iogpu.wired_limit_mb: 114688") == 112.0
    assert (
        preflight.ceiling_gib("iogpu.wired_limit_mb: 0") == preflight.STOCK_CEILING_GIB
    )
    assert preflight.STOCK_CEILING_GIB == 107.52
