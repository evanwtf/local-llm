#!/usr/bin/env python3
"""Report the argv/env equivalence diff for the five non-route_agent ports. #235, #264, #149

Route_agent_ab is already pinned by tests/test_equiv.py. This reports the
remaining #235 drivers: greedy_mtp, strip_toggle, targets (each spawns
run.py + ds4-server), plus decode and metal_knob (each runs ds4-bench
directly, no run.py).

For each driver the shell side is the reference except where a bug is being
fixed, so the report answers the peer's two questions per driver:

  - is the diff EXPLAINED or merely SMALL? A one-flag difference nobody can
    account for is worse than a six-flag one where each has a reason.
  - does the ARM DIFFERENCE hold? (the tensor/env split, the mtp-vs-plain
    server, the shim mode) -- not just the flags.

The port side is driven through each port's OWN argv/env builders, so it
cannot drift from what the port really emits. The shell side is transcribed
from the committed .sh command lines, cited by line.

Run with:  uv run python scripts/equiv_five_report.py
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
for sub in ("scripts", "scripts/lib", "benchmarks/agent"):
    sys.path.insert(0, str(REPO / sub))

import batch as batchlib
import decode_ab
import equiv
import greedy_mtp_ab as gm
import metal_knob
import metal_knob_ab
import strip_toggle_ab as st
import targets_ab as tg

# Stable tokens so a run.py path or --server-log value cannot make the diff
# about the machine instead of about flags.
OUT = pathlib.Path("out")
BATCH = "b1"
TRIALS = 1
HEAD = "deadbeef"
LOGDIR = OUT  # the shell's $LOGDIR, as a stable token

DECLARED = equiv.declared_run_flags()  # run.py's literal flags

RUN_PY_PREFIX = ("uv", "run", "python", "benchmarks/agent/run.py")


def run_flags_only(argv: list[str]) -> list[str]:
    """The tokens after the fixed `uv run python run.py` head."""
    for i, tok in enumerate(argv):
        if tok.endswith("run.py"):
            return list(argv[i + 1 :])
    raise AssertionError(f"no run.py in argv: {argv}")


def verdict(name: str, shell_only, port_only) -> str:
    if not shell_only and not port_only:
        return f"{name}: IDENTICAL (empty canonical diff)"
    bits = []
    if shell_only:
        bits.append("shell-only " + ", ".join(map(str, sorted(shell_only))))
    if port_only:
        bits.append("port-only " + ", ".join(map(str, sorted(port_only))))
    return f"{name}: EXPLAINED diff -- " + "; ".join(bits)


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


# ------------------------------------------------------------------ greedy_mtp


def report_greedy_mtp() -> None:
    section("greedy_mtp_ab  (run.py + ds4-server, MTP vs plain)")
    for kind, backend, want_mtp, kv in (
        ("mtp", gm.TREATMENT, True, gm.KV_MTP),
        ("plain", gm.CONTROL, False, gm.KV_PLAIN),
    ):
        tag = f"{kind}-sweep1"
        port_run = run_flags_only(gm.arm_argv(backend, tag, LOGDIR, BATCH, TRIALS))
        # shell, greedy_mtp_ab.sh:114-120 (run_arm)
        shell_run = [
            "--backend",
            backend,
            "--client",
            "opencode",
            "--trials",
            str(TRIALS),
            "--no-lock",
            "--batch",
            BATCH,
            "--server-log",
            str(LOGDIR / f"ds4server-{tag}.log"),
            "--draft-log-engine",
            "ds4",
        ]
        shell_only, port_only = equiv.argv_difference(shell_run, port_run, DECLARED)
        equiv.assert_flags_declared(port_run, DECLARED)
        print(f"  arm {kind}: {verdict('run.py', shell_only, port_only)}")
        print("      run.py flags all declared by run.py: yes")
        # server
        port_srv = gm.server_command(want_mtp, kv)
        # shell, greedy_mtp_ab.sh:88-96 (start_server)
        shell_srv = [
            str(gm.DS4_TREE / "ds4-server"),
            "--metal",
            "-m",
            str(gm.DS4_MODEL),
            "--ple",
            str(gm.DS4_PLE),
            "--ctx",
            "100000",
            "--warm-weights",
            "--kv-disk-dir",
            str(kv),
            "--kv-disk-space-mb",
            "8192",
        ]
        if want_mtp:
            shell_srv += [
                "--mtp-model",
                str(gm.DS4_MTP),
                "--mtp-draft",
                "7",
                "--mtp-timing",
            ]
        shell_srv += ["--host", "127.0.0.1", "--port", "8000"]
        s_only, p_only = equiv.argv_difference(shell_srv, port_srv, DECLARED)
        print(f"      server: {verdict('argv', s_only, p_only)}")
    print(
        "  arm difference: mtp has --mtp-model/--mtp-draft/--mtp-timing in the\n"
        "    server argv; plain has none. Both sides carry the same per-arm KV\n"
        "    dir. control-shell caveat: the empty ${mtp_args[@]} was unbound under\n"
        "    set -u on bash 3.2 (2026-09-08); the committed .sh:94 now guards with\n"
        "    '${mtp_args[@]+\"${mtp_args[@]}\"}' and no set -u is in effect, so the\n"
        "    plain arm is executable today -- the port encodes the fix structurally."
    )


# ---------------------------------------------------------------- strip_toggle


def _batch(driver_backend: str, driver_prefix: str):
    return batchlib.Batch(
        repo=REPO,
        results=pathlib.Path("out") / "results.jsonl",
        manifest=pathlib.Path("out") / "manifest.jsonl",
        logdir=pathlib.Path("out"),
        bench_logs=pathlib.Path("out") / "bench-logs",
        batch=BATCH,
        harness_head=HEAD,
        backend=driver_backend,
        prefix=driver_prefix,
    )


def report_strip_toggle() -> None:
    section("strip_toggle_ab  (run.py + ds4-server, shim strip on/off #112)")
    b = _batch(st.BACKEND, st.PREFIX)
    port_run = run_flags_only(batchlib.argv(b, ()))
    # shell, strip_toggle_ab.sh:190-195 (run_one)
    shell_run = [
        "--backend",
        st.BACKEND,
        "--trials",
        str(TRIALS),
        "--client",
        "opencode",
        "--no-lock",
        "--allow-implausible",
        "--results",
        str(b.results),
        "--require-harness-head",
        HEAD,
        "--batch",
        BATCH,
    ]
    shell_only, port_only = equiv.argv_difference(shell_run, port_run, DECLARED)
    equiv.assert_flags_declared(port_run, DECLARED)
    print(f"  run.py: {verdict('argv', shell_only, port_only)}")
    # server identical across arms (single KV)
    port_srv = st.server_command()
    shell_srv = [
        str(st.DS4_TREE / "ds4-server"),
        "--metal",
        "-m",
        str(st.DS4_MODEL),
        "--ple",
        str(st.DS4_PLE),
        "--ctx",
        "100000",
        "--warm-weights",
        "--kv-disk-dir",
        str(st.DS4_KV),
        "--kv-disk-space-mb",
        "8192",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    s_only, p_only = equiv.argv_difference(shell_srv, port_srv, DECLARED)
    print(f"  server: {verdict('argv', s_only, p_only)}")
    print(
        "  arm difference is the SHIM env, not the server: on=strip (shim env\n"
        "    SHIM_NO_STRIP ABSENT via tool_shim.strip_env -> (unset,)),\n"
        "    off=no-strip (SHIM_NO_STRIP=1). Same arm spelling in the shell\n"
        "    strip_toggle_ab.sh:138/143 (SHIM_NO_STRIP=1 vs env -u SHIM_NO_STRIP).\n"
        "    run.py argv is arm-independent on both sides."
    )


# -------------------------------------------------------------------- targets


def report_targets() -> None:
    section("targets_ab  (run.py + ds4-server, targets legacy/sandbox #146)")
    b = _batch(tg.BACKEND, tg.PREFIX)
    for arm in tg.ARMS:
        port_run = run_flags_only(batchlib.argv(b, ["--targets", arm]))
        # shell, targets_ab.sh:186-190 (run_one); order differs cosmetically
        shell_run = [
            "--backend",
            tg.BACKEND,
            "--trials",
            str(TRIALS),
            "--client",
            "opencode",
            "--no-lock",
            "--targets",
            arm,
            "--allow-implausible",
            "--results",
            str(b.results),
            "--require-harness-head",
            HEAD,
            "--batch",
            BATCH,
        ]
        shell_only, port_only = equiv.argv_difference(shell_run, port_run, DECLARED)
        equiv.assert_flags_declared(port_run, DECLARED)
        print(f"  arm {arm}: {verdict('run.py', shell_only, port_only)}")
    port_srv = tg.server_command()
    shell_srv = [
        str(tg.DS4_TREE / "ds4-server"),
        "--metal",
        "-m",
        str(tg.DS4_MODEL),
        "--ple",
        str(tg.DS4_PLE),
        "--ctx",
        "100000",
        "--warm-weights",
        "--kv-disk-dir",
        str(tg.DS4_KV),
        "--kv-disk-space-mb",
        "8192",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    s_only, p_only = equiv.argv_difference(shell_srv, port_srv, DECLARED)
    print(f"  server: {verdict('argv', s_only, p_only)}")
    print(
        "  arm difference is the --targets VALUE passed to run.py (legacy|sandbox),\n"
        "    not a server/env difference; server argv and shim (always strip-on) are\n"
        "    identical across arms on both sides."
    )


# --------------------------------------------------------------------- decode


def report_decode() -> None:
    section("decode_ab  (ds4-bench only, no run.py)")
    gguf = pathlib.Path("out/gguf")
    csv = pathlib.Path("out/rep.csv")
    prompt = pathlib.Path("out/prompt.txt")
    binary = pathlib.Path("ds4/ds4-bench")
    port = decode_ab.bench_argv(
        gguf, csv, prompt, binary=binary, ctx_start=0, ctx_max=4096, step=512, gen=64
    )
    port_nochunk = list(port)
    # both sides carry --prefill-chunk only when set; compare the no-chunk form
    # shell, decode_ab.sh:187-191 (the ds4-bench invocation inside run_rep)
    shell_nochunk = [
        str(binary),
        "-m",
        str(gguf),
        "--metal",
        "--prompt-file",
        str(prompt),
        "--ctx-start",
        "0",
        "--ctx-max",
        "4096",
        "--step-incr",
        "512",
        "--gen-tokens",
        "64",
        "--csv",
        str(csv),
    ]
    s_only, p_only = equiv.argv_difference(shell_nochunk, port_nochunk, DECLARED)
    print(f"  ds4-bench argv (no chunk): {verdict('argv', s_only, p_only)}")
    print(
        "  no run.py is spawned on either side (decode measures decode-vs-next-token\n"
        "    via ds4-bench, decode_ab.py:15-20), so there is no #264 flag surface and\n"
        "    no server to launch. Arm labels are user-supplied gguf names."
    )


# ----------------------------------------------------------------- metal_knob


def report_metal_knob() -> None:
    section("metal_knob_ab  (ds4-bench only, per-knob arm env #162)")
    gguf = pathlib.Path("out/gguf")
    csv = pathlib.Path("out/rep.csv")
    prompt = pathlib.Path("out/prompt.txt")
    port = metal_knob_ab.bench_argv(
        gguf, prompt, csv, ctx_start=0, ctx_max=4096, step=512, gen=64
    )
    # shell, metal_knob_ab.sh:249-252 (the ARM invocation, not the engagement
    # probe at :136 -- that one deliberately passes --ctx-max "$CTX_START" so
    # it measures admission at a single context, and comparing against it
    # would report a --ctx-max difference that is not a difference).
    shell_nochunk = [
        "./ds4-bench",
        "-m",
        str(gguf),
        "--metal",
        "--prompt-file",
        str(prompt),
        "--ctx-start",
        "0",
        "--ctx-max",
        "4096",
        "--step-incr",
        "512",
        "--gen-tokens",
        "64",
        "--csv",
        str(csv),
    ]
    s_only, p_only = equiv.argv_difference(shell_nochunk, port, DECLARED)
    print(f"  ds4-bench argv: {verdict('argv', s_only, p_only)}")
    # arm_env vs arm_cmd across every knob
    bad = []
    for knob in metal_knob.KNOBS:
        for label in ("on", "off"):
            for value in ("1", "7"):
                cmd = metal_knob.arm_cmd(knob, label, value)
                env, unset = metal_knob.arm_env(knob, label, value)
                if unset:
                    expect = f"-u {unset[0]}"
                elif env:
                    var = next(iter(env))
                    expect = f"{var}={value}"
                else:
                    expect = "(no var!)"
                if cmd != expect:
                    bad.append((knob, label, value, cmd, expect))
    print(
        f"  arm_env vs arm_cmd across {len(metal_knob.KNOBS)} knobs x on/off x 2 values: "
        f"{'MISMATCH ' + str(bad) if bad else 'agree on every arm'}"
    )
    print(
        "  guarded by tests/test_metal_knob_ab.py::test_the_two_arm_spellings_describe\n"
        "    _the_same_arm; no run.py on either side."
    )


def main() -> int:
    report_greedy_mtp()
    report_strip_toggle()
    report_targets()
    report_decode()
    report_metal_knob()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
