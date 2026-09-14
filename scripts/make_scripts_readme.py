"""Generate scripts/README.md from each script's own first docstring line.

The index also carries a **platform** column, because this project runs three
machines (M5 Max / Metal, DGX Spark / CUDA, Ryzen+RTX) and "does a script for
this already exist, and can I run it here?" is the question the index has to
answer. Platform is the machine a script must *execute* on:

- `mac`    -- drives the ds4 Metal engine, ds4-bench/ds4-server, or reads this
             Mac's sensors (thermals, fans, the monitord series). It shells out
             to a Metal build and does nothing anywhere else.
- `nvidia` -- drives vLLM / CUDA / `nvidia-smi` on the DGX Spark (GB10).
- `any`    -- runs anywhere: ledger and results analysis, git and release
             tooling, the sweeps, transcript readers, machine-agnostic infra.
             A readout that only parses files is `any` even when the files came
             off one machine -- the script itself runs on any of them.

Only the platform-specific scripts are listed below; everything else defaults
to `any`. When you add a script that must run on one machine, add it to `MAC`
or `NVIDIA` -- `tests/test_make_scripts_readme.py` fails on a name that names no
file, and refuses a README that has drifted from this generator.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

#: Scripts that only run on the M5 Max: the ds4 Metal engine, ds4-bench /
#: ds4-server, or this Mac's own sensors.
MAC = frozenset(
    {
        "bitexact_ab.py",
        "calibrate_settle.py",
        "check_metal_equivalence.py",
        "coherence_check.py",
        "decode_ab.py",
        "decode_ab_engine.py",
        "decode_ab_repeat.py",
        "decode_ab_stack.py",
        "degeneration_cascade_run.py",
        "disk_kv_mechanism.py",
        "ds4_serve.py",
        "ds4-fast.sh",
        "ds4-vanilla.sh",
        "eval_trace.py",
        "fan_ab.py",
        "fan_ab_collate.py",
        "fan_ab_report.py",
        "greedy_mtp_ab.py",
        "install-metal-ceiling.sh",
        "kv_prefix_audit.py",
        "kv_prefix_reuse.py",
        "load_matrix.py",
        "metal_knob_ab.py",
        "moe_tile_ab.py",
        "mtp_draft_audit.py",
        "mtp_engagement.py",
        "mtp_log_split.py",
        "mtp_recovery_attribution.py",
        "mtp_replay_probe.py",
        "mtp_treatment_gate.py",
        "prefill_chunk_ab.py",
        "prefix_stability.py",
        "prefix_stall.py",
        "qwen38_metal_suites.py",
        "route_ab_report.py",
        "route_agent_ab.py",
        "sensor_windows.py",
        "stack_agent_ab.py",
        "stack_agent_report.py",
        "stack_agent_report_191.py",
        "strip_toggle_ab.py",
        "thermals.py",
    }
)

#: Scripts that only run on the DGX Spark: vLLM / CUDA / the GB10 memory pool.
NVIDIA = frozenset(
    {
        "gpu_utilization.py",
        "memory_gate.py",
        "vllm_load.py",
    }
)


def platform_for(name: str) -> str:
    """The machine a script must run on; `any` unless it is platform-specific."""
    if name in MAC:
        return "mac"
    if name in NVIDIA:
        return "nvidia"
    return "any"


def first_doc_line(path: pathlib.Path) -> str:
    """The script's own one-line summary, from its docstring or `#` header."""
    if path.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(path.read_text())) or ""
        except SyntaxError:
            doc = ""
        return doc.strip().splitlines()[0] if doc.strip() else ""
    lines = [ln for ln in path.read_text().splitlines()[:6] if ln.startswith("#")]
    body = [ln.lstrip("# ").strip() for ln in lines if not ln.startswith("#!")]
    return body[0] if body else ""


def script_paths() -> list[pathlib.Path]:
    """Every indexed script, in the order the README lists them."""
    return sorted(SCRIPTS.glob("*.py")) + sorted(SCRIPTS.glob("*.sh"))


def render() -> str:
    """The full README text, so a test can compare without writing the file."""
    intro = (
        "One line per script, taken from its own docstring so this file cannot "
        "drift into describing something the script no longer does. The "
        "**platform** column is the machine a script must run on -- `any` unless "
        "it drives one machine's engine or sensors. Regenerate with:"
    )
    check = (
        "Each script explains itself in full at the top of its own file -- what "
        "it computes, and why that and not a neighbouring thing. This is an "
        "index, not documentation. **Check it before writing analysis code**: "
        "the tool you need may already be here (`gguf_meta.py` reads GGUF "
        "metadata without loading the model; a heredoc that re-derives it is the "
        "drift this index exists to prevent)."
    )
    out = [
        "# scripts/",
        "",
        intro,
        "",
        "    uv run python scripts/make_scripts_readme.py",
        "",
        check,
        "",
        "| script | platform | what it does |",
        "|---|---|---|",
    ]
    for path in script_paths():
        first = first_doc_line(path) or "(no description)"
        out.append(f"| `{path.name}` | {platform_for(path.name)} | {first} |")
    out.append("")
    return "\n".join(out) + "\n"


def main() -> None:
    text = render()
    (SCRIPTS / "README.md").write_text(text)
    print(f"indexed {len(script_paths())} scripts")


if __name__ == "__main__":
    main()
