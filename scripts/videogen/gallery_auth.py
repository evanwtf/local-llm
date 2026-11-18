"""The login gate for the video-gen gallery: credentials, sessions, lockout (#1413).

`gallery_serve.py` uses this module; `gallery_serve.py set-password` writes
the credentials file with it.

**The credentials file** is YAML, outside every repository, at
`~/.config/local-llm/videogen-gallery/auth.yaml` by default:

    users:
      - username: NAME
        password_hash: "scrypt$n=32768,r=8,p=1$<salt b64>$<hash b64>"
    session_secret: "<32 random bytes, b64>"

It holds a salted scrypt hash, never the password. The loader refuses a file
whose mode is not 0600 or whose owner is another user, a file of another
shape, an unknown hash scheme, and a cost outside [MIN_N, MAX_N]. Only
`yaml.safe_load` reads it, so a tag cannot run code.

**A session** is a cookie: base64url(`user:expiry`) + "." + base64url(an
HMAC-SHA256 of that part, keyed by `session_secret`). `set-password` makes a
new secret each time, so a new password ends every open session.

**The lockout**: 5 failed logins from one client within 10 minutes lock that
client out of the login form for 10 minutes. The table lives in memory and
holds at most `max_clients` entries.
"""

from __future__ import annotations

import base64
import binascii
import collections
import dataclasses
import getpass
import hashlib
import hmac
import ipaddress
import logging
import os
import pathlib
import re
import secrets
import stat
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any, TextIO

import yaml

logger = logging.getLogger(__name__)

DEFAULT_AUTH_FILE = (
    pathlib.Path.home() / ".config" / "local-llm" / "videogen-gallery" / "auth.yaml"
)

SCHEME = "scrypt"
#: scrypt cost. n = 2^15 with r = 8 needs 32 MiB and about 0.1 s a check:
#: slow enough to make a stolen file costly, fast enough for one login.
N, R, P = 2**15, 8, 1
#: The loader refuses a cost outside these bounds: a weak hash below, and a
#: file that would make each login take gigabytes above.
MIN_N, MAX_N, MAX_R, MAX_P = 2**14, 2**17, 16, 4
SALT_BYTES, HASH_BYTES, SECRET_BYTES = 16, 32, 32
MIN_PASSWORD = 8

USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
HASH = re.compile(
    r"^scrypt\$n=(?P<n>\d+),r=(?P<r>\d+),p=(?P<p>\d+)"
    r"\$(?P<salt>[A-Za-z0-9+/=]+)\$(?P<hash>[A-Za-z0-9+/=]+)$"
)

COOKIE = "vg_session"
SESSION_S = 30 * 24 * 3600

MAX_FAILURES, WINDOW_S, LOCK_S, MAX_CLIENTS = 5, 600.0, 600.0, 4096


class AuthError(Exception):
    """The credentials file is missing, unsafe or malformed, or an input is bad."""


@dataclasses.dataclass(frozen=True)
class User:
    username: str
    n: int
    r: int
    p: int
    salt: bytes
    digest: bytes


@dataclasses.dataclass(frozen=True)
class Auth:
    users: tuple[User, ...]
    secret: bytes


# ------------------------------------------------------------ hashing ----


def scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        # scrypt needs 128 * n * r bytes; leave room above that.
        maxmem=256 * n * r + (1 << 20),
        dklen=HASH_BYTES,
    )


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as err:
        raise AuthError(f"not base64: {err}") from err


def hash_password(password: str, *, n: int = N, r: int = R, p: int = P) -> str:
    """`scrypt$n=..,r=..,p=..$<salt>$<hash>` with a new random salt."""
    salt = secrets.token_bytes(SALT_BYTES)
    return (
        f"{SCHEME}$n={n},r={r},p={p}${b64(salt)}${b64(scrypt(password, salt, n, r, p))}"
    )


