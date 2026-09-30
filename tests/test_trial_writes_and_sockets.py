"""Tests for #780's last two items: private TMPDIR, writes, container sockets.

On 2026-09-25 a trial extracted about 18 GB of a personal mail archive into
the shared $TMPDIR. #786 closed the read; these close the write and the
daemon. A trial writes only to its worktree, its own TMPDIR and the tool
caches, and cannot connect to a container daemon's socket, which would act
on the host outside the sandbox. Like test_home_read_allowlist.py, the
sandbox tests run the real profile under `sandbox-exec`, so they check the
kernel's decision, not the profile's text.
"""

from __future__ import annotations

import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import run

SANDBOX_EXEC = pathlib.Path("/usr/bin/sandbox-exec")
needs_sandbox = pytest.mark.skipif(
    not SANDBOX_EXEC.exists(), reason="needs sandbox-exec (macOS)"
)


@pytest.fixture
def home(tmp_path: pathlib.Path) -> pathlib.Path:
    root = (tmp_path / "home").resolve()
    (root / "Downloads").mkdir(parents=True)
    (root / ".cache" / "uv").mkdir(parents=True)
    return root


@pytest.fixture
def layout(tmp_path: pathlib.Path, home: pathlib.Path) -> dict[str, pathlib.Path]:
    """A fake system temp dir holding a trial dir, as `$TMPDIR/agent-bench` does."""
    system_tmp = (tmp_path / "T").resolve()
    worktree = system_tmp / "agent-bench" / "trial-1" / "repo"
    worktree.mkdir(parents=True)
    tmp_root = system_tmp / "agent-bench" / "trial-tmp"
    trial_tmp = run.trial_tmp(worktree, tmp_root)
    trial_tmp.mkdir(parents=True)
    return {
        "system_tmp": system_tmp,
        "worktree": worktree,
        "trial_tmp": trial_tmp,
        "tmp_root": tmp_root,
    }


def _profile(tmp_path, home, layout, sockets=()) -> pathlib.Path:
    profile, _ = run.sandbox_profile(
        layout["worktree"],
        home / "git" / "target",
        home=home,
        system_tmp=layout["system_tmp"],
        sockets=sockets,
        tmp_root=layout["tmp_root"],
    )
    path = tmp_path / "confine.sb"
    path.write_text(profile)
    return path


def _touch(profile: pathlib.Path, target: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SANDBOX_EXEC), "-f", str(profile), "/usr/bin/touch", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )


@needs_sandbox
def test_a_write_under_home_is_refused(tmp_path, home, layout):
    target = home / "Downloads" / "extracted.mbox"
    result = _touch(_profile(tmp_path, home, layout), target)
    assert result.returncode != 0
    assert not target.exists()


@needs_sandbox
def test_a_write_to_the_shared_temp_dir_is_refused(tmp_path, home, layout):
    """The 2026-09-25 extraction landed here."""
    target = layout["system_tmp"] / "extracted.mbox"
    result = _touch(_profile(tmp_path, home, layout), target)
    assert result.returncode != 0
    assert not target.exists()


@needs_sandbox
def test_a_write_to_slash_tmp_is_refused(tmp_path, home, layout):
    target = pathlib.Path("/private/tmp") / f"lllm-780-{tmp_path.name}"
    try:
        result = _touch(_profile(tmp_path, home, layout), target)
        assert result.returncode != 0
        assert not target.exists()
    finally:
        target.unlink(missing_ok=True)


@needs_sandbox
@pytest.mark.parametrize("where", ["worktree", "trial_tmp"])
def test_the_trial_can_write_its_worktree_and_its_tmp(tmp_path, home, layout, where):
    target = layout[where] / "out.txt"
    result = _touch(_profile(tmp_path, home, layout), target)
    assert result.returncode == 0, result.stderr
    assert target.exists()


