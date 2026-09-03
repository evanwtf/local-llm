"""Read this Mac's die temperatures, with a timestamp, without sudo.

Written because a benchmark's numbers moved 8-11% across three sweeps and the
only available explanation was "the machine got hot" -- which was a guess. A
run that drifts needs a temperature next to it, or the drift stays a story.

`powermetrics` gives this but needs root, and this project runs unattended.
The IOKit HID thermal sensors are readable by any user: 52 of them on an
M5 Max, exposed as `PrimaryUsagePage 0xff00 / PrimaryUsage 5` services whose
`kIOHIDEventTypeTemperature` field carries degrees Celsius.

    uv run python scripts/thermals.py                 # one reading
    uv run python scripts/thermals.py --watch 300     # every 300s until killed
    uv run python scripts/thermals.py --json

Sensor names come from the SMC and are not documented by Apple. `tdie*` are
die sensors and are what this reports; `tcal` is a calibration reference and
reads ~15 C high, so it is excluded rather than averaged in. The absolute
values are less useful than the trend during one run.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import logging
import pathlib
import statistics
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


def summarise(sensors: list[tuple[str, float]]) -> dict[str, float | int]:
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


def reading() -> dict[str, float | int | str]:
    """One timestamped reading. The clock is the system clock, always."""
    got = summarise(read_sensors())
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
        if not got.get("sensors"):
            logger.error("no thermal sensors readable")
            return got
        if args.json:
            logger.info(json.dumps(got))
        else:
            logger.info(
                "%s  die max %.2f C  mean %.2f C  (%d sensors)",
                got["local"],
                got["die_max_c"],
                got["die_mean_c"],
                got["sensors"],
            )
        return got

    first = emit()
    if not args.watch:
        return 0 if first.get("sensors") else 1
    try:
        while True:
            time.sleep(args.watch)
            emit()
    except KeyboardInterrupt:
        logger.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
