#!/usr/bin/env python3
"""Put earlyoom's configuration in the repo, and check the box still matches. #458

earlyoom is the box-wide OOM net on the DGX Spark: it SIGTERMs the largest
inference or agent process before the pool is exhausted, and never sshd. Its
configuration lived only in `/etc/default/earlyoom`, edited by hand, so the
one setting that decides whether the machine survives was untracked. This
script is that configuration, version-controlled, with a check that says when
the box has drifted from it.

**The setting this exists for.** earlyoom v1.7 acts only when memory **and**
swap are both below their thresholds -- `--help`: *"Note: both memory and swap
must be below minimum for earlyoom to act."* The original `-s 20,10` therefore
made it unfireable here: with `vm.swappiness=10` and a GPU-side allocation
ramp, swap barely drains. On 2026-09-17 at 06:48 earlyoom logged `mem avail: 0
of 124610 MiB (0.00%), swap free: 14983 of 16383 MiB (91.45%)` and killed
nothing; the box hard-locked and the smart plug had to power-cycle it. `-s
100,100` makes the swap condition always true, so free memory alone decides.

**The drop-in.** earlyoom's own `-p` cannot set its priority under the unit's
`DynamicUser` sandbox (`Could not set priority: Permission denied`), so
`Nice=-20` and `OOMScoreAdjust=-1000` go in a systemd drop-in instead: the
watcher must be scheduled ahead of the runaway and must never be the kernel's
victim.

    uv run python scripts/setup_earlyoom.py            # what differs, exit 1 if drift
    sudo -E uv run python scripts/setup_earlyoom.py --apply
    uv run python scripts/setup_earlyoom.py --print     # the files, to stdout

Applying writes `/etc/default/earlyoom` and the drop-in, backs up whatever was
there, reloads systemd and restarts the service. `--check` is what a preflight
or a heartbeat can run: it reads and never writes.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

DEFAULTS_PATH = pathlib.Path("/etc/default/earlyoom")
DROPIN_PATH = pathlib.Path("/etc/systemd/system/earlyoom.service.d/priority.conf")

#: SIGTERM at 1.0 GiB MemAvailable, SIGKILL at 0.5 GiB -- absolute sizes, in
#: KiB, for `-M` (#700, operator decision 2026-09-23). Below every other floor
#: in use, so a cleaner stop always gets the first chance: the two-node recipe
#: memguards at 1.5 GiB, and `dgx_server.py`'s watcher at 14 GiB (8 GiB for a
#: server-only run, #456, #562).
#:
#: Why so low: large two-node models idle below any percentage line that
#: leaves real runway -- GLM-5.3-Flash at 2.7-3.3 GiB on the head (#648),
#: DeepSeek V4.1 at ~4.5 GiB (#685). At the old 5% (~6.1 GiB) every such run
#: had to stop earlyoom by hand and remember to restart it; on 2026-09-23 that
#: step was missed and it SIGTERMed a GLM launch at load (#700). Disabling it
#: was rejected: it is the only net that does not depend on a recipe (#485
#: caught a 108,524 MiB runaway; the GLM recipe has no memguard at all).
#:
#: The cost: ~1 GiB of runway instead of ~6, against allocation ramps that can
#: move a GiB in under a second. Where between 0 and 1 GiB the box stops
#: answering is unmeasured (the 2026-09-17 lock logged 0%).
#:
#: History: -m 10,5 (~12 / ~6 GiB) at install, 2026-09-14; -m 5,3 from
#: 2026-09-19 when remote clients left the box holding only the server (#562);
#: -M 1048576,524288 from 2026-09-23 (#700).
MEM_THRESHOLDS_KIB = "1048576,524288"

#: 100,100 -- i.e. ignore swap. See the module docstring: the AND with swap is
#: what made this unfireable on 2026-09-17.
SWAP_THRESHOLDS = "100,100"

#: Report interval, seconds. Every minute is enough to reconstruct an incident
#: and quiet enough not to fill the journal.
REPORT_SECONDS = 60

#: Never kill the things that make the box reachable or that enforce the
#: limits. sshd first: without it there is no way in.
AVOID_REGEX = r"(^|/)(systemd|systemd-.*|sshd|dockerd|containerd|earlyoom)$"

#: Prefer the processes that can actually exhaust this pool: the inference
#: servers, and the python a trial's model-written code runs in.
PREFER_REGEX = r"(^|/)(vllm|VLLM|pt_main_thread|llama-server|ollama|python[0-9.]*)$"


def earlyoom_args() -> str:
    """The `EARLYOOM_ARGS` value, as one shell-quoted line."""
    return (
        f"-M {MEM_THRESHOLDS_KIB} -s {SWAP_THRESHOLDS} -r {REPORT_SECONDS} "
        f"--avoid '{AVOID_REGEX}' --prefer '{PREFER_REGEX}'"
    )


def defaults_file() -> str:
    """`/etc/default/earlyoom`, with the reasoning a later reader will want."""
    return f"""# Managed by scripts/setup_earlyoom.py (local-llm #458). Edit there.
