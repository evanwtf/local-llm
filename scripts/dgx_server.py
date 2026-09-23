#!/usr/bin/env python3
"""Control every long-lived DGX Spark server by recorded systemd scope. #429

This is the one lifecycle interface for inference and viewing servers on the
DGX Spark. It never searches process command lines. Start records a stable
systemd scope, its PID, canonical port, model identifier, and command; status
reads that record plus systemd and the endpoint; stop addresses the recorded
scope and waits for both the port and unified memory to clear.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
import logs

logger = logging.getLogger(__name__)
STATE_DIR = pathlib.Path(
    os.environ.get(
        "LOCAL_LLM_DGX_SERVER_DIR",
        pathlib.Path.home() / ".local-llm-bench" / "dgx-servers",
    )
)
CGROUP_ROOT = pathlib.Path("/sys/fs/cgroup")
MEM_SETTLE_MAX_GIB = float(os.environ.get("LOCAL_LLM_MEM_SETTLE_MAX_GIB", "24"))

#: The floor the watcher stops a server at, in GiB of MemAvailable.
#:
#: `MemoryMax` on the scope does NOT bound CUDA allocations on GB10 (#456):
#: while Nemotron-3-Super loaded under `MemoryMax=108G`, nvidia-smi showed the
#: EngineCore holding 71,776 MiB while the scope's own `memory.current` read
#: 3,762 MiB. Only `MemAvailable` sees the GPU side, so the wrapper watches it
#: and stops the scope itself. earlyoom (memory-only since #458) fires at
#: 1.0 GiB since #700, so the floor sits well above it: the wrapper should
#: take the server down cleanly before the box-wide killer has to.
MEM_FLOOR_GIB = float(os.environ.get("LOCAL_LLM_MEM_FLOOR_GIB", "14"))

#: Seconds between watcher polls. The #406 collapse ran ~0.7 GiB/s, so a 1 s
#: poll sees about 0.7 GiB of movement per tick -- fine against a 2 GiB margin
#: above earlyoom.
MEM_POLL_SECONDS = 1.0

#: Default parallelism for JIT kernel builds inside a server launch.
#:
#: FlashInfer compiles its NVFP4 CUTLASS kernel (18 CUDA translation units) on
#: first use, during vLLM's warmup, i.e. on top of the loaded weights. With
#: MAX_JOBS unset, ninja defaults to nproc+2 -- 22 concurrent `nvcc` processes
#: on this box -- and that, not the model, exhausted the pool twice on
#: 2026-09-17 (#406). Three is slow and survivable; prebuilding the kernel
#: while memory is free is still the better move.
DEFAULT_MAX_JOBS = os.environ.get("LOCAL_LLM_DEFAULT_MAX_JOBS", "3")


@dataclasses.dataclass(frozen=True)
class Profile:
    port: int
    health_path: str
    managed_args: tuple[str, ...] = ()
    memory_heavy: bool = True


PROFILES = {
    "llamacpp": Profile(
        8020, "/v1/models", ("--host", "0.0.0.0", "--port", "8020", "--metrics")
    ),
    "ds4": Profile(8020, "/v1/models", ("--host", "0.0.0.0", "--port", "8020")),
    "vllm": Profile(8030, "/v1/models", ("--host", "0.0.0.0", "--port", "8030")),
    "omni": Profile(8041, "/v1/models", ("--host", "0.0.0.0", "--port", "8041")),
    "clip": Profile(8042, "/", ("--bind", "0.0.0.0", "8042"), memory_heavy=False),
    "ollama": Profile(11434, "/api/tags"),
}


@dataclasses.dataclass(frozen=True)
class Server:
    name: str
    pid: int
    unit: str
    port: int
    model: str
    memory_max: str | None
    command: list[str]
    log: str
    started: str
    hostname: str
    watcher_pid: int | None = None
    mem_floor_gib: float | None = None

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def state_path(name: str, state_dir: pathlib.Path | None = None) -> pathlib.Path:
    if name not in PROFILES:
        raise ValueError(f"unknown server {name!r}; choose: {', '.join(PROFILES)}")
    return (state_dir or STATE_DIR) / f"{name}.json"


def read(name: str, state_dir: pathlib.Path | None = None) -> Server | None:
    try:
        raw = json.loads(state_path(name, state_dir).read_text())
        return Server(
            name=raw["name"],
            pid=int(raw["pid"]),
            unit=raw["unit"],
            port=int(raw["port"]),
            model=raw["model"],
            memory_max=raw.get("memory_max"),
            command=list(raw["command"]),
            log=raw["log"],
            started=raw["started"],
            hostname=raw["hostname"],
            # Both are optional: a record written before #456 has neither, and
            # a pre-#456 record must still load rather than read as absent.
            watcher_pid=(
                int(raw["watcher_pid"]) if raw.get("watcher_pid") is not None else None
            ),
            mem_floor_gib=(
                float(raw["mem_floor_gib"])
                if raw.get("mem_floor_gib") is not None
                else None
            ),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, check=False
    )


def unit_properties(unit: str) -> dict[str, str]:
    proc = _systemctl(
        "show",
        unit,
        "--property=ActiveState,SubState,ControlGroup,MemoryCurrent,TasksCurrent",
    )
    if proc.returncode != 0:
        return {}
    return dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)


def _unit_live(unit: str) -> bool:
    return unit_properties(unit).get("ActiveState") in {"active", "activating"}


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def _endpoint(port: int, path: str) -> tuple[bool, str | None]:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=2
        ) as response:
            raw = response.read()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return True, None
        models = data.get("data", []) if isinstance(data, dict) else []
        ids = [item.get("id") for item in models if isinstance(item, dict)]
        if isinstance(data, dict) and not ids:
            ids = [
                item.get("name")
                for item in data.get("models", [])
                if isinstance(item, dict)
            ]
        return True, next((item for item in ids if item), None)
    except Exception:  # noqa: BLE001 - status reports every probe failure as unhealthy
        return False, None


def _scope_pid(properties: dict[str, str], fallback: int) -> int:
    group = properties.get("ControlGroup", "").lstrip("/")
    if not group:
        return fallback
    try:
        pids = (CGROUP_ROOT / group / "cgroup.procs").read_text().split()
        return int(pids[0]) if pids else fallback
    except (OSError, ValueError):
        return fallback


def _mem_held_gib() -> float | None:
    try:
        values = {}
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(rest.split()[0])
        return (values["MemTotal"] - values["MemAvailable"]) / 1048576
    except (OSError, KeyError, ValueError, IndexError):
        return None


def mem_available_gib() -> float | None:
    """MemAvailable in GiB, or None if /proc/meminfo cannot be read.

    This is the only reading that sees a CUDA allocation on GB10; the scope's
    own cgroup counter does not (#456).
    """
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key == "MemAvailable":
                return int(rest.split()[0]) / 1048576
    except (OSError, ValueError, IndexError):
        return None
    return None


def watch(
    name: str,
    floor_gib: float = MEM_FLOOR_GIB,
    poll_seconds: float = MEM_POLL_SECONDS,
    state_dir: pathlib.Path | None = None,
) -> int:
    """Stop `name` if MemAvailable falls below `floor_gib`. Returns an exit code.

    0  the unit went away on its own (a normal stop, or it exited)
    1  the floor was crossed and the scope was stopped
    2  nothing to watch -- no record, or the unit was never live

    An unreadable /proc/meminfo is not a reason to kill a server, so the
    watcher treats it as "keep going" and says so once.
    """
    server = read(name, state_dir)
    if server is None:
        logger.info("watch: %s is not recorded", name)
        return 2
    warned = False
    while True:
        if not _unit_live(server.unit):
            logger.info("watch: %s is gone; stopping watch", server.unit)
            return 0
        avail = mem_available_gib()
        if avail is None:
            if not warned:
                logger.warning("watch: cannot read MemAvailable; not acting on it")
                warned = True
        elif avail < floor_gib:
            logger.error(
                "watch: MemAvailable %.1f GiB < floor %.1f GiB -- stopping %s "
                "(MemoryMax cannot bound CUDA on GB10, #456)",
                avail,
                floor_gib,
                server.unit,
            )
            _systemctl("stop", server.unit)
            return 1
        time.sleep(poll_seconds)


def _spawn_watcher(name: str, floor_gib: float, log: pathlib.Path) -> int | None:
    """Start `watch` as a detached child. Returns its pid, or None on failure.

    Deliberately not inside the server's own scope: a watcher there would be
    stopped by the very `systemctl stop` it issues, and would also count
    against the scope it is watching.
    """
    argv = [
        sys.executable,
        str(pathlib.Path(__file__).resolve()),
        "watch",
        name,
        "--floor-gib",
        str(floor_gib),
    ]
    try:
        with log.open("ab") as handle:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except (OSError, ValueError):
        logger.warning("could not start the memory watcher; launching without it")
        return None
    return proc.pid


def _managed_command(name: str, command: list[str]) -> list[str]:
    if not command:
        raise ValueError("a server needs a command after --")
    forbidden = {"--host", "--port", "--bind"}
    if any(item in forbidden for item in command):
        raise ValueError(
            "host and port are wrapper-owned; remove them from the command"
        )
    if name == "llamacpp" and "--metrics" in command:
        raise ValueError("metrics are wrapper-owned; remove --metrics from the command")
    return [*command, *PROFILES[name].managed_args]


def start(
    name: str,
    command: list[str],
    model: str,
    memory_max: str | None,
    log: pathlib.Path,
    state_dir: pathlib.Path | None = None,
    cwd: pathlib.Path | None = None,
    mem_floor_gib: float | None = None,
    max_jobs: str | None = None,
) -> Server:
    profile = PROFILES[name]
    unit = f"local-llm-{name}.scope"
    old = read(name, state_dir)
    if old and _unit_live(old.unit):
        raise RuntimeError(f"{name} is already running as {old.unit} (pid {old.pid})")
    if _unit_live(unit):
        raise RuntimeError(f"systemd unit {unit} is already live")
    if _port_open(profile.port):
        raise RuntimeError(f"canonical port {profile.port} is already in use")
    if profile.memory_heavy and not memory_max:
        raise ValueError(f"{name} requires --memory-max on the DGX unified-memory pool")
    final = _managed_command(name, command)
    directory = state_dir or STATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    argv = ["systemd-run", "--user", "--scope", f"--unit={unit.removesuffix('.scope')}"]
    jobs = DEFAULT_MAX_JOBS if max_jobs is None else max_jobs
    if jobs and not any(item.startswith("MAX_JOBS=") for item in final):
        # A JIT kernel build inside the launch is what exhausted the pool in
        # #406, so the cap is on by default. An explicit MAX_JOBS in the
        # command wins, because the caller said something deliberate.
        argv.append(f"--setenv=MAX_JOBS={jobs}")
    if memory_max:
        argv.extend(
            (f"--property=MemoryMax={memory_max}", "--property=MemorySwapMax=0")
        )
    argv.extend(("--", *final))
    with log.open("ab") as handle:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + 5
    properties: dict[str, str] = {}
    while time.monotonic() < deadline:
        properties = unit_properties(unit)
        if properties.get("ActiveState") == "active":
            break
        if proc.poll() is not None:
            raise RuntimeError(
                f"systemd-run exited before {unit} became active; see {log}"
            )
        time.sleep(0.1)
    if properties.get("ActiveState") != "active":
        _systemctl("stop", unit)
        raise RuntimeError(f"{unit} did not become active within 5 seconds")
    floor = MEM_FLOOR_GIB if mem_floor_gib is None else mem_floor_gib
    server = Server(
        name=name,
        pid=_scope_pid(properties, proc.pid),
        unit=unit,
        port=profile.port,
        model=model,
        memory_max=memory_max,
        command=final,
        log=str(log),
        started=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        hostname=os.uname().nodename,
        mem_floor_gib=floor if profile.memory_heavy and floor > 0 else None,
    )
    try:
        state_path(name, state_dir).write_text(
            json.dumps(server.as_dict(), indent=2) + "\n"
        )
    except BaseException:
        _systemctl("stop", unit)
        raise
    if server.mem_floor_gib is not None:
        watcher = _spawn_watcher(name, server.mem_floor_gib, log)
        if watcher is not None:
            server = dataclasses.replace(server, watcher_pid=watcher)
            try:
                state_path(name, state_dir).write_text(
                    json.dumps(server.as_dict(), indent=2) + "\n"
                )
            except OSError:
                logger.warning("watcher pid %s not recorded", watcher)
    return server


def stop(
    name: str, state_dir: pathlib.Path | None = None, timeout: float = 120
) -> bool:
    server = read(name, state_dir)
    if server is None:
        logger.info("%s is not recorded", name)
        return False
    _systemctl("stop", server.unit)
    deadline = time.monotonic() + timeout
    profile = PROFILES[name]
    while time.monotonic() < deadline:
        held = _mem_held_gib()
        memory_settled = (
            not profile.memory_heavy or held is None or held <= MEM_SETTLE_MAX_GIB
        )
        if (
            not _unit_live(server.unit)
            and not _port_open(server.port)
            and memory_settled
        ):
            state_path(name, state_dir).unlink(missing_ok=True)
            return True
        time.sleep(1)
    raise RuntimeError(
        f"{server.unit} did not fully stop: unit_live={_unit_live(server.unit)}, port_open={_port_open(server.port)}, memory_held_gib={_mem_held_gib()}"
    )


def _pid_live(pid: int | None) -> bool | None:
    """Is that pid still around? None when there is no pid to ask about.

    Signal 0 checks for existence and never kills.
    """
    if not pid:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return None
    return True


def status(name: str, state_dir: pathlib.Path | None = None) -> dict[str, object]:
    server = read(name, state_dir)
    profile = PROFILES[name]
    if server is None:
        return {
            "name": name,
            "recorded": False,
            "port": profile.port,
            "port_open": _port_open(profile.port),
        }
    properties = unit_properties(server.unit)
    healthy, served = _endpoint(server.port, profile.health_path)
    memory = properties.get("MemoryCurrent")
    return {
        **server.as_dict(),
        "recorded": True,
        "unit_state": properties.get("ActiveState", "not-found"),
        "unit_substate": properties.get("SubState", "not-found"),
        "port_open": _port_open(server.port),
        "healthy": healthy,
        "resident_bytes": int(memory) if memory and memory.isdigit() else None,
        "served_model": served or (server.model if healthy else None),
        "mem_available_gib": mem_available_gib(),
        "watcher_live": _pid_live(server.watcher_pid),
    }


def _split_command(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        return argv, []
    index = argv.index("--")
    return argv[:index], argv[index + 1 :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", type=pathlib.Path)
    sub = parser.add_subparsers(dest="action", required=True)
    start_parser = sub.add_parser("start")
    start_parser.add_argument("name", choices=PROFILES)
    start_parser.add_argument("--model", required=True)
    start_parser.add_argument("--memory-max")
    start_parser.add_argument("--log", type=pathlib.Path)
    start_parser.add_argument("--cwd", type=pathlib.Path)
    start_parser.add_argument(
        "--mem-floor-gib",
        type=float,
        default=None,
        help=(
            f"stop this server when MemAvailable falls below N GiB "
            f"(default {MEM_FLOOR_GIB}; 0 disables the watcher). MemoryMax "
            f"cannot bound CUDA memory on GB10 (#456)."
        ),
    )
    start_parser.add_argument(
        "--max-jobs",
        default=None,
        help=(
            f"MAX_JOBS for JIT kernel builds inside the launch "
            f"(default {DEFAULT_MAX_JOBS}; empty disables). An unset MAX_JOBS "
            f"runs ~nproc+2 nvcc jobs during warmup and exhausted the pool "
            f"in #406."
        ),
    )
    watch_parser = sub.add_parser("watch")
    watch_parser.add_argument("name", choices=PROFILES)
    watch_parser.add_argument("--floor-gib", type=float, default=MEM_FLOOR_GIB)
    watch_parser.add_argument("--poll-seconds", type=float, default=MEM_POLL_SECONDS)
    stop_parser = sub.add_parser("stop")
    stop_parser.add_argument("name", choices=PROFILES)
    stop_parser.add_argument("--timeout", type=float, default=120)
    status_parser = sub.add_parser("status")
    status_parser.add_argument("name", choices=PROFILES)
    head, command = _split_command(list(sys.argv[1:] if argv is None else argv))
    args = parser.parse_args(head)
    logs.configure()
    try:
        if args.action == "start":
            log = (
                args.log
                or pathlib.Path.home() / "bench-logs" / f"{args.name}-server.log"
            )
            server = start(
                args.name,
                command,
                args.model,
                args.memory_max,
                log,
                state_dir=args.state_dir,
                cwd=args.cwd,
                mem_floor_gib=args.mem_floor_gib,
                max_jobs=args.max_jobs,
            )
            logger.info("started %s", json.dumps(server.as_dict()))
            return 0
        if args.action == "watch":
            return watch(
                args.name,
                floor_gib=args.floor_gib,
                poll_seconds=args.poll_seconds,
                state_dir=args.state_dir,
            )
        if args.action == "stop":
            stop(args.name, state_dir=args.state_dir, timeout=args.timeout)
            return 0
        result = status(args.name, state_dir=args.state_dir)
        print(json.dumps(result, indent=2))
        return 0 if result.get("unit_state") == "active" else 3
    except (OSError, RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
