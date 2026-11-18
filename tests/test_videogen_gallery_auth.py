"""scripts/videogen/gallery_auth.py: the credentials file, password hashes,
session cookies, the client address and the login lockout (#1413)."""

from __future__ import annotations

import base64
import io
import os
import pathlib
import stat
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import gallery_auth as ga

PASSWORD = "correct horse battery staple"
#: The cheapest cost the loader accepts, so the tests stay fast.
FAST = {"n": ga.MIN_N, "r": 8, "p": 1}


@pytest.fixture
def auth_file(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "cfg" / "videogen-gallery" / "auth.yaml"
    ga.write_auth(path, "wat", PASSWORD, **FAST)
    return path


# ------------------------------------------------------------ the file ----


def test_write_auth_makes_a_0600_file_in_a_0700_dir(auth_file: pathlib.Path) -> None:
    assert stat.S_IMODE(auth_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(auth_file.parent.stat().st_mode) == 0o700
    # No temp file is left beside it.
    assert [p.name for p in auth_file.parent.iterdir()] == ["auth.yaml"]


def test_the_file_never_holds_the_plain_password(auth_file: pathlib.Path) -> None:
    text = auth_file.read_text()
    assert PASSWORD not in text
    for part in PASSWORD.split():
        assert part not in text
    assert base64.b64encode(PASSWORD.encode()).decode() not in text


def test_the_file_has_the_documented_shape(auth_file: pathlib.Path) -> None:
    data = yaml.safe_load(auth_file.read_text())
    assert set(data) == {"users", "session_secret"}
    assert [u["username"] for u in data["users"]] == ["wat"]
    assert data["users"][0]["password_hash"].startswith(f"scrypt$n={ga.MIN_N},r=8,p=1$")
    assert len(base64.b64decode(data["session_secret"])) >= 32


def test_write_auth_is_atomic(
    auth_file: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = auth_file.read_bytes()

    def broken(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        ga.write_auth(auth_file, "wat", "another long password", **FAST)
    assert auth_file.read_bytes() == before
    assert [p.name for p in auth_file.parent.iterdir()] == ["auth.yaml"]


def test_a_new_password_rotates_the_secret(auth_file: pathlib.Path) -> None:
    first = ga.load_auth(auth_file)
    ga.write_auth(auth_file, "wat", "another long password", **FAST)
    second = ga.load_auth(auth_file)
    assert first.secret != second.secret
    cookie = ga.make_cookie(first, "wat", now=1000.0)
    assert ga.check_cookie(second, cookie, now=1001.0) is None


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o700, 0o400])
def test_a_file_at_the_wrong_mode_is_refused(
    auth_file: pathlib.Path, mode: int
) -> None:
    auth_file.chmod(mode)
    with pytest.raises(ga.AuthError, match="0600"):
        ga.load_auth(auth_file)


def test_a_missing_file_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ga.AuthError, match="set-password"):
        ga.load_auth(tmp_path / "nope.yaml")


def write_raw(path: pathlib.Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o600)


@pytest.mark.parametrize(
    "text",
    [
        "just a string",
        "users: []\nsession_secret: " + base64.b64encode(b"x" * 32).decode(),
        "users:\n  - username: wat\nsession_secret: abc",
        "users:\n  - username: wat\n    password_hash: md5$abc$def\n"
        "session_secret: " + base64.b64encode(b"x" * 32).decode(),
        "users:\n  - username: wat\n    password_hash: scrypt$n=1024,r=8,p=1$c2FsdA==$aGFzaA==\n"
        "session_secret: " + base64.b64encode(b"x" * 32).decode(),
        "users:\n  - username: wat\n    password_hash: scrypt$n=16384,r=8,p=1$c2FsdA==$aGFzaA==\n"
        "session_secret: " + base64.b64encode(b"short").decode(),
        "!!python/object/apply:os.system ['true']",
    ],
)
def test_a_file_of_another_shape_is_refused(tmp_path: pathlib.Path, text: str) -> None:
    path = tmp_path / "auth.yaml"
    write_raw(path, text)
    with pytest.raises(ga.AuthError):
        ga.load_auth(path)


def test_an_extra_key_is_refused(auth_file: pathlib.Path) -> None:
    data = yaml.safe_load(auth_file.read_text())
    data["password"] = "plain"
    write_raw(auth_file, yaml.safe_dump(data))
    with pytest.raises(ga.AuthError, match="unknown"):
        ga.load_auth(auth_file)


# ---------------------------------------------------------- passwords ----


def test_verify(auth_file: pathlib.Path) -> None:
    auth = ga.load_auth(auth_file)
    assert ga.verify(auth, "wat", PASSWORD) == "wat"
    assert ga.verify(auth, "wat", PASSWORD + "x") is None
    assert ga.verify(auth, "WAT", PASSWORD) is None
    assert ga.verify(auth, "other", PASSWORD) is None
    assert ga.verify(auth, "", "") is None


def test_hash_password_salts_each_hash() -> None:
    assert ga.hash_password("pw", **FAST) != ga.hash_password("pw", **FAST)


# ------------------------------------------------------------ cookies ----


def test_a_cookie_round_trips_until_it_expires(auth_file: pathlib.Path) -> None:
    auth = ga.load_auth(auth_file)
    cookie = ga.make_cookie(auth, "wat", now=1000.0)
    assert ga.check_cookie(auth, cookie, now=1000.0 + ga.SESSION_S - 1) == "wat"
    assert ga.check_cookie(auth, cookie, now=1000.0 + ga.SESSION_S + 1) is None


def tamper(cookie: str) -> list[str]:
    body, sig = cookie.split(".")
    raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    other = base64.urlsafe_b64encode(raw.replace(b"wat", b"wau")).decode().rstrip("=")
    later = raw.rsplit(b":", 1)[0] + b":99999999999"
    late = base64.urlsafe_b64encode(later).decode().rstrip("=")
    flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    return [f"{other}.{sig}", f"{late}.{sig}", f"{body}.{flipped}", body, "", "a.b.c"]


def test_a_tampered_cookie_is_refused(auth_file: pathlib.Path) -> None:
    auth = ga.load_auth(auth_file)
    cookie = ga.make_cookie(auth, "wat", now=1000.0)
    for bad in tamper(cookie):
        assert ga.check_cookie(auth, bad, now=1001.0) is None, bad


def test_a_cookie_for_a_removed_user_is_refused(
    auth_file: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    auth = ga.load_auth(auth_file)
    cookie = ga.make_cookie(auth, "wat", now=1000.0)
    data = yaml.safe_load(auth_file.read_text())
    data["users"][0]["username"] = "other"
    write_raw(auth_file, yaml.safe_dump(data))
    assert ga.check_cookie(ga.load_auth(auth_file), cookie, now=1001.0) is None


def test_the_store_reloads_a_changed_file(auth_file: pathlib.Path) -> None:
    store = ga.AuthStore(auth_file)
    first = store.get()
    assert first is not None
    ga.write_auth(auth_file, "wat", "another long password", **FAST)
    second = store.get()
    assert second is not None and second.secret != first.secret
    auth_file.chmod(0o644)
    assert store.get() is None  # fail closed


# ------------------------------------------------------------- client ----


@pytest.mark.parametrize(
    ("peer", "xff", "want"),
    [
        ("10.0.0.5", None, "10.0.0.5"),
        ("10.0.0.5", "1.2.3.4", "10.0.0.5"),  # untrusted peer: header ignored
        ("10.0.0.1", "1.2.3.4", "1.2.3.4"),
        ("10.0.0.1", "6.6.6.6, 1.2.3.4", "1.2.3.4"),  # the left part is spoofable
        ("10.0.0.1", "1.2.3.4, 10.0.0.1", "1.2.3.4"),  # skip trusted hops
        ("10.0.0.1", "garbage", "10.0.0.1"),
        ("10.0.0.1", "", "10.0.0.1"),
    ],
)
def test_client_address(peer: str, xff: str | None, want: str) -> None:
    assert ga.client_address(peer, xff, frozenset({"10.0.0.1"})) == want


@pytest.mark.parametrize(
    ("peer", "proto", "want"),
    [
        ("10.0.0.1", "https", True),
        ("10.0.0.1", "HTTPS", True),
        ("10.0.0.1", "http", False),
        ("10.0.0.1", None, False),
        ("10.0.0.5", "https", False),  # untrusted peer
    ],
)
def test_forwarded_https(peer: str, proto: str | None, want: bool) -> None:
    assert ga.forwarded_https(peer, proto, frozenset({"10.0.0.1"})) is want


# ------------------------------------------------------------ lockout ----


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_five_failures_in_ten_minutes_lock_the_client_for_ten_minutes() -> None:
    clock = Clock()
    lock = ga.Lockout(clock=clock)
    for _ in range(4):
        assert not lock.fail("a")
        clock.t += 60
    assert lock.locked("a") == 0
    assert lock.fail("a")  # the fifth
    assert lock.locked("a") == pytest.approx(600)
    assert lock.locked("b") == 0  # another client is not affected
    clock.t += 599
    assert lock.locked("a") > 0
    clock.t += 2
    assert lock.locked("a") == 0


def test_old_failures_fall_out_of_the_window() -> None:
    clock = Clock()
    lock = ga.Lockout(clock=clock)
    for _ in range(4):
        lock.fail("a")
    clock.t += 601
    assert not lock.fail("a")
    assert lock.locked("a") == 0


def test_a_success_clears_the_failures() -> None:
    clock = Clock()
    lock = ga.Lockout(clock=clock)
    for _ in range(4):
        lock.fail("a")
    lock.success("a")
    assert not lock.fail("a")


def test_the_table_is_bounded() -> None:
    lock = ga.Lockout(clock=Clock(), max_clients=10)
    for i in range(100):
        lock.fail(f"c{i}")
    assert len(lock) <= 10


# ------------------------------------------------------- set-password ----


def test_read_password_from_a_pipe() -> None:
    assert ga.read_password(io.StringIO(PASSWORD + "\n")) == PASSWORD


@pytest.mark.parametrize("text", ["", "\n", "short\n"])
def test_read_password_refuses_a_short_one(text: str) -> None:
    with pytest.raises(ga.AuthError):
        ga.read_password(io.StringIO(text))


@pytest.mark.parametrize("name", ["", "a b", "a/b", "x" * 65, "é"])
def test_a_bad_username_is_refused(tmp_path: pathlib.Path, name: str) -> None:
    with pytest.raises(ga.AuthError):
        ga.write_auth(tmp_path / "a.yaml", name, PASSWORD, **FAST)
