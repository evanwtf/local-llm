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

A fresh build is checked under a candidate tag: the pins, the arch, and then
every OpenCode tool the trials use (`client_tool_smoke.py`, #968). Only a
build that passes all of them is tagged; a failed one never becomes the image
`client_container.py` runs.

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
    "opencode": "1.18.35",
    "uv": "0.13.0",
    "python": "3.14.8",
    # #968: on PATH, so OpenCode's grep and glob never unpack their own.
    "ripgrep": "15.2.0",
}

#: uname -m values this image is expected to build on, and the Docker platform
#: each one corresponds to. A third architecture is a deliberate decision, not
#: something that should quietly work.
ARCHES = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}

#: Tools whose --version puts the number in the second field.
_VERSION_IS_SECOND_TOKEN = frozenset({"uv", "python", "ripgrep"})


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
    # --pull and --no-cache: every rebuild takes the newest base image and the
    # newest packages, never a cached apt layer from an earlier build.
    got = subprocess.run(
        ["docker", "build", "--pull", "--no-cache", "-t", tag, str(context)],
        check=False,
    )
    return got.returncode


def inspect(tag: str) -> dict[str, str]:
    """Ask the built image what it has, from inside it."""
    script = (
        "opencode --version; uv --version; "
        f"uv python find {PINS['python']} >/dev/null && "
        f"$(uv python find {PINS['python']}) --version; "
        "git --version; uname -m; "
        # Last, and one line whatever happens: `rg --version` prints several,
        # and an image without rg (#968) must not shift the keys above.
        "{ rg --version 2>/dev/null || echo 'ripgrep missing'; } | head -n 1"
    )
    got = _run(["docker", "run", "--rm", "--entrypoint", "sh", tag, "-c", script])
    lines = [ln for ln in got.stdout.splitlines() if ln.strip()]
    keys = ["opencode", "uv", "python", "git", "machine", "ripgrep"]
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

    candidate = candidate_tag(args.tag)
    if not args.skip_build and build(candidate) != 0:
        print("FAIL  build: docker build returned non-zero")
        return 1

    # A fresh build is checked under a candidate tag and only then given the
    # real one, so a build that fails a check never becomes the image
    # client_container.py runs (#968).
    subject = args.tag if args.skip_build else candidate
    failed = verify(subject, machine)
    if args.skip_build:
        print(f"{'OK' if not failed else 'FAILED'}: {failed} check(s) failed")
        return 1 if failed else 0
    if failed:
        _run(["docker", "rmi", candidate])
        print(
            f"FAILED: {failed} check(s) failed; the build is NOT tagged {args.tag} "
            "and its candidate tag is removed"
        )
        return 1
    tagged = _run(["docker", "tag", candidate, args.tag])
    _run(["docker", "rmi", candidate])  # only the tag: the image keeps args.tag
    if tagged.returncode != 0:
        print(f"FAIL  tag: docker tag {candidate} {args.tag}: {tagged.stderr.strip()}")
        return 1
    print(f"OK: every check passed; tagged {args.tag}")
    return 0


def candidate_tag(tag: str) -> str:
    """The tag a build is checked under before it earns `tag`."""
    name, sep, version = tag.rpartition(":")
    if sep and "/" not in version:
        return f"{name}:{version}-candidate"
    return f"{tag}:candidate"


def verify(tag: str, machine: str) -> int:
    """How many checks `tag` fails: its pins, its arch, and its tools."""
    raw = inspect(tag)
    if not raw:
        print("FAIL  inspect: the image produced no output")
        return 1
    seen = parse_versions({k: raw.get(k, "") for k in PINS})
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
    print(json.dumps({"tag": tag, "host": machine, "seen": seen}))

    # #968: pins and arch were all this checked, and an image whose grep and
    # glob failed on every call passed it for two weeks. Run every tool.
    failures = tool_smoke(tag)
    for failure in failures:
        print(f"FAIL  tool {failure}")
    if not failures:
        print("PASS  tools: every OpenCode tool the trials use works in the image")
    return failed + len(failures)


def tool_smoke(tag: str) -> list[str]:
    """The tool self-test's failures for `tag` (scripts/client_tool_smoke.py)."""
    # Imported here: the self-test imports client_container, which imports
    # this module for its pins.
    import client_tool_smoke

    import logs

    logs.configure()
    return client_tool_smoke.run_in_image(tag)


if __name__ == "__main__":
    raise SystemExit(main())
