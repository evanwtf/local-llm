#!/usr/bin/env python3
"""Build the pinned OpenCode client image and verify it from inside. #611

The image exists so a row's `env.client_machine` means a CPU and a memory
size, not also a distro, a Python and a glibc (#562). That only holds if the
pins are real, so this builds and then asks the running container what it
actually has.

It must build natively on both machines in play: linux/amd64 clients and the
linux/arm64 server box. Nothing in the Dockerfile branches on architecture --
the OpenCode and uv installers resolve it themselves -- so the same command
runs on either, and this script records which one it ran on.

    uv run python scripts/build_client_image.py
    uv run python scripts/build_client_image.py --tag local-llm-client:test
"""

from __future__ import annotations

import argparse
import json
import pathlib
import platform
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
CONTEXT = REPO / "docker" / "opencode-client"

#: What the Dockerfile pins. Kept here as well so a drift between the two is a
#: test failure rather than a surprise at build time.
PINS = {
    "opencode": "1.18.32",
    "uv": "0.12.13",
    "python": "3.14.4",
}

#: uname -m values this image is expected to build on, and the Docker platform
#: each one corresponds to. A third architecture is a deliberate decision, not
#: something that should quietly work.
ARCHES = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}

#: Tools whose --version puts the number in the second field.
_VERSION_IS_SECOND_TOKEN = frozenset({"uv", "python"})


def platform_for(machine: str) -> str | None:
    """The Docker platform for a `uname -m`, or None if unsupported."""
    return ARCHES.get(machine)


def parse_versions(raw: dict[str, str]) -> dict[str, str]:
    """Normalise the version strings the tools print.

    `uv --version` prints "uv 0.12.13 (x86_64-unknown-linux-gnu)" and the
    build host's triple is in there, so a naive equality check against a pin
    would fail on one architecture and pass on the other -- which is exactly
    the class of bug this image exists to remove.
    """
    out = {}
    for name, text in raw.items():
        token = text.strip().split()
        if not token:
            out[name] = ""
            continue
        # `uv --version` prints "uv 0.12.13 (<triple>)" and `python --version`
        # prints "Python 3.14.4" -- both put the number second. OpenCode
        # prints the bare number.
        second = name in _VERSION_IS_SECOND_TOKEN and len(token) >= 2
        out[name] = token[1] if second else token[0]
    return out


def check_pins(
    seen: dict[str, str], pins: dict[str, str]
) -> list[tuple[str, bool, str]]:
    """One (name, ok, detail) per pin, in a stable order."""
    return [
        (name, seen.get(name) == want, f"{seen.get(name) or 'missing'} (pinned {want})")
        for name, want in sorted(pins.items())
    ]


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=False, **kw)


def build(tag: str, context: pathlib.Path = CONTEXT) -> int:
    print(f"building {tag} for {platform.machine()} from {context}", flush=True)
    got = subprocess.run(["docker", "build", "-t", tag, str(context)], check=False)
    return got.returncode


def inspect(tag: str) -> dict[str, str]:
    """Ask the built image what it has, from inside it."""
    script = (
        "opencode --version; uv --version; "
        f"uv python find {PINS['python']} >/dev/null && "
        f"$(uv python find {PINS['python']}) --version; "
        "git --version; uname -m"
    )
    got = _run(["docker", "run", "--rm", "--entrypoint", "sh", tag, "-c", script])
    lines = [ln for ln in got.stdout.splitlines() if ln.strip()]
    keys = ["opencode", "uv", "python", "git", "machine"]
    return dict(zip(keys, lines))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--tag", default=f"local-llm-client:{PINS['opencode']}")
    p.add_argument("--skip-build", action="store_true", help="verify an existing tag")
    args = p.parse_args(argv)

    machine = platform.machine()
    docker_platform = platform_for(machine)
    if docker_platform is None:
        print(f"FAIL  architecture: {machine} is not one of {sorted(ARCHES)}")
        return 1
    print(f"PASS  architecture: {machine} -> {docker_platform}")

    if not args.skip_build and build(args.tag) != 0:
        print("FAIL  build: docker build returned non-zero")
        return 1

    raw = inspect(args.tag)
    if not raw:
        print("FAIL  inspect: the image produced no output")
        return 1
    seen = parse_versions({k: raw.get(k, "") for k in ("opencode", "uv", "python")})
    failed = 0
    for name, ok, detail in check_pins(seen, PINS):
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
        failed += 0 if ok else 1

    inside = raw.get("machine", "")
    same = inside == machine
    print(
        f"{'PASS' if same else 'FAIL'}  image arch: {inside or 'unknown'} (host {machine})"
    )
    failed += 0 if same else 1

    print(f"PASS  git: {raw.get('git', 'unknown')}")
    print(json.dumps({"tag": args.tag, "host": machine, "seen": seen}))
    print(f"{'OK' if not failed else 'FAILED'}: {failed} check(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
