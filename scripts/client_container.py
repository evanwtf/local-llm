#!/usr/bin/env python3
"""Run the harness inside the pinned client image. #611

The image pins OpenCode, uv, CPython and git so a row's client identity is a
CPU and a memory size rather than also a toolchain (#562). This builds the
`docker run` that puts the harness inside it, and every argument here is a
decision that would otherwise be made wrong silently:

* **HOME=/root.** `tasks.toml` declares targets as `~/git/gmail-archive` and
  the harness expands them, so the mounts do not have to mimic the host's
  home -- they have to land where `$HOME` points. /root is also where the
  OpenCode installer puts its binary (it hardcodes `$HOME/.opencode/bin`).
* **A container-local project environment.** Mounting the repo brings the
  host's `.venv` with it, so `uv run` would execute the HOST's interpreter and
  the image's Python would never be used -- the run would measure nothing new.
* **bwrap needs privileges.** Measured 2026-09-20: `--cap-add SYS_ADMIN` with
  seccomp AND AppArmor unconfined is the minimum that runs a trial. This is
  not a hardening story: it removes most of what makes a container a boundary,
  and the thing protecting the host is the same bwrap layer as before.
* **--network host.** The server is reached over the LAN and the point is to
  measure the client, not to add a bridge hop to every request.

    uv run python scripts/client_container.py --server <host> \\
        --facts ~/facts.json -- --backend <name> --client opencode --trials 3
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

#: Host paths the harness reads or writes, mounted at the same path under the
#: container's HOME. A missing one fails as a model problem rather than a
#: mount problem, which is why they are enumerated rather than discovered.
MOUNTS = (
    "git/local-llm",
    "git/gmail-archive",
    "bench-logs",
    "bench-solutions",
    ".config/opencode",
    ".local/share/opencode",
)

#: The minimum privilege posture bwrap needs inside Docker (#611). Every
#: weaker combination was measured and fails at a different stage.
PRIVILEGES = (
    "--cap-add",
    "SYS_ADMIN",
    "--security-opt",
    "seccomp=unconfined",
    "--security-opt",
    "apparmor=unconfined",
)

CONTAINER_HOME = "/root"
#: Outside the mounted repo, so `uv run` cannot pick up the host's .venv.
PROJECT_ENV = "/opt/harness-venv"


def mount_args(home: pathlib.Path, mounts=MOUNTS) -> list[str]:
    """`-v host:container` for each mount, same relative path under HOME."""
    out = []
    for rel in mounts:
        out += ["-v", f"{home / rel}:{CONTAINER_HOME}/{rel}"]
    return out


def missing_mounts(home: pathlib.Path, mounts=MOUNTS) -> list[str]:
    """Which mounts do not exist on the host, so the failure is named early."""
    return [rel for rel in mounts if not (home / rel).exists()]


def docker_argv(
    *,
    image: str,
    home: pathlib.Path,
    server: str,
    facts: pathlib.Path,
    command: list[str],
    mem_cap_gib: int | None = None,
    name: str | None = None,
) -> list[str]:
    """The full `docker run` for one harness invocation."""
    env = [
        "-e",
        f"HOME={CONTAINER_HOME}",
        "-e",
        f"UV_PROJECT_ENVIRONMENT={PROJECT_ENV}",
        "-e",
        f"LOCAL_LLM_SERVER_HOST={server}",
        "-e",
        f"LOCAL_LLM_SERVER_FACTS={CONTAINER_HOME}/{facts.name}",
    ]
    if mem_cap_gib is not None:
        env += ["-e", f"LOCAL_LLM_CLIENT_MEM_CAP_GIB={mem_cap_gib}"]
    return [
        "docker",
        "run",
        "--rm",
        *(["--name", name] if name else []),
        "--network",
        "host",
        *PRIVILEGES,
        *mount_args(home),
        "-v",
        f"{facts}:{CONTAINER_HOME}/{facts.name}:ro",
        *env,
        "-w",
        f"{CONTAINER_HOME}/git/local-llm",
        image,
        "uv",
        "run",
        "python",
        "benchmarks/agent/run.py",
        *command,
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--image", default="local-llm-client:1.18.31")
    p.add_argument("--server", required=True)
    p.add_argument("--facts", type=pathlib.Path, required=True)
    p.add_argument("--home", type=pathlib.Path, default=pathlib.Path.home())
    p.add_argument("--mem-cap-gib", type=int, default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--print", action="store_true", help="print the argv, do not run")
    p.add_argument("command", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        p.error("pass the run.py arguments after --")

    gone = missing_mounts(args.home)
    if gone:
        print(f"missing on the host: {', '.join(gone)}", file=sys.stderr)
        return 1
    if not args.facts.exists():
        print(f"missing facts file: {args.facts}", file=sys.stderr)
        return 1

    full = docker_argv(
        image=args.image,
        home=args.home,
        server=args.server,
        facts=args.facts.resolve(),
        command=command,
        mem_cap_gib=args.mem_cap_gib,
        name=args.name,
    )
    if args.print:
        print(" ".join(full))
        return 0
    return subprocess.run(full, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
