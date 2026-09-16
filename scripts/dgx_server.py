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
    )
    try:
        state_path(name, state_dir).write_text(
            json.dumps(server.as_dict(), indent=2) + "\n"
        )
    except BaseException:
        _systemctl("stop", unit)
        raise
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
            )
            logger.info("started %s", json.dumps(server.as_dict()))
            return 0
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
