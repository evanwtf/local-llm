"""Derive a machine's results-directory name from the machine itself.

A hand-typed directory name can disagree with the hardware it claims to
describe, and nothing would catch it. This reads the machine -- `sysctl` and
`system_profiler` on macOS, `/proc`, `lscpu`, `dmidecode` and `nvidia-smi` on
Linux -- and prints the canonical name.

    uv run python scripts/hardware_id.py
    uv run python scripts/hardware_id.py --json

Naming rules live in `hardware/README.md`. Versions never appear in the name:
a directory that renames itself on a driver update breaks every link to it.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import subprocess
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import provenance

logger = logging.getLogger(__name__)

# Sizes DIMMs actually ship in. The OS reports usable memory -- 30.5 GiB on a
# 32 GB box, because firmware and the iGPU take a slice -- and the sticker
# number is what someone comparing two machines will have.
STANDARD_GB = (4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512)


def _run(argv: list[str]) -> str:
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def installed_memory_gb(usable_bytes: int) -> int:
    """Round usable memory up to the size that is actually installed.

    Never rounds down: a machine reporting 30.5 GiB has 32 GB in its slots, and
    calling it 24 would name a machine that does not exist.
    """
    # Binary, not decimal. RAM is sold and labelled in GiB even though the unit
    # says GB: 137,438,953,472 bytes is exactly 128, and dividing by 1000**3
    # gives 137.4, which rounds up to the next standard size and names a machine
    # that does not exist.
    usable_gb = usable_bytes / 1024**3
    for size in STANDARD_GB:
        if usable_gb <= size * 1.02:  # 2% for firmware reservation
            return size
    return round(usable_gb)


def path_safe(name: str) -> str:
    """Anything not `A-Za-z0-9-_.` becomes an underscore.

    A whitelist, not a blacklist: vendors put arbitrary text in model strings
    and the next surprise will not be a slash. "Ryzen 7 PRO 8845HS w/ Radeon
    780M Graphics" is the one that bit -- its "w/" did not fail, it silently
    made a nested path and every run on that machine died with
    FileNotFoundError.

    `-` and `_` are kept because they are OUR separators: `-` joins the parts
    of a name and `_` already stands in for the slash in Apple's model number.
    Replacing them would rename the two directories that hold our data.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def normalise_cpu(raw: str) -> str:
    """A CPU's marketing string down to the part that identifies it.

    `AMD Ryzen 9 7900X 12-Core Processor` -> `Ryzen9-7900X`
    `Intel(R) Core(TM) i9-13900K CPU @ 3.00GHz` -> `Corei9-13900K`
    `Apple M5 Max` -> `M5-Max`
    """
    s = raw.strip()
    s = re.sub(r"\((R|TM)\)", "", s)
    s = re.sub(r"\b\d+-Core Processor\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bCPU\b.*$", "", s)
    s = re.sub(r"\bProcessor\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(AMD|Intel|Apple)\s+", "", s, flags=re.IGNORECASE)
    # "Ryzen 7 PRO 8845HS w/ Radeon 780M Graphics" -- the integrated-GPU
    # suffix is not part of the CPU's identity, and its "w/" put a FORWARD
    # SLASH into a directory name, which silently became a nested path and
    # broke every run on that machine. The Apple branch already swapped "/"
    # for "_" in the model number; nothing did it for CPU strings.
    s = re.sub(r"\s+w/\s+.*$", "", s, flags=re.IGNORECASE)
    s = s.strip()
    # "Ryzen 9 7900X" -> "Ryzen9-7900X"; "Core i9-13900K" -> "Corei9-13900K"
    s = re.sub(r"\b(Ryzen|Core)\s+(\w+)", r"\1\2", s)
    return re.sub(r"\s+", "-", s.strip()).strip("-")


