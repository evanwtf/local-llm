"""Install one OpenCode binary from npm, checked against the registry (#1086).

OpenCode 2.x ships only on npm, as `@opencode/cli` plus one binary package
per platform (`@opencode/cli-linux-x64`, `@opencode/cli-linux-arm64`, ...).
Its GitHub releases, the `opencode-ai` npm package and opencode.ai/install all
stop at 1.19.0, so the 1.x install path cannot reach 2.x.

The image has no Node, so this does what `npm install` would for one file:
read the platform package's metadata, download its tarball, refuse it unless
the sha512 matches the registry's `dist.integrity`, and extract only
`package/bin/opencode`. Standard library only: the image runs it before
anything else is installed.

    python3 install_opencode.py 2.0.32 /usr/local/bin/opencode
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import platform
import sys
import tarfile
import urllib.request
from collections.abc import Callable

REGISTRY = "https://registry.npmjs.org"
ARCHES = {"x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}
MEMBER = "package/bin/opencode"


class InstallError(RuntimeError):
    """The download cannot be trusted or does not hold the binary."""


def package_for(machine: str) -> str:
    try:
        return f"@opencode/cli-linux-{ARCHES[machine]}"
    except KeyError:
        raise InstallError(f"no OpenCode linux package for {machine!r}") from None


def metadata_url(package: str, version: str) -> str:
    return f"{REGISTRY}/{package.replace('/', '%2f')}/{version}"


def check_integrity(blob: bytes, integrity: str) -> None:
    """Refuse `blob` unless it matches an npm `sha512-<base64>` integrity."""
    algo, _, want = integrity.partition("-")
    if algo != "sha512" or not want:
        raise InstallError(f"unsupported integrity {integrity[:20]!r}")
    got = base64.b64encode(hashlib.sha512(blob).digest()).decode()
    if got != want:
        raise InstallError("tarball sha512 does not match the registry integrity")


def extract_binary(blob: bytes) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        try:
            member = tar.getmember(MEMBER)
        except KeyError:
            raise InstallError(f"tarball has no {MEMBER}") from None
        if not member.isfile():
            raise InstallError(f"{MEMBER} is not a regular file")
        handle = tar.extractfile(member)
        if handle is None:
            raise InstallError(f"cannot read {MEMBER}")
        return handle.read()


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=300) as resp:
        data: bytes = resp.read()
        return data


def install(
    version: str,
    dest: str,
    machine: str | None = None,
    get: Callable[[str], bytes] = _get,
) -> str:
    package = package_for(machine or platform.machine())
    meta = json.loads(get(metadata_url(package, version)))
    if meta.get("version") != version:
        raise InstallError(f"registry answered {meta.get('version')!r}, not {version}")
    dist = meta.get("dist") or {}
    tarball, integrity = dist.get("tarball", ""), dist.get("integrity", "")
    if not tarball.startswith(REGISTRY + "/"):
        raise InstallError(f"tarball is not on the registry: {tarball!r}")
    blob = get(tarball)
    check_integrity(blob, integrity)
    binary = extract_binary(blob)
    with open(dest, "wb") as out:
        out.write(binary)
    os.chmod(dest, 0o755)
    return package


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: install_opencode.py VERSION DEST\n")
        return 2
    try:
        package = install(argv[0], argv[1])
    except (InstallError, OSError, ValueError) as exc:
        sys.stderr.write(f"install_opencode: {exc}\n")
        return 1
    sys.stdout.write(f"installed {package}@{argv[0]} to {argv[1]}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