@needs_sandbox
def test_a_tool_cache_stays_writable(tmp_path, home, layout):
    target = home / ".cache" / "uv" / "wheel.txt"
    result = _touch(_profile(tmp_path, home, layout), target)
    assert result.returncode == 0, result.stderr


@needs_sandbox
def test_a_container_socket_cannot_be_reached(tmp_path, home, layout):
    """A daemon acts on the host outside the sandbox (2026-09-18: a trial's
    `docker compose up` created a root-owned dir at a real repo path)."""
    short = pathlib.Path(tempfile.mkdtemp(dir="/tmp"))  # AF_UNIX paths are short
    try:
        sock_path = short / "docker.sock"
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(sock_path))
        server.listen(1)  # a connect succeeds without accept()
        profile = _profile(tmp_path, home, layout, sockets=(str(sock_path),))
        client = (
            "import socket,sys\n"
            "s=socket.socket(socket.AF_UNIX)\n"
            f"s.connect({str(sock_path)!r})\n"
        )
        result = subprocess.run(
            [str(SANDBOX_EXEC), "-f", str(profile), sys.executable, "-c", client],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "Operation not permitted" in result.stderr
        server.close()
    finally:
        shutil.rmtree(short, ignore_errors=True)


def test_the_socket_rules_name_the_real_path_too(tmp_path):
    """The sandbox checks the resolved path. OrbStack's /var/run/docker.sock
    is a symlink into ~/.orbstack, so both names are denied."""
    real = tmp_path / "real.sock"
    real.write_text("")
    link = tmp_path / "link.sock"
    link.symlink_to(real)
    paths = run.socket_rule_paths([str(link)])
    assert str(link) in paths
    assert str(real.resolve()) in paths


def test_the_mac_socket_list_covers_the_common_runtimes():
    home = pathlib.Path("/Users/x")
    listed = {str(p) for p in run.mac_container_sockets(home)}
    assert "/var/run/docker.sock" in listed
    assert "/Users/x/.orbstack/run/docker.sock" in listed
    assert "/Users/x/.docker/run/docker.sock" in listed
    assert "/Users/x/.colima/default/docker.sock" in listed


def test_the_trial_gets_its_own_empty_tmpdir(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "TRIAL_TMP_ROOT", tmp_path / "trial-tmp")
    worktree = tmp_path / "trial-1" / "repo"
    worktree.mkdir(parents=True)
    stale = run.trial_tmp(worktree) / "left-by-the-last-trial"
    stale.parent.mkdir(parents=True)
    stale.write_text("x")
    backend = {
        "base_url": "http://127.0.0.1:1",
        "auth_token": "t",
        "model": "m",
        "context_tokens": 1000,
    }
    env = run.agent_env(backend, worktree)
    assert env["TMPDIR"] == str(tmp_path / "trial-tmp" / "repo")
    assert pathlib.Path(env["TMPDIR"]).is_dir()
    assert not stale.exists()


def test_a_stash_layout_trial_tmp_is_not_beside_the_checkout():
    """A stash-layout worktree IS the real checkout, ~/git/monitor. The first
    cut put the TMPDIR at its parent, so every trial shared ~/git/tmp."""
    home = pathlib.Path.home()
    tmp = run.trial_tmp(home / "git" / "monitor")
    assert not str(tmp).startswith(str(home) + "/")
    assert tmp != home / "git" / "tmp"


@needs_sandbox
def test_a_stash_layout_trial_can_read_back_its_temp_files(tmp_path, home, layout):
    """`ld` writes object files to TMPDIR and opens them again. Under $HOME
    the write was allowed and the read denied, and every swift build failed."""
    worktree = home / "git" / "monitor"
    worktree.mkdir(parents=True)
    trial_tmp = run.trial_tmp(worktree, layout["tmp_root"])
    trial_tmp.mkdir(parents=True)
    profile, _ = run.sandbox_profile(
        worktree,
        home / "git" / "target",
        home=home,
        system_tmp=layout["system_tmp"],
        sockets=(),
        tmp_root=layout["tmp_root"],
    )
    path = tmp_path / "stash.sb"
    path.write_text(profile)
    obj = trial_tmp / "BuildStamp-1.o"
    script = f"echo obj > {obj} && cat {obj}"
    result = subprocess.run(
        [str(SANDBOX_EXEC), "-f", str(path), "/bin/sh", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "obj\n"


@needs_sandbox
@pytest.mark.parametrize("name", ["xcrun_db-AbC123", "xcrun_db"])
def test_xcruns_cache_file_may_be_written_to_the_shared_tmp(
    tmp_path, home, layout, name
):
    """xcrun writes xcrun_db-XXXX, then renames it to xcrun_db."""
    ok = _touch(_profile(tmp_path, home, layout), layout["system_tmp"] / name)
    assert ok.returncode == 0, ok.stderr


@needs_sandbox
def test_any_other_name_in_the_shared_tmp_is_refused(tmp_path, home, layout):
    target = layout["system_tmp"] / "not-xcrun_db-AbC123"
    assert _touch(_profile(tmp_path, home, layout), target).returncode != 0


SWIFTC = shutil.which("swiftc") or shutil.which("xcrun")


@needs_sandbox
@pytest.mark.skipif(SWIFTC is None, reason="needs swiftc")
def test_a_foundation_atomic_write_into_the_worktree_succeeds(tmp_path):
    """Foundation stages an atomic write in <per-user temp>/TemporaryItems,
    whatever TMPDIR says. Refused, the Swift build service could not write
    its manifest into the worktree (batch 0929-780w2). The real per-user
    temp dir, since that is the one Foundation uses."""
    src = tmp_path / "aw.swift"
    src.write_text(
        "import Foundation\n"
        'try! "x".write(toFile: CommandLine.arguments[1], atomically: true,'
        " encoding: .utf8)\n"
    )
    binary = tmp_path / "aw"
    build = subprocess.run(
        ["xcrun", "swiftc", str(src), "-o", str(binary)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    worktree = pathlib.Path(tempfile.mkdtemp(prefix="lllm-780-")).resolve() / "repo"
    try:
        worktree.mkdir()
        profile, _ = run.sandbox_profile(
            worktree,
            tmp_path / "home" / "git" / "target",
            home=tmp_path / "home",
            sockets=(),
            tmp_root=tmp_path / "trial-tmp",
        )
        path = tmp_path / "real.sb"
        path.write_text(profile)
        result = subprocess.run(
            [str(SANDBOX_EXEC), "-f", str(path), str(binary), str(worktree / "a.txt")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (worktree / "a.txt").read_text() == "x"
    finally:
        shutil.rmtree(worktree.parent, ignore_errors=True)


def test_the_stamp_records_the_new_policy():
    """New values, so rows under this policy never pool with older ones (#477)."""
    rec = run.confinement_record("sandbox-exec", ["/x"], 24)
    assert rec["tmp"] == "private"
    assert rec["writes"] == "allow-list"
    assert rec["sockets"] == "denied"
    assert run.confinement_record("none", [], 24)["writes"] == "unenforced"
    assert run.confinement_record("none", [], 24)["sockets"] == "unenforced"


@needs_sandbox
def test_the_socket_control_connects_without_the_rule(tmp_path, home, layout):
    """The control for the test above: the same connect, no socket rule."""
    short = pathlib.Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        sock_path = short / "docker.sock"
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(sock_path))
        server.listen(1)
        profile = _profile(tmp_path, home, layout, sockets=())
        client = (
            "import socket\n"
            "s=socket.socket(socket.AF_UNIX)\n"
            f"s.connect({str(sock_path)!r})\n"
        )
        result = subprocess.run(
            [str(SANDBOX_EXEC), "-f", str(profile), sys.executable, "-c", client],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        server.close()
    finally:
        shutil.rmtree(short, ignore_errors=True)
