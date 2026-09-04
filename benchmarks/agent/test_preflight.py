"""Tests for the preflight check.

This parses `ps` and `lsof` output, which is exactly the kind of code that
fails silently: a format drift makes every check pass vacuously and the warning
that should have fired never does. The samples below are real output captured
on this machine on 2026-08-28, with a GLM llama-server holding 77.6 GiB.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import preflight

REPO_ROOT = pathlib.Path(preflight.__file__).resolve().parent.parent.parent

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


# --- Metal tensor API (#78) ------------------------------------------------
#
# ggml-org/llama.cpp#27461: on an M5 the tensor API probe could fail silently
# and prefill would run on general-purpose ALUs instead of the Neural
# Accelerators. One warning line during device init, then normal output.


def test_metal_tensor_reads_the_enabled_line():
    text = (
        "ggml_metal_device_init: has unified memory    = true\n"
        "ggml_metal_device_init: has bfloat            = true\n"
        "ggml_metal_device_init: has tensor            = true\n"
    )
    assert preflight.parse_metal_tensor(text) is True


def test_metal_tensor_reads_the_disabled_line():
    text = (
        "ggml_metal_device_init: - the tensor API is not supported in this "
        "environment - disabling\n"
        "ggml_metal_device_init: has tensor            = false\n"
    )
    assert preflight.parse_metal_tensor(text) is False


def test_metal_tensor_is_unknown_when_absent():
    """A missing line must not read as False.

    Absent means we could not tell -- an older binary, a non-Metal build. A
    False would send someone chasing a regression that is really a parse gap.
    """
    assert (
        preflight.parse_metal_tensor("ggml_metal_device_init: has bfloat = true\n")
        is None
    )
    assert preflight.parse_metal_tensor("") is None


def test_metal_tensor_reads_stderr_not_just_stdout(tmp_path, monkeypatch):
    """ggml logs device init to stderr.

    The first version of this check read stdout only, returned None against a
    healthy build, and would have reported "unknown" forever. Caught by running
    it against the real binary, not by the unit tests -- which is the argument
    for keeping this one.
    """
    root = tmp_path / "llama.cpp"
    binary = root / "build" / "bin" / "llama-bench"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho 'has tensor = true' >&2\n")
    binary.chmod(0o755)
    assert preflight.metal_tensor_api(root) is True


def test_metal_tensor_prefers_the_newer_build_dir(tmp_path):
    """build2/ is the current build; build/ may be a preserved older one."""
    root = tmp_path / "llama.cpp"
    for name, value in (("build", "false"), ("build2", "true")):
        binary = root / name / "bin" / "llama-bench"
        binary.parent.mkdir(parents=True)
        binary.write_text(f"#!/bin/sh\necho 'has tensor = {value}' >&2\n")
        binary.chmod(0o755)
    assert preflight.metal_tensor_api(root) is True


# --- platform honesty (#81) -----------------------------------------------


def test_no_metal_ceiling_is_reported_off_darwin(monkeypatch):
    """A fabricated number is worse than an absent one.

    On Linux the sysctl is absent and the old fallback returned the macOS
    128 GiB-host default, so preflight printed "107.5 GiB headroom under a
    107.52 GiB Metal ceiling (stock)" on a 30 GiB box. That is the confident
    kind of wrong, on the machine fact this project treats as load-bearing.
    """
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    ceiling, raised = preflight.metal_ceiling()
    assert ceiling is None
    assert raised is False


def test_the_ceiling_is_still_read_on_darwin(monkeypatch):
    monkeypatch.setattr(preflight.sys, "platform", "darwin")
    monkeypatch.setattr(
        preflight, "_capture", lambda argv: "iogpu.wired_limit_mb: 114688"
    )
    ceiling, raised = preflight.metal_ceiling()
    assert ceiling == 112.0
    assert raised is True


def test_confinement_names_what_actually_confined_the_agent(monkeypatch):
    monkeypatch.setattr(preflight.sys, "platform", "darwin")
    assert preflight.confinement() == "sandbox-exec"
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    assert preflight.confinement() == "none"


def test_log_report_does_not_print_a_ceiling_off_darwin(monkeypatch, caplog):
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    report = preflight.Report([], [], 0.0, 0.0)
    with caplog.at_level("INFO"):
        preflight.log_report(report)
    text = caplog.text
    assert "Metal ceiling" not in text or "no Metal ceiling" in text
    assert "confinement: none" in text


def test_machine_facts_describe_this_machine(monkeypatch):
    """Whatever the platform, the row must be able to name its hardware."""
    facts = preflight.machine_facts()
    assert facts["arch"]
    assert facts["os"]
    assert facts["confinement"] in {"sandbox-exec", "none"}
    assert isinstance(facts["cpu_count"], int)


def test_machine_facts_omit_the_metal_ceiling_off_darwin(monkeypatch):
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    facts = preflight.machine_facts()
    assert "metal_ceiling_gib" not in facts
    assert facts["confinement"] == "none"


def test_memory_is_read_from_proc_on_linux(monkeypatch, tmp_path):
    """Linux reports kB in /proc/meminfo; macOS reports bytes from a sysctl."""
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       32821180 kB\nMemFree: 1 kB\n")
    monkeypatch.setattr(preflight.pathlib, "Path", lambda p: meminfo)
    assert preflight.total_memory_gib() == 31.3


def test_first_match_returns_none_when_absent():
    assert preflight._first_match("a: 1\nb: 2\n", "c") is None
    assert preflight._first_match("model name\t: Ryzen", "model name") == "Ryzen"


# --- the shim's upstream is not stale (#132) -------------------------------
#
# A backend behind the tool shim names only the shim in base_url, so the
# expected ports are {8101} -- while the model lives in the ds4-server on
# :8000 upstream, holding 74.3 GiB. Preflight warned about the one process
# the run could not do without. The warning was right about the ports and
# wrong about the conclusion.

SHIM_PS = """\
  PID    RSS COMMAND
