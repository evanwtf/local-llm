"""Read this machine's temperatures, with a timestamp, without sudo.

Written because a benchmark's numbers moved 8-11% across three sweeps and the
only available explanation was "the machine got hot" -- which was a guess. A
run that drifts needs a temperature next to it, or the drift stays a story.

Two sources, whichever the machine has (#326):

- **Mac**: `powermetrics` gives die temperatures but needs root, and this
  project runs unattended. The IOKit HID thermal sensors are readable by any
  user: 52 of them on an M5 Max, exposed as `PrimaryUsagePage 0xff00 /
  PrimaryUsage 5` services whose `kIOHIDEventTypeTemperature` field carries
  degrees Celsius. `tdie*` are die sensors and are what this reports; `tcal`
  is a calibration reference that reads ~15 C high, so it is excluded.
- **Nvidia (DGX Spark, RTX 3080 Ti)**: `nvidia-smi` reports GPU temperature,
  power, and clocks without root. Both boxes throttle under a sustained sweep,
  which is exactly what a long agent campaign produces.

`reading()` merges whichever applies, so a caller gets a comparable timestamped
dict on any machine. The absolute values matter less than the trend in one run.

    uv run python scripts/thermals.py                 # one reading
    uv run python scripts/thermals.py --watch 300     # every 300s until killed
    uv run python scripts/thermals.py --json
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import logging
import pathlib
import shutil
import statistics
import subprocess
import sys
import time
from ctypes import c_char_p, c_double, c_int, c_uint32, c_uint64, c_void_p

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "agent")
)

import provenance

logger = logging.getLogger(__name__)

# kIOHIDEventTypeTemperature. The value field is the type shifted into the
# high half of a 32-bit field selector.
TEMPERATURE = 15
FIELD = TEMPERATURE << 16
UTF8 = 0x08000100
# Any reading outside this is a sensor we do not understand, not a hot Mac.
PLAUSIBLE = (0.0, 150.0)


def _frameworks():
    iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
    cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
    for fn, res, args in (
        ("CFStringCreateWithCString", c_void_p, [c_void_p, c_char_p, c_uint32]),
        ("CFDictionaryCreateMutable", c_void_p, [c_void_p, c_int, c_void_p, c_void_p]),
        ("CFNumberCreate", c_void_p, [c_void_p, c_int, c_void_p]),
        ("CFArrayGetCount", c_int, [c_void_p]),
        ("CFArrayGetValueAtIndex", c_void_p, [c_void_p, c_int]),
        ("CFStringGetCString", c_int, [c_void_p, c_char_p, c_int, c_uint32]),
    ):
        f = getattr(cf, fn)
        f.restype, f.argtypes = res, args
    cf.CFDictionarySetValue.argtypes = [c_void_p, c_void_p, c_void_p]
    for fn, res, args in (
        ("IOHIDEventSystemClientCreate", c_void_p, [c_void_p]),
        ("IOHIDEventSystemClientSetMatching", None, [c_void_p, c_void_p]),
        ("IOHIDEventSystemClientCopyServices", c_void_p, [c_void_p]),
        ("IOHIDServiceClientCopyProperty", c_void_p, [c_void_p, c_void_p]),
        (
            "IOHIDServiceClientCopyEvent",
            c_void_p,
            [c_void_p, c_uint64, c_int, c_uint64],
        ),
        ("IOHIDEventGetFloatValue", c_double, [c_void_p, c_uint32]),
    ):
        f = getattr(iokit, fn)
        f.restype, f.argtypes = res, args
    return iokit, cf


#: These sensors are Apple's IOKit HID services. There is no equivalent read on
#: Linux, and the Linux CI runner has neither IOKit nor CoreFoundation, so
#: `ctypes.CDLL(None)` there resolves to the process itself and every lookup
#: fails with `undefined symbol: CFStringCreateWithCString`. That crash made CI
#: red for 20 consecutive runs. Report "unsupported" instead of raising, so a
#: caller on another platform gets an empty reading with a real clock rather
#: than a traceback.
SUPPORTED = sys.platform == "darwin"


def read_sensors() -> list[tuple[str, float]]:
    """[(sensor name, celsius)] for every readable thermal sensor."""
    if not SUPPORTED:
        return []
    iokit, cf = _frameworks()

    def cfstr(s: str):
        return cf.CFStringCreateWithCString(None, s.encode(), UTF8)

    def cfnum(n: int):
        v = ctypes.c_int32(n)
        return cf.CFNumberCreate(None, 3, ctypes.byref(v))

    def tostr(ref) -> str:
        if not ref:
            return ""
        buf = ctypes.create_string_buffer(256)
        return buf.value.decode() if cf.CFStringGetCString(ref, buf, 256, UTF8) else ""

    client = iokit.IOHIDEventSystemClientCreate(None)
    if not client:
        return []
    match = cf.CFDictionaryCreateMutable(None, 0, None, None)
    cf.CFDictionarySetValue(match, cfstr("PrimaryUsagePage"), cfnum(0xFF00))
    cf.CFDictionarySetValue(match, cfstr("PrimaryUsage"), cfnum(5))
    iokit.IOHIDEventSystemClientSetMatching(client, match)
    services = iokit.IOHIDEventSystemClientCopyServices(client)
    if not services:
        return []

    out: list[tuple[str, float]] = []
    for i in range(cf.CFArrayGetCount(services)):
        svc = cf.CFArrayGetValueAtIndex(services, i)
        event = iokit.IOHIDServiceClientCopyEvent(svc, TEMPERATURE, 0, 0)
        if not event:
            continue
        value = iokit.IOHIDEventGetFloatValue(event, FIELD)
        if PLAUSIBLE[0] < value < PLAUSIBLE[1]:
            out.append(
                (
                    tostr(iokit.IOHIDServiceClientCopyProperty(svc, cfstr("Product"))),
                    value,
                )
            )
    return out


def summarize(sensors: list[tuple[str, float]]) -> dict[str, float | int]:
    """Die max/mean, and the count, from a sensor list.

    `tcal` is a calibration reference that reads about 15 C above the dies;
    averaging it in would hide a real rise behind a constant.
    """
    dies = [v for name, v in sensors if "tdie" in name.lower()]
    pool = dies or [v for _, v in sensors]
    if not pool:
        return {}
    return {
        "die_max_c": round(max(pool), 2),
        "die_mean_c": round(statistics.mean(pool), 2),
        "sensors": len(pool),
    }


def fan_rpm() -> dict[str, float | int]:
    """Fan speeds, read through `fancontrol status` (#116). Empty on failure.

    Temperature alone cannot tell a throttled run from a well-cooled one: a
    die sitting at 68 C with the fans at 3400 rpm and one at 68 C with them at
    5300 rpm are different machines. #118's run 4 recorded 48.4 C at the start
    and 67.6 C at the end and could say nothing about whether cooling was the
    reason, because no fan speed was captured alongside.

    Read only. `fancontrol max` and `set` are never called from here -- fan
    state is an operator decision, and a benchmark that quietly changes the
    machine's cooling is measuring something it did not declare.
    """
    try:
        got = subprocess.run(
            ["fancontrol", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if got.returncode != 0:
            return {}
        fans = json.loads(got.stdout).get("fans") or []
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return {}
    out: dict[str, float | int] = {}
    actual = []
    for fan in fans:
        if not isinstance(fan, dict):
            continue
        rpm = fan.get("actual_rpm")
        if isinstance(rpm, int | float):
            out[f"fan{fan.get('index', len(actual))}_rpm"] = rpm
            actual.append(rpm)
        mode = fan.get("mode")
        if isinstance(mode, str):
            out[f"fan{fan.get('index', len(actual) - 1)}_mode"] = mode  # type: ignore[assignment]
    if actual:
        out["fan_rpm_max"] = max(actual)
    return out


#: nvidia-smi reads GPU temperature, power, and clocks without root, so it is
#: the Linux/CUDA counterpart to the Mac's IOKit sensors (#326). Absent on the
#: Mac and on a Linux box with no NVIDIA GPU; `gpu_thermals()` then reads
#: nothing, the same way `read_sensors()` does off macOS.
NVIDIA = shutil.which("nvidia-smi")

#: The fields queried, in order. Positional, so the parser maps by index and
#: never depends on nvidia-smi's default column set.
_NVIDIA_QUERY = (
    "index",
    "temperature.gpu",
    "power.draw",
    "clocks.gr",
    "clocks.mem",
    "utilization.gpu",
)


def parse_nvidia_smi(text: str) -> dict[str, float | int]:
    """Per-GPU temperature/power/clocks from `nvidia-smi` CSV, plus the maxima.

    One row per GPU, `nounits`. A field a GPU cannot report prints as `[N/A]`
    and is dropped without dropping the row. The maxima are what a long sweep
    is read against: the hottest die is the one that throttles, not the first.
    """
    out: dict[str, float | int] = {}
    temps: list[float] = []
    powers: list[float] = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.strip().split(",")]
        if len(parts) != len(_NVIDIA_QUERY):
            continue
        fields = dict(zip(_NVIDIA_QUERY, parts, strict=True))

        def num(key: str, cast=float, fields=fields):
            value = fields.get(key, "")
            if not value or value.upper() == "N/A" or value.startswith("["):
                return None
            try:
                return cast(value)
            except ValueError:
                return None

        idx = num("index", int)
        if idx is None:
            continue
        temp = num("temperature.gpu")
        power = num("power.draw")
        clock = num("clocks.gr", int)
        mem_clock = num("clocks.mem", int)
        util = num("utilization.gpu", int)
        if temp is not None:
            out[f"gpu{idx}_temp_c"] = temp
            temps.append(temp)
        if power is not None:
            out[f"gpu{idx}_power_w"] = power
            powers.append(power)
        if clock is not None:
            out[f"gpu{idx}_clock_mhz"] = clock
        if mem_clock is not None:
            out[f"gpu{idx}_mem_clock_mhz"] = mem_clock
        if util is not None:
            out[f"gpu{idx}_util_pct"] = util
    if temps:
        out["gpu_temp_max_c"] = round(max(temps), 2)
    if powers:
        out["gpu_power_max_w"] = round(max(powers), 2)
    return out


def gpu_thermals() -> dict[str, float | int]:
    """One nvidia-smi reading, or empty when there is no GPU to read (#326).

    Empty on a missing tool, a non-zero exit, or unparsable output -- the same
    contract as `fan_rpm()`: never a fabricated number beside a temperature.
    """
    if not NVIDIA:
        return {}
    try:
        got = subprocess.run(
            [
                NVIDIA,
                "--query-gpu=" + ",".join(_NVIDIA_QUERY),
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if got.returncode != 0:
        return {}
    return parse_nvidia_smi(got.stdout)


def reading() -> dict[str, float | int | str]:
    """One timestamped reading. The clock is the system clock, always.

    Merges every source; each returns nothing where it does not apply, so the
    Mac contributes die sensors and fans and an Nvidia box contributes GPU
    temperature, power, and clocks, with no platform branch here.
    """
    got: dict[str, float | int | str] = dict(summarize(read_sensors()))
    got.update(fan_rpm())
    got.update(gpu_thermals())
    got["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    got["local"] = time.strftime("%H:%M:%S %Z")
    return got


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="sample every SECONDS until interrupted",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true", help="no banner (for tight loops)")
    args = p.parse_args()

    provenance.configure()
    if not args.quiet:
        provenance.banner(logger, engines=False)

    def emit() -> dict:
        got = reading()
        if not got.get("sensors") and "gpu_temp_max_c" not in got:
            logger.error("no thermal source readable (no die sensors, no nvidia-smi)")
            return got
        if args.json:
            logger.info(json.dumps(got))
        elif got.get("sensors"):
            logger.info(
                "%s  die max %.2f C  mean %.2f C  (%d sensors)",
                got["local"],
                got["die_max_c"],
                got["die_mean_c"],
                got["sensors"],
            )
        else:
            logger.info(
                "%s  gpu max %.2f C  power max %s W",
                got["local"],
                got["gpu_temp_max_c"],
                got.get("gpu_power_max_w", "?"),
            )
        return got

    def _readable(got: dict) -> bool:
        return bool(got.get("sensors")) or "gpu_temp_max_c" in got

    first = emit()
    if not args.watch:
        return 0 if _readable(first) else 1
    try:
        while True:
            time.sleep(args.watch)
            emit()
    except KeyboardInterrupt:
        logger.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