def parse_user(item: Any) -> User:
    if not isinstance(item, dict) or set(item) != {"username", "password_hash"}:
        raise AuthError("each user needs exactly username and password_hash")
    name, text = item["username"], item["password_hash"]
    if not isinstance(name, str) or not USERNAME.match(name):
        raise AuthError("a username must match [A-Za-z0-9_.@-]{1,64}")
    if not isinstance(text, str) or not text.startswith(SCHEME + "$"):
        raise AuthError("unknown password hash scheme (only scrypt)")
    match = HASH.match(text)
    if match is None:
        raise AuthError("a password_hash is not scrypt$n=N,r=R,p=P$salt$hash")
    n, r, p = int(match["n"]), int(match["r"]), int(match["p"])
    if not (MIN_N <= n <= MAX_N and n & (n - 1) == 0):
        raise AuthError(f"scrypt n must be a power of 2 in [{MIN_N}, {MAX_N}]")
    if not (1 <= r <= MAX_R and 1 <= p <= MAX_P):
        raise AuthError(f"scrypt r must be 1-{MAX_R} and p 1-{MAX_P}")
    salt, digest = unb64(match["salt"]), unb64(match["hash"])
    if len(salt) < SALT_BYTES or len(digest) != HASH_BYTES:
        raise AuthError("the salt or the hash has the wrong length")
    return User(name, n, r, p, salt, digest)


def verify(auth: Auth, username: str, password: str) -> str | None:
    """The username if the pair is right, else None.

    It runs scrypt once whether or not the name exists, so the time does not
    say which field was wrong.
    """
    found: User | None = None
    for user in auth.users:
        if hmac.compare_digest(user.username.encode(), username.encode()):
            found = user
    target = found or auth.users[0]
    digest = scrypt(password, target.salt, target.n, target.r, target.p)
    ok = hmac.compare_digest(digest, target.digest)
    return found.username if found is not None and ok else None


# --------------------------------------------------------------- file ----


def check_mode(path: pathlib.Path) -> os.stat_result:
    """The file's stat, or AuthError unless it is ours and 0600."""
    try:
        st = path.stat()
    except FileNotFoundError as err:
        raise AuthError(
            f"{path} does not exist; make it with "
            "`gallery_serve.py set-password --user NAME`"
        ) from err
    if not stat.S_ISREG(st.st_mode):
        raise AuthError(f"{path} is not a regular file")
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise AuthError(
            f"{path} has mode {stat.S_IMODE(st.st_mode):04o}; it must be 0600"
        )
    if st.st_uid != os.getuid():
        raise AuthError(f"{path} belongs to uid {st.st_uid}, not to this user")
    return st


def parse_auth(text: str) -> Auth:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise AuthError(f"not valid YAML: {err}") from err
    if not isinstance(data, dict):
        raise AuthError("the file must be a mapping with users and session_secret")
    extra = set(data) - {"users", "session_secret"}
    if extra:
        raise AuthError(f"unknown keys: {sorted(map(str, extra))}")
    users = data.get("users")
    if not isinstance(users, list) or not users:
        raise AuthError("users must be a non-empty list")
    secret = data.get("session_secret")
    if not isinstance(secret, str):
        raise AuthError("session_secret must be a base64 string")
    key = unb64(secret)
    if len(key) < SECRET_BYTES:
        raise AuthError(f"session_secret must be at least {SECRET_BYTES} bytes")
    parsed = tuple(parse_user(u) for u in users)
    if len({u.username for u in parsed}) != len(parsed):
        raise AuthError("a username appears twice")
    return Auth(parsed, key)


def load_auth(path: pathlib.Path) -> Auth:
    """The credentials, or AuthError. Checks the mode before it reads."""
    check_mode(path)
    return parse_auth(path.read_text())


