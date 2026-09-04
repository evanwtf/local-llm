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


#: How OpenCode updates itself, found by inspecting the shipped binary on
#: 2026-09-04. Recorded here rather than only in a comment on #131, because
#: "we turned it off" is not reproducible on a fresh install.
OPENCODE_AUTOUPDATE_ENV = "OPENCODE_DISABLE_AUTOUPDATE"
OPENCODE_CONFIG = pathlib.Path.home() / ".config" / "opencode" / "opencode.json"


def opencode_autoupdate_disabled(
    env: dict[str, str] | None = None, config: pathlib.Path | None = None
) -> tuple[bool, str]:
    """Is OpenCode's self-update turned off? Returns (disabled, how we know).

    Two independent switches, either of which is enough:

    * the environment variable `OPENCODE_DISABLE_AUTOUPDATE`
    * `"autoupdate": false` in `~/.config/opencode/opencode.json`

    Both names come from strings in the shipped binary. A deliberate upgrade
    is `opencode upgrade [target]`, which is the path a pin move should take.

    This only reports. Changing an operator's client config is not preflight's
    business, and a benchmark harness silently editing the tool under test is
    exactly the class of thing #131 exists to prevent.
    """
    import os

    env = os.environ if env is None else env
    if env.get(OPENCODE_AUTOUPDATE_ENV):
        return True, f"{OPENCODE_AUTOUPDATE_ENV} is set"
    path = OPENCODE_CONFIG if config is None else config
    try:
        import json

        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return False, f"no {OPENCODE_AUTOUPDATE_ENV}, and {path.name} unreadable"
    if isinstance(raw, dict) and raw.get("autoupdate") is False:
        return True, f'"autoupdate": false in {path.name}'
    return False, (
        f'neither {OPENCODE_AUTOUPDATE_ENV} nor "autoupdate": false in {path.name}'
    )
