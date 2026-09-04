"""Read the pinned agent client versions, and say what drifted (#131).

Every backend comparison assumes the client is a constant. #104 measured
OpenCode 1.18.26 -> 1.18.27 roughly doubling median turns with everything
else held, and no row recorded a client version at the time, so the finding
could not be applied backwards.

`client_version` on the row closed the recording half (225b90c). This is the
protecting half: a pin, and a check that refuses rather than warns.

The asymmetry is deliberate and matches `preflight`'s existing split. Process
detection is inferential, so it warns. A pin is a declaration -- somebody
wrote this version down on purpose -- so a mismatch is an error.
"""

from __future__ import annotations

import pathlib
import tomllib

DEFAULT_PINS = pathlib.Path(__file__).resolve().parent.parent / "client-pins.toml"


def load_pins(path: pathlib.Path = DEFAULT_PINS) -> dict[str, str]:
    """name -> pinned version. Absent file or section means no pins.

    An empty string means "present but deliberately unpinned" and is dropped
    here, so callers cannot confuse it with a pin of "".
    """
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    clients = raw.get("clients")
    if not isinstance(clients, dict):
        return {}
    return {
        str(k): str(v).strip()
        for k, v in clients.items()
        if isinstance(v, str) and v.strip()
    }


def drift(
    installed: dict[str, str | None], pins: dict[str, str]
) -> list[tuple[str, str, str]]:
    """(name, pinned, installed) for every client that does not match its pin.

    A client that is pinned but not installed is reported with "not found":
    the pin says this machine is supposed to have it, so its absence is drift
    too, not something to skip over quietly.

    Matching is on the version numbers found in the string, so
    "ollama version is 0.33.3", "codex-cli 0.152.0" and "0.152.0" all compare
    equal to a pin of "0.152.0". The tools do not agree on a format and the
    pin file should not have to encode each one's decoration.
    """
    from staleness import parse  # local import: preflight owns sys.path

    out: list[tuple[str, str, str]] = []
    for name, want in sorted(pins.items()):
        got = installed.get(name)
        if got is None:
            out.append((name, want, "not found"))
            continue
        if parse(got) != parse(want):
            out.append((name, want, got.strip()))
    return out