def write_auth(
    path: pathlib.Path,
    username: str,
    password: str,
    *,
    n: int = N,
    r: int = R,
    p: int = P,
) -> None:
    """Write one user and a new session secret, 0600, through a rename.

    The new secret ends every session made with the old one.
    """
    if not USERNAME.match(username):
        raise AuthError("a username must match [A-Za-z0-9_.@-]{1,64}")
    data = {
        "users": [
            {
                "username": username,
                "password_hash": hash_password(password, n=n, r=r, p=p),
            }
        ],
        "session_secret": b64(secrets.token_bytes(SECRET_BYTES)),
    }
    text = yaml.safe_dump(data, sort_keys=False)
    parse_auth(text)  # what we write must load
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    # mkstemp makes the file 0600 before a byte is written.
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = pathlib.Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def read_password(stream: TextIO) -> str:
    """The new password: twice with no echo on a TTY, else one line of stdin."""
    if stream.isatty():
        first = getpass.getpass("New gallery password: ")
        if getpass.getpass("Again: ") != first:
            raise AuthError("the two passwords differ")
        password = first
    else:
        password = stream.readline().rstrip("\r\n")
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"the password must be at least {MIN_PASSWORD} characters")
    return password


class AuthStore:
    """The credentials, read again whenever the file changes.

    A file that turns bad (wrong mode, removed, malformed) gives None, so
    every session and every login fails closed until it is fixed.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self._auth: Auth | None = None
        self._key: tuple[int, int, int, int] | None = None
        self._error: str | None = None
        self._lock = threading.Lock()
        self.get()

    def get(self) -> Auth | None:
        with self._lock:
            return self._get()

    def _get(self) -> Auth | None:
        try:
            st = check_mode(self.path)
            key = (st.st_ino, st.st_mtime_ns, st.st_size, st.st_ctime_ns)
            if key != self._key:
                self._auth = load_auth(self.path)
                self._key = key
                self._error = None
                logger.info("loaded %s (%d user)", self.path, len(self._auth.users))
            return self._auth
        except (AuthError, OSError) as err:
            if str(err) != self._error:
                logger.error("login disabled: %s", err)
            self._error = str(err)
            self._auth, self._key = None, None
            return None


# ------------------------------------------------------------ cookies ----


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64url(text: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None


def _sign(auth: Auth, body: str) -> str:
    return _b64url(hmac.new(auth.secret, body.encode("ascii"), hashlib.sha256).digest())


def make_cookie(auth: Auth, username: str, *, now: float) -> str:
    body = _b64url(f"{username}:{int(now) + SESSION_S}".encode())
    return f"{body}.{_sign(auth, body)}"


def check_cookie(auth: Auth, value: str, *, now: float) -> str | None:
    """The user of a valid, unexpired cookie, else None."""
    body, dot, sig = value.partition(".")
    if not dot or not body or not sig or "." in sig:
        return None
    try:
        expected = _sign(auth, body)
    except UnicodeEncodeError:
        return None
    if not hmac.compare_digest(expected, sig):
        return None
    raw = _unb64url(body)
    if raw is None:
        return None
    name, _, exp = raw.decode("utf-8", "replace").rpartition(":")
    if not exp.isdigit() or int(exp) <= now:
        return None
    if not any(u.username == name for u in auth.users):
        return None
    return name


def cookie_expiry(value: str) -> int | None:
    """The expiry (epoch seconds) inside a cookie; None if it has none.

    It does not check the signature: call check_cookie first.
    """
    raw = _unb64url(value.partition(".")[0])
    if raw is None:
        return None
    exp = raw.decode("utf-8", "replace").rpartition(":")[2]
    return int(exp) if exp.isdigit() else None


class Revoked:
    """Cookie values that logged out, kept until they expire (#1413).

    A signed cookie stays valid until its expiry, so logout alone would only
    ask the browser to forget it. This set makes the server refuse it too.
    It lives in memory: a restart forgets it, and the set holds at most
    `max_entries`; when full it drops the entry that expires first.
    """

    def __init__(self, max_entries: int = MAX_CLIENTS) -> None:
        self.max_entries = max_entries
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._until)

    def add(self, value: str, until: float, *, now: float) -> None:
        with self._lock:
            self._prune(now)
            self._until[value] = until
            while len(self._until) > self.max_entries:
                del self._until[min(self._until, key=self._until.__getitem__)]

    def __contains__(self, value: object) -> bool:
        with self._lock:
            return value in self._until

    def _prune(self, now: float) -> None:
        for key in [k for k, v in self._until.items() if v <= now]:
            del self._until[key]


# ------------------------------------------------------------- client ----


def _ip(text: str) -> str | None:
    try:
        return ipaddress.ip_address(text.strip()).compressed
    except ValueError:
        return None


def normalize_proxies(addresses: tuple[str, ...] | list[str]) -> frozenset[str]:
    out = set()
    for a in addresses:
        ip = _ip(a)
        if ip is None:
            raise AuthError(f"--trusted-proxy {a!r} is not an IP address")
        out.add(ip)
    return frozenset(out)


def client_address(
    peer: str, forwarded_for: str | None, trusted: frozenset[str]
) -> str:
    """The client: the peer, or, behind a trusted proxy, the rightmost
    X-Forwarded-For entry that is not itself a trusted proxy.

    The left part of the header is whatever the client sent, so it is never
    read.
    """
    peer_ip = _ip(peer) or peer
    if peer_ip not in trusted or not forwarded_for:
        return peer_ip
    for part in reversed(forwarded_for.split(",")):
        ip = _ip(part)
        if ip is None:
            return peer_ip
        if ip not in trusted:
            return ip
    return peer_ip


def client_key(client: str) -> str:
    """What the lockout counts: an IPv4 address, or an IPv6 /64.

    One IPv6 host usually holds a whole /64, so counting single addresses
    would let it pick a new one for every try.
    """
    try:
        ip = ipaddress.ip_address(client)
    except ValueError:
        return client
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def forwarded_https(peer: str, proto: str | None, trusted: frozenset[str]) -> bool:
    """True when a trusted proxy says the client used HTTPS."""
    if (_ip(peer) or peer) not in trusted or not proto:
        return False
    return proto.split(",")[-1].strip().lower() == "https"


# ------------------------------------------------------------ lockout ----


@dataclasses.dataclass
class _Entry:
    failures: collections.deque[float]
    locked_until: float = 0.0


class Lockout:
    """Failed logins per client, in memory, at most `max_clients` entries."""

    def __init__(
        self,
        *,
        max_failures: int = MAX_FAILURES,
        window_s: float = WINDOW_S,
        lock_s: float = LOCK_S,
        max_clients: int = MAX_CLIENTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.lock_s = lock_s
        self.max_clients = max_clients
        self.clock = clock
        self._table: collections.OrderedDict[str, _Entry] = collections.OrderedDict()
        # The server answers each connection on its own thread.
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._table)

    def locked(self, client: str) -> float:
        """Seconds left on the client's lock; 0 when it may try."""
        with self._lock:
            entry = self._table.get(client)
            if entry is None:
                return 0.0
            return max(0.0, entry.locked_until - self.clock())

    def fail(self, client: str) -> bool:
        """Record a failure. True when it locks the client."""
        with self._lock:
            return self._fail(client)

    def _fail(self, client: str) -> bool:
        now = self.clock()
        entry = self._table.pop(client, None) or _Entry(collections.deque())
        while entry.failures and entry.failures[0] <= now - self.window_s:
            entry.failures.popleft()
        entry.failures.append(now)
        locked = len(entry.failures) >= self.max_failures
        if locked:
            entry.locked_until = now + self.lock_s
            entry.failures.clear()
        self._table[client] = entry
        while len(self._table) > self.max_clients:
            self._evict(now)
        return locked

    def _evict(self, now: float) -> None:
        """Drop the oldest entry that is not locked; a locked one only when
        every entry is locked. A flood of new clients then cannot push a
        locked client out of the table."""
        for key, entry in self._table.items():
            if entry.locked_until <= now:
                del self._table[key]
                return
        self._table.popitem(last=False)

    def success(self, client: str) -> None:
        with self._lock:
            self._table.pop(client, None)