21095   9184 /opt/homebrew/.../Python.app/Contents/MacOS/Python ds4_qwen_tool_shim.py --port 8101 --upstream http://127.0.0.1:8000
 8110 77957862 ./ds4-server --model ~/models/qwen.gguf --port 8000 --mtp-draft 7
"""

SHIM_LSOF = """\
COMMAND     PID   USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
Python    21095      e   6u  IPv4 0x5678      0t0    TCP 127.0.0.1:8101 (LISTEN)
ds4-serv   8110      e   9u  IPv4 0x9abc      0t0    TCP 127.0.0.1:8000 (LISTEN)
"""


def test_the_upstream_of_a_selected_shim_is_not_stale():
    """The case the issue observed live: shim selected, server falsely stale."""
    got = preflight.check(SHIM_PS, SHIM_LSOF, expected_ports={8101})
    assert got.stale == []
    # The 74.3 GiB still counts: exempt from 'stale' is not 'invisible'.
    assert round(got.total_gib, 1) == 74.3


def test_a_shim_whose_own_port_is_not_selected_shields_nothing():
    """A leftover shim from a different run must not exempt its upstream."""
    got = preflight.check(SHIM_PS, SHIM_LSOF, expected_ports={9000})
    assert [p.pid for p in got.stale] == [8110]


def test_without_a_shim_running_the_server_is_stale_again():
    no_shim = "\n".join(
        line for line in SHIM_PS.splitlines() if "ds4_qwen_tool_shim.py" not in line
    )
    got = preflight.check(no_shim, SHIM_LSOF, expected_ports={8101})
    assert [p.pid for p in got.stale] == [8110]


def test_a_process_that_merely_mentions_the_shim_shields_nothing():
    """The self-match trap, again. Mentions without both flags parse to nothing."""
    ps = (
        "  PID    RSS COMMAND\n"
        "87535   9184 /bin/zsh -c tail -f ds4_qwen_tool_shim.log\n"
        "87536   9184 /bin/zsh -c grep ds4_qwen_tool_shim.py /var/log/x\n"
    )
    assert preflight.shim_upstream_ports(ps, {8101}) == set()


def test_shim_upstream_ports_read_both_flag_forms():
    """`--upstream=URL` and `--upstream URL` both name the port."""
    ps = (
        "  PID    RSS COMMAND\n"
        "1 9184 python ds4_qwen_tool_shim.py --port 8101"
        " --upstream http://127.0.0.1:8000\n"
        "2 9184 python ds4_qwen_tool_shim.py --port 8102"
        " --upstream=http://127.0.0.1:8001\n"
    )
    assert preflight.shim_upstream_ports(ps, {8101, 8102}) == {8000, 8001}


def test_shim_upstream_ports_need_a_port_in_the_url():
    """An upstream URL with no port cannot name one; it shields nothing."""
    ps = (
        "  PID    RSS COMMAND\n"
        "1 9184 python ds4_qwen_tool_shim.py --port 8101"
        " --upstream http://localhost\n"
    )
    assert preflight.shim_upstream_ports(ps, {8101}) == set()


# --- a red main is learned at preflight, not seventeen hours later (#129) --
#
# Both red streaks this repo has had were found by a person going looking:
# one ran 20 runs over 17 hours before anyone noticed, and the fix then took
# seven minutes. Preflight runs before every session, so the check belongs
# here. Advisory like the rest of preflight: it warns and never refuses.


def test_a_streak_counts_consecutive_reds_from_the_newest():
    assert preflight.ci_streak(["failure", "failure", "failure"]) == 3
    assert preflight.ci_streak(["failure", "failure", "success", "failure"]) == 2
    # newest first: a green run in front means there is no streak at all
    assert preflight.ci_streak(["success", "failure", "failure"]) == 0


def test_a_run_without_a_verdict_is_not_evidence_either_way():
    """In-progress and cancelled runs neither extend nor break the streak.

    A cancelled run is a deliberate stop, not a red; an in-progress run has
    no verdict yet. The reds behind them are still real.
    """
    assert preflight.ci_streak([None, "in_progress", "failure", "failure"]) == 2
    assert preflight.ci_streak(["cancelled", "failure"]) == 1
    assert preflight.ci_streak(["in_progress"]) == 0
    assert preflight.ci_streak([]) == 0


def test_a_timed_out_run_is_red():
    assert preflight.ci_streak(["timed_out", "failure"]) == 2


def test_an_unknown_conclusion_ends_the_streak_rather_than_extending_it():
    """A conclusion we do not know must not be counted as skip.

    If GitHub grows a new red conclusion, reading it as 'no verdict' would
    silence the warning; reading it as 'not red' understates the streak. The
    conservative choice is to stop counting.
    """
    assert preflight.ci_streak(["failure", "some_new_state"]) == 1


def test_the_gh_call_names_the_repo_branch_and_a_short_limit(monkeypatch):
    seen = {}

    def fake(argv):
        seen["argv"] = argv
        return '[{"conclusion": "success"}]'

    monkeypatch.setattr(preflight, "_capture", fake)
    preflight.log_ci_status()
    argv = seen["argv"]
    assert "evanwtf/local-llm" in argv
    assert argv[argv.index("--branch") + 1] == "main"
    assert argv[argv.index("--limit") + 1] == "5"


def test_two_reds_in_a_row_warn_at_preflight(caplog, monkeypatch):
    reds = json.dumps([{"conclusion": "failure"}, {"conclusion": "timed_out"}])
    monkeypatch.setattr(preflight, "_capture", lambda argv: reds)
    with caplog.at_level("INFO"):
        preflight.log_ci_status()
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    text = " ".join(r.message for r in caplog.records)
    assert "2" in text and "RED" in text


def test_one_red_is_info_not_a_warning(caplog, monkeypatch):
    """One red can be a flake. A flake that shouts is noise by the time the
    real streak lands, and a warning nobody reads is worth less than no
    warning at all."""
    one_red = json.dumps([{"conclusion": "failure"}, {"conclusion": "success"}])
    monkeypatch.setattr(preflight, "_capture", lambda argv: one_red)
    with caplog.at_level("INFO"):
        preflight.log_ci_status()
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("1" in r.message for r in caplog.records)


def test_green_ci_says_so_and_claims_nothing_more(caplog, monkeypatch):
    """'No red' is the claim; 'green' would vouch for runs we did not see."""
    green = json.dumps([{"conclusion": "success"}] * 5)
    monkeypatch.setattr(preflight, "_capture", lambda argv: green)
    with caplog.at_level("INFO"):
        preflight.log_ci_status()
    text = " ".join(r.message for r in caplog.records)
    assert "no red" in text


def test_gh_that_answers_garbage_is_info_not_an_error(caplog, monkeypatch):
    monkeypatch.setattr(preflight, "_capture", lambda argv: "not json")
    with caplog.at_level("INFO"):
        preflight.log_ci_status()  # must not raise
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    assert any("could not" in r.message for r in caplog.records)


def test_a_missing_gh_is_info_not_a_crash(caplog, monkeypatch):
    def raise_fnfe(argv):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(preflight, "_capture", raise_fnfe)
    with caplog.at_level("INFO"):
        preflight.log_ci_status()  # must not raise
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_offline_skips_the_gh_call_entirely(caplog, monkeypatch):
    def explode(argv):
        raise AssertionError("no network under --offline")

    monkeypatch.setattr(preflight, "_capture", explode)
    with caplog.at_level("INFO"):
        preflight.log_ci_status(offline=True)
    assert any("offline" in r.message.lower() for r in caplog.records)


def test_main_actually_calls_the_ci_check(monkeypatch):
    """The check was written, tested, and never called (#129).

    Ten passing tests proved `log_ci_status` behaved correctly and none of
    them proved it ran. That is the same failure #129 is about -- a check
    nobody invokes -- so the wiring gets its own test rather than trusting
    that a call site added once stays there.
    """
    called: list[bool] = []
    monkeypatch.setattr(preflight, "log_ci_status", lambda *a, **k: called.append(True))
    monkeypatch.setattr(preflight, "log_versions", lambda *a, **k: None)
    monkeypatch.setattr(
        preflight,
        "inspect",
        lambda *a, **k: preflight.Report(
            stale=[], unmatched=[], total_gib=128.0, headroom_gib=100.0
        ),
    )
    monkeypatch.setattr(preflight, "log_report", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["preflight", "--offline"])
    preflight.main()
    assert called, "main() ran without calling log_ci_status"


def test_no_versions_does_not_reach_the_network(monkeypatch):
    """--no-versions means servers only; it must not make a gh call."""
    called: list[bool] = []
    monkeypatch.setattr(preflight, "log_ci_status", lambda *a, **k: called.append(True))
    monkeypatch.setattr(
        preflight,
        "inspect",
        lambda *a, **k: preflight.Report(
            stale=[], unmatched=[], total_gib=128.0, headroom_gib=100.0
        ),
    )
    monkeypatch.setattr(preflight, "log_report", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["preflight", "--no-versions"])
    preflight.main()
    assert not called


def test_crossing_the_ollama_sampler_boundary_is_named(caplog):
    """#84: BEHIND on this one line nudges toward the action that silently
    changes the sampler for every future row."""
    with caplog.at_level("WARNING"):
        preflight.warn_if_ollama_upgrade_changes_the_sampler("0.33.2", "v0.33.3")
    text = " ".join(r.message for r in caplog.records)
    assert "0.33.3" in text and "#84" in text


def test_an_upgrade_that_stays_below_the_boundary_says_nothing():
    """Not every ollama upgrade changes the sampler; only this line does."""
    import logging

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    preflight.logger.addHandler(handler)
    try:
        preflight.warn_if_ollama_upgrade_changes_the_sampler("0.33.0", "0.33.2")
    finally:
        preflight.logger.removeHandler(handler)
    assert not records


def test_already_past_the_boundary_says_nothing(caplog):
    """Once both sides are past it there is no crossing left to warn about."""
    with caplog.at_level("WARNING"):
        preflight.warn_if_ollama_upgrade_changes_the_sampler("0.33.3", "0.34.0")
    assert not caplog.records


def test_an_unreadable_version_warns_about_nothing(caplog):
    with caplog.at_level("WARNING"):
        preflight.warn_if_ollama_upgrade_changes_the_sampler("nightly", "0.33.3")
        preflight.warn_if_ollama_upgrade_changes_the_sampler("0.33.2", None)
    assert not caplog.records


def test_version_tuple_reads_a_leading_v_and_an_rc_suffix():
    assert preflight._version_tuple("v0.33.3") == (0, 33, 3)
    assert preflight._version_tuple("0.33.3-rc0") == (0, 33, 3)
    assert preflight._version_tuple("") is None


# --- #133: the run lock -------------------------------------------------


def test_no_lock_file_is_free():
    assert preflight.lock_state(None, "mac", 1)[0] == "free"


def test_a_live_lock_from_another_process_is_held():
    lock = {"hostname": "mac", "pid": os.getpid(), "what": "#118 arm A"}
    state, why = preflight.lock_state(lock, "mac", os.getpid() + 1)
    assert state == "held"
    assert "#118 arm A" in why


def test_our_own_lock_is_not_an_obstacle():
    lock = {"hostname": "mac", "pid": 4242}
    assert preflight.lock_state(lock, "mac", 4242)[0] == "ours"


def test_a_dead_pid_is_stale_and_its_contents_are_reported():
    """A stale lock is never stolen silently: what it recorded is what turns
    "something crashed" into "the 03:00 arm A died"."""
    dead = 999_999
    assert not preflight._pid_alive(dead)
    lock = {"hostname": "mac", "pid": dead, "what": "#118 arm A", "started": "03:00"}
    state, why = preflight.lock_state(lock, "mac", os.getpid())
    assert state == "stale"
    assert "#118 arm A" in why and "03:00" in why


def test_a_lock_from_another_machine_is_refused_not_adopted():
    """A lock is a claim on one machine. Syncing one must not wedge the other
    tier, and must not be taken over either."""
    lock = {"hostname": "linux-box", "pid": os.getpid()}
    state, why = preflight.lock_state(lock, "mac", os.getpid())
    assert state == "foreign"
    assert "linux-box" in why


def test_a_corrupt_lock_reads_as_held_not_as_free():
    """An unparseable lock is evidence something went wrong while claiming the
    machine -- which is exactly when a second run must not start."""
    assert preflight.lock_state({"corrupt": True}, "mac", 1)[0] == "corrupt"
    assert preflight.lock_state({"hostname": "mac", "pid": "nope"}, "mac", 1)[0] == (
        "corrupt"
    )


def test_unreadable_lock_json_is_corrupt_rather_than_absent(tmp_path):
    path = tmp_path / ".run-lock.json"
    path.write_text("{not json")
    assert preflight.read_lock(path) == {"corrupt": True}


def test_a_missing_lock_file_reads_as_none(tmp_path):
    assert preflight.read_lock(tmp_path / "nothing.json") is None


def test_the_lock_is_gitignored_because_it_is_machine_local():
    """#133: syncing a lock would let one machine wedge the other."""
    ignored = (REPO_ROOT / ".gitignore").read_text()
    assert ".run-lock.json" in ignored


def test_acquiring_a_free_lock_writes_what_and_who(tmp_path):
    path = tmp_path / ".run-lock.json"
    ok, _ = preflight.acquire_lock("#118 arm A", path, hostname="mac", pid=4242)
    assert ok
    got = json.loads(path.read_text())
    assert got["what"] == "#118 arm A"
    assert got["pid"] == 4242 and got["hostname"] == "mac"


def test_a_live_foreign_lock_refuses_hard(tmp_path):
    """The one place this module refuses instead of warning. Process detection
    is inferential and stays advisory; a lock is an explicit declaration."""
    path = tmp_path / ".run-lock.json"
    path.write_text(json.dumps({"hostname": "mac", "pid": os.getpid(), "what": "x"}))
    ok, why = preflight.acquire_lock("mine", path, hostname="mac", pid=os.getpid() + 1)
    assert not ok and "cannot take the run lock" in why


def test_a_stale_lock_is_not_taken_automatically(tmp_path):
    """Recovering from a crashed run is a decision with a name on it, not a
    side effect of the next run starting."""
    path = tmp_path / ".run-lock.json"
    path.write_text(json.dumps({"hostname": "mac", "pid": 999_999, "what": "dead"}))
    ok, why = preflight.acquire_lock("mine", path, hostname="mac", pid=os.getpid())
    assert not ok
    assert "stale" in why and "dead" in why
    assert path.exists(), "a stale lock must survive so it can be seen"


def test_release_drops_our_own_lock(tmp_path):
    path = tmp_path / ".run-lock.json"
    preflight.acquire_lock("mine", path, hostname="mac", pid=4242)
    ok, _ = preflight.release_lock(path, hostname="mac", pid=4242)
    assert ok and not path.exists()


def test_release_refuses_somebody_elses_lock(tmp_path):
    path = tmp_path / ".run-lock.json"
    path.write_text(json.dumps({"hostname": "mac", "pid": os.getpid(), "what": "x"}))
    ok, why = preflight.release_lock(path, hostname="mac", pid=os.getpid() + 1)
    assert not ok and "not ours" in why
    assert path.exists()


def test_releasing_when_there_is_no_lock_is_fine(tmp_path):
    ok, _ = preflight.release_lock(tmp_path / "none.json", hostname="mac", pid=1)
    assert ok


def test_every_measuring_entry_point_takes_the_lock():
    """#133: a guard the entry points do not call is the #129 mistake again."""
    for name in (
        "decode_ab.sh",
        "decode_ab_engine.sh",
        "restart_between_trials.sh",
        "restart_between_trials_armB.sh",
    ):
        text = (REPO_ROOT / "scripts" / name).read_text()
        assert "--acquire-lock" in text, name
        assert "--release-lock" in text, name
        assert "trap " in text, f"{name} must release on exit"


def test_the_restart_scripts_hold_the_lock_across_the_whole_cycle():
    """#133: the window this lock exists for is the gap BETWEEN the runs,
    where ds4-server is deliberately down. Letting each inner run.py take and
    drop its own lock would leave exactly that gap unclaimed."""
    for name in ("restart_between_trials.sh", "restart_between_trials_armB.sh"):
        text = (REPO_ROOT / "scripts" / name).read_text()
        assert "--no-lock" in text, f"{name}: inner run.py must not re-take it"
        assert text.index("--acquire-lock") < text.index("run_trial 1"), (
            f"{name}: the lock must be held before the first cycle"
        )


def test_the_repeat_harness_captures_state_and_refuses_to_clobber():
    """#136: runs accumulate, so a completed run must never be overwritten,
    and each start state must be captured rather than hoped for."""
    text = (REPO_ROOT / "scripts" / "decode_ab_repeat.sh").read_text()
    assert "already holds CSVs, skipping" in text
    assert "thermals.py" in text
    assert "fancontrol status" in text
    assert "fancontrol max" not in text and "fancontrol set" not in text