#
# -s {SWAP_THRESHOLDS} means "ignore swap". earlyoom acts only when memory AND
# swap are both below their thresholds, so the original -s 20,10 made it
# unfireable on this box: with vm.swappiness=10 a GPU-side ramp does not drain
# swap. 2026-09-17 06:48: "mem avail: 0 of 124610 MiB (0.00%), swap free:
# 14983 of 16383 MiB (91.45%)" -- nothing was killed, and the box hard-locked.
#
# -M {MEM_THRESHOLDS_KIB} is KiB: SIGTERM at 1.0 GiB available, SIGKILL at
# 0.5 GiB (#700). Large two-node models idle at 2.7-4.5 GiB, so a higher line
# killed them at rest and had to be stopped by hand for every run.
EARLYOOM_ARGS="{earlyoom_args()}"
"""


def dropin_file() -> str:
    """The systemd drop-in. earlyoom's own -p cannot do this under DynamicUser."""
    return """# Managed by scripts/setup_earlyoom.py (local-llm #458). Edit there.
#
# earlyoom's -p flag fails under this unit's DynamicUser sandbox
# ("Could not set priority: Permission denied"), so systemd sets both: the
# watcher must run ahead of the runaway it is watching, and must never be the
# kernel's victim.
[Service]
Nice=-20
OOMScoreAdjust=-1000
"""


def _read(path: pathlib.Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def drift() -> list[str]:
    """Which managed files do not match this script. [] when the box agrees.

    Compares the `EARLYOOM_ARGS` line rather than the whole defaults file, so
    a comment edit is not reported as a drifted safety setting -- but the
    drop-in is compared whole, because every line in it is load-bearing.
    """
    out = []
    current = _read(DEFAULTS_PATH)
    if current is None:
        out.append(f"{DEFAULTS_PATH} is missing")
    else:
        want = f'EARLYOOM_ARGS="{earlyoom_args()}"'
        if want not in current:
            have = next(
                (
                    line.strip()
                    for line in current.splitlines()
                    if line.startswith("EARLYOOM_ARGS=")
                ),
                "(no EARLYOOM_ARGS line)",
            )
            out.append(f"{DEFAULTS_PATH}: {have}")
    dropin = _read(DROPIN_PATH)
    if dropin is None:
        out.append(f"{DROPIN_PATH} is missing")
    elif dropin.strip() != dropin_file().strip():
        out.append(f"{DROPIN_PATH} differs")
    return out


def _backup(path: pathlib.Path) -> pathlib.Path | None:
    """Copy `path` beside itself before overwriting. None when there was none.

    Dated, because the pre-#458 backup already exists under a fixed name and
    a second apply must not overwrite the one record of the original config.
    """
    if not path.exists():
        return None
    stamp = datetime.datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
    target = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, target)
    return target


def apply() -> int:
    """Write both files, reload systemd, restart earlyoom. Needs root."""
    for path, body in ((DEFAULTS_PATH, defaults_file()), (DROPIN_PATH, dropin_file())):
        path.parent.mkdir(parents=True, exist_ok=True)
        saved = _backup(path)
        if saved:
            logger.info("backed up %s -> %s", path, saved)
        path.write_text(body)
        logger.info("wrote %s", path)
    for argv in (
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", "earlyoom"],
    ):
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
        if proc.returncode:
            logger.error("%s failed: %s", " ".join(argv), proc.stderr.strip()[:200])
            return 1
    logger.warning(
        "earlyoom restarted. Verify the thresholds it logs: it must say "
        "'swap <= 100.00%%', not the old 20%%, or it cannot fire on this box; "
        "and 'mem <=  0.82%%' / '0.41%%' on a 121.7 GiB Spark (1.0 / 0.5 GiB)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--apply", action="store_true", help="write the files (needs root)")
    p.add_argument("--print", action="store_true", help="print both files and exit")
    args = p.parse_args(argv)
    if args.print:
        print(f"# {DEFAULTS_PATH}\n{defaults_file()}\n# {DROPIN_PATH}\n{dropin_file()}")
        return 0
    if args.apply:
        return apply()
    differences = drift()
    if not differences:
        logger.info("earlyoom matches scripts/setup_earlyoom.py")
        return 0
    for line in differences:
        logger.error("drift: %s", line)
    logger.error("run: sudo -E uv run python scripts/setup_earlyoom.py --apply")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
