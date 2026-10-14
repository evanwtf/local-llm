"""The image's OpenCode installer must refuse anything it cannot verify (#1086).

OpenCode 2.x comes from npm tarballs, not GitHub releases. The installer's
whole job is to refuse a download that does not match the registry, so the
tests here are mostly refusals.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import pathlib
import tarfile

import pytest

SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "docker"
    / "opencode-client"
    / "install_opencode.py"
)
_spec = importlib.util.spec_from_file_location("install_opencode", SCRIPT)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

URL = "https://registry.npmjs.org/@opencode/cli-linux-x64/-/cli-linux-x64-2.0.29.tgz"


def tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def integrity(blob: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode()


def registry(blob: bytes, meta: dict | None = None):
    meta = meta or {
        "version": "2.0.29",
        "dist": {"tarball": URL, "integrity": integrity(blob)},
    }

    def get(url: str) -> bytes:
        if url.endswith(".tgz"):
            return blob
        assert url == "https://registry.npmjs.org/@opencode%2fcli-linux-x64/2.0.29"
        return json.dumps(meta).encode()

    return get


def test_installs_the_binary_when_the_integrity_matches(tmp_path):
    blob = tarball({"package/bin/opencode": b"BIN", "package/package.json": b"{}"})
    dest = tmp_path / "opencode"
    got = mod.install("2.0.29", str(dest), "x86_64", registry(blob))
    assert got == "@opencode/cli-linux-x64"
    assert dest.read_bytes() == b"BIN"
    assert dest.stat().st_mode & 0o777 == 0o755


def test_a_tampered_tarball_is_refused(tmp_path):
    blob = tarball({"package/bin/opencode": b"BIN"})
    meta = {
        "version": "2.0.29",
        "dist": {"tarball": URL, "integrity": integrity(b"something else")},
    }
    dest = tmp_path / "opencode"
    with pytest.raises(mod.InstallError, match="sha512"):
        mod.install("2.0.29", str(dest), "x86_64", registry(blob, meta))
    assert not dest.exists()


def test_a_non_sha512_integrity_is_refused():
    with pytest.raises(mod.InstallError, match="unsupported"):
        mod.check_integrity(b"x", "sha1-abc")


def test_a_tarball_off_the_registry_is_refused(tmp_path):
    blob = tarball({"package/bin/opencode": b"BIN"})
    meta = {
        "version": "2.0.29",
        "dist": {"tarball": "https://evil.example/x.tgz", "integrity": integrity(blob)},
    }
    with pytest.raises(mod.InstallError, match="not on the registry"):
        mod.install("2.0.29", str(tmp_path / "o"), "x86_64", registry(blob, meta))


def test_a_version_mismatch_is_refused(tmp_path):
    blob = tarball({"package/bin/opencode": b"BIN"})
    meta = {"version": "2.0.28", "dist": {"tarball": URL, "integrity": integrity(blob)}}
    with pytest.raises(mod.InstallError, match="2.0.28"):
        mod.install("2.0.29", str(tmp_path / "o"), "x86_64", registry(blob, meta))


def test_a_tarball_without_the_binary_is_refused(tmp_path):
    blob = tarball({"package/README.md": b"hi"})
    with pytest.raises(mod.InstallError, match="no package/bin/opencode"):
        mod.install("2.0.29", str(tmp_path / "o"), "x86_64", registry(blob))


def test_an_unknown_arch_is_refused():
    with pytest.raises(mod.InstallError, match="riscv64"):
        mod.package_for("riscv64")
    assert mod.package_for("aarch64") == "@opencode/cli-linux-arm64"
