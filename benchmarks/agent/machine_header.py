"""The machine header: firmware and OS facts, once per ledger segment (#305).

A row names the software that produced it. Nothing named the firmware, and a
row is the wrong place for it: the same bytes on thousands of rows, for facts
that change a few times a year. So run.py writes one record line,

    {"record": "machine-header", "v": 1, "at": ..., "machine": ..., "facts": {...}}

when a ledger has none, and again when the facts change. `results.load()`
skips it (step 1, PR #880); `results.header(path, row)` returns the header in
force at a row.

`facts` has no schema. Each platform records what it can answer, and a fact
that does not apply is absent, never null.
"""

from __future__ import annotations

import json
import logging
import pathlib
import platform
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import results

logger = logging.getLogger(__name__)

HEADER_VERSION = 1

#: A command's stdout, or None when it failed or is absent.
Runner = Callable[[list[str]], str | None]
#: A file's text, or None when it is absent or unreadable.
Reader = Callable[[str], str | None]


def _run(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 and r.stdout.strip() else None


def _read(path: str) -> str | None:
    try:
        return pathlib.Path(path).read_text(errors="replace")
    except OSError:
        return None


def _clean(value: str | None) -> str | None:
    """A probe's value, or None for an empty or "[N/A]" answer."""
    if value is None:
        return None
    value = value.strip()
    return None if not value or value in {"[N/A]", "N/A"} else value


def _release_field(text: str | None, key: str) -> str | None:
    """The last `KEY="value"` in an os-release style file.

    The last, because an OTA update appends its own lines below the factory
    ones in /etc/dgx-release.
    """
    found = re.findall(rf'^{key}="?([^"\n]*)"?$', text or "", re.MULTILINE)
    return _clean(found[-1]) if found else None


def linux_facts(run: Runner = _run, read: Reader = _read) -> dict[str, Any]:
    """Firmware and platform facts a Linux machine answers without root.

    The DGX Spark's list is the one hardware/Cortex-X925-128GB-GB10/VERSIONS.md
    took by hand. The serial number in /etc/dgx-release is never read.
    """
    facts: dict[str, Any] = {"kernel": platform.release()}
    gpu = run(
        [
            "nvidia-smi",
            "--query-gpu=driver_version,vbios_version",
            "--format=csv,noheader",
        ]
    )
    if gpu:
        parts = [p.strip() for p in gpu.splitlines()[0].split(",")]
        facts["nvidia_driver"] = _clean(parts[0])
        facts["vbios"] = _clean(parts[1]) if len(parts) > 1 else None
    smi = run(["nvidia-smi"]) or ""
    if m := re.search(r"CUDA Version:\s*([0-9.]+)", smi):
        facts["cuda_driver_api"] = m.group(1)
    query = run(["nvidia-smi", "-q"]) or ""
    if m := re.search(r"GSP Firmware Version\s*:\s*(\S+)", query):
        facts["gsp_firmware"] = _clean(m.group(1))
    facts["system_firmware"] = _clean(read("/sys/class/dmi/id/bios_version"))
    facts["cpu_governor"] = _clean(
        read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    )
    facts["nvme_firmware"] = _clean(read("/sys/class/nvme/nvme0/firmware_rev"))
    dgx = read("/etc/dgx-release")
    facts["dgx_os"] = _release_field(dgx, "DGX_SWBUILD_VERSION")
    facts["dgx_ota"] = _release_field(dgx, "DGX_OTA_VERSION")
    return {k: v for k, v in facts.items() if v is not None}


def darwin_facts(run: Runner = _run) -> dict[str, Any]:
    """The layer under `sw_vers` on a Mac: the build, firmware and GPU limit."""
    probes = {
        "macos": ["sw_vers", "-productVersion"],
        "macos_build": ["sw_vers", "-buildVersion"],
        "chip": ["sysctl", "-n", "machdep.cpu.brand_string"],
        "iogpu_wired_limit_mb": ["sysctl", "-n", "iogpu.wired_limit_mb"],
    }
    facts: dict[str, Any] = {}
    for key, cmd in probes.items():
        got = run(cmd)
        if (value := _clean(got.splitlines()[0] if got else None)) is not None:
            facts[key] = int(value) if value.isdigit() else value
    hw = run(["system_profiler", "SPHardwareDataType"])
    if hw and (m := re.search(r"System Firmware Version:\s*(.+)", hw)):
        facts["system_firmware"] = _clean(m.group(1))
    return {k: v for k, v in facts.items() if v is not None}


def collect(platform_name: str = sys.platform) -> dict[str, Any]:
    """This machine's facts. Empty on a platform with no collector."""
    if platform_name == "darwin":
        return darwin_facts()
    if platform_name.startswith("linux"):
        return linux_facts()
    return {}


def changed(last: dict[str, Any] | None, facts: dict[str, Any]) -> bool:
    """Whether `facts` starts a new segment after the header `last`.

    A key both name with different values is a change, and so is a key only
    `facts` names. A key only `last` names is not: a probe that failed once
    (an `nvidia-smi` hiccup) must not fabricate a firmware boundary.
    """
    if last is None:
        return True
    before = last.get("facts") or {}
    return any(k not in before or before[k] != v for k, v in facts.items())


def record(machine: str, facts: dict[str, Any], at: str) -> dict[str, Any]:
    return {
        results.RECORD_KEY: results.MACHINE_HEADER,
        "v": HEADER_VERSION,
        "at": at,
        "machine": machine,
        "facts": facts,
    }


def ensure(
    path: pathlib.Path,
    machine: str,
    facts: dict[str, Any],
    at: str,
    allow_other_machine: bool = False,
) -> dict[str, Any] | None:
    """Append a header to `path` if it starts a new segment; return it, or None.

    Under the ledger lock, so no row lands between the read of the last
    header and the write of the new one. Refuses (SystemExit) when the last
    header names another machine: one file, one machine (#20). With
    `allow_other_machine` (run.py's --allow-foreign-hardware) the new machine
    starts its own segment instead.
    """
    if not facts:
        return None
    path = pathlib.Path(path)
    with results.ledger_lock(path):
        last = results.header(path)
        named = last.get("machine") if last is not None else None
        other = last is not None and named != machine
        if other and not allow_other_machine:
            raise SystemExit(
                f"{path}: its last machine header names {named!r}, "
                f"but this machine is {machine!r}. One file, one machine (#20, "
                "#305): point --results at this machine's ledger."
            )
        if not other and not changed(last, facts):
            return None
        new = record(machine, facts, at)
        if problems := results.header_problems(new):
            raise SystemExit(f"machine header: {'; '.join(problems)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(new) + "\n")
    logger.info("machine header written to %s (#305)", path)
    return new