def normalise_gpu(raw: str) -> str:
    """`NVIDIA GeForce RTX 3080 Ti` -> `RTX3080Ti`."""
    s = re.sub(r"^(NVIDIA|AMD|Intel)\s+", "", raw.strip(), flags=re.IGNORECASE)
    s = re.sub(r"^(GeForce|Radeon|Arc)\s+", "", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", "", s.strip())


def _darwin() -> dict:
    prof = _run(["system_profiler", "SPHardwareDataType"])
    got = {}
    for key, field in (
        ("Model Name", "model_name"),
        ("Model Identifier", "model_identifier"),
        ("Model Number", "model_number"),
        ("Chip", "chip"),
    ):
        m = re.search(rf"^\s*{key}:\s*(.+)$", prof, re.MULTILINE)
        if m:
            got[field] = m.group(1).strip()
    mem = _run(["sysctl", "-n", "hw.memsize"]).strip()
    got["memory_gb"] = installed_memory_gb(int(mem)) if mem.isdigit() else None
    return got


def _linux() -> dict:
    got = {}
    cpuinfo = pathlib.Path("/proc/cpuinfo")
    if cpuinfo.exists():
        m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo.read_text(), re.MULTILINE)
        if m:
            got["cpu"] = m.group(1).strip()
    if not got.get("cpu"):
        m = re.search(r"^Model name:\s*(.+)$", _run(["lscpu"]), re.MULTILINE)
        if m:
            got["cpu"] = m.group(1).strip()

    meminfo = pathlib.Path("/proc/meminfo")
    if meminfo.exists():
        m = re.search(r"^MemTotal:\s+(\d+) kB", meminfo.read_text(), re.MULTILINE)
        if m:
            got["memory_gb"] = installed_memory_gb(int(m.group(1)) * 1024)
    # dmidecode is exact where it is readable, and needs root where it is not.
    dmi = _run(["dmidecode", "-t", "memory"])
    sizes = [int(x) for x in re.findall(r"^\s+Size:\s+(\d+) GB", dmi, re.MULTILINE)]
    if sizes:
        got["memory_gb"] = sum(sizes)

    gpu = _run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]
    ).strip()
    if gpu:
        name, _, vram = gpu.splitlines()[0].partition(",")
        got["gpu"] = name.strip()
        vm = re.search(r"(\d+)", vram)
        if vm:
            got["vram_gb"] = round(int(vm.group(1)) / 1024)
    else:
        m = re.search(
            r"product:\s*(.+)$", _run(["lshw", "-C", "display"]), re.MULTILINE
        )
        if m:
            got["gpu"] = m.group(1).strip()
    return got


def directory_name(facts: dict, platform: str) -> str:
    """The canonical results-directory name for these facts."""
    if platform == "darwin":
        parts = [
            facts.get("model_name", "Mac").replace(" ", "-"),
            normalise_cpu(facts.get("chip", "")),
            f"{facts['memory_gb']}GB" if facts.get("memory_gb") else None,
            # Apple ships one model number per configuration, which is the only
            # thing that separates SKUs sharing a chip and a memory size.
            (facts.get("model_number") or "").replace("/", "_") or None,
        ]
    else:
        parts = [
            normalise_cpu(facts.get("cpu", "")),
            f"{facts['memory_gb']}GB" if facts.get("memory_gb") else None,
            normalise_gpu(facts.get("gpu", "")),
            f"{facts['vram_gb']}GB" if facts.get("vram_gb") else None,
        ]
    name = "-".join(p for p in parts if p)
    if not name:
        raise SystemExit("could not identify this machine; refusing to guess")
    return path_safe(name)


def short_slug(facts: dict, platform: str) -> str:
    """A compact machine token for log lines and filenames.

    The full directory name is right for a directory and too long for every
    line of output. This has to survive being copied out of context: a line
    reading `[abc1234]` could have come from either machine, and a run on
    DeepSeek on the MacBook must never be mistakable for ornith on the Linux
    box with a 3080 Ti.

        M5-Max-128GB          Ryzen9-7900X-RTX3080Ti
    """
    if platform == "darwin":
        chip = normalise_cpu(facts.get("chip", "")) or "Mac"
        mem = f"-{facts['memory_gb']}GB" if facts.get("memory_gb") else ""
        return f"{chip}{mem}"
    cpu = normalise_cpu(facts.get("cpu", "")) or "cpu"
    gpu = normalise_gpu(facts.get("gpu", ""))
    return path_safe(f"{cpu}-{gpu}" if gpu else cpu)


def facts_for_this_machine() -> tuple[dict, str]:
    """(facts, platform) for the machine running this process."""
    return (_darwin() if sys.platform == "darwin" else _linux()), sys.platform


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true", help="print the facts too")
    args = p.parse_args()
    provenance.configure()
    log_file = provenance.tee("hardware-id", machine_specific=True)
    provenance.banner(logger, engines=False)

    facts = _darwin() if sys.platform == "darwin" else _linux()
    name = directory_name(facts, sys.platform)
    if args.json:
        logger.info(json.dumps({"directory": name, "facts": facts}, indent=2))
    else:
        logger.info(name)
    logger.info("log: %s", log_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
