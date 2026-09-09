"""Read the recorded agent client versions, and say which have moved (#131).

Every backend comparison assumes the client is a constant. #104 measured
OpenCode 1.18.26 -> 1.18.27 roughly doubling median turns with everything
else held, and no row recorded a client version at the time, so the finding
could not be applied backwards.

This module used to hold the *protecting* half of that: a pin, and a check
that refused to run when the installed client differed from it. On
2026-09-04 the operator removed the pinning and kept the recording, and the
reason is worth stating because it is not a retreat.

**This machine is a daily driver first and a benchmark rig second.** Pinning
the agent clients means holding a developer's own tools back to serve a
measurement. The operator wants the current version of everything and
accepts that comparability across a client update is recovered afterwards
instead of prevented. A guard that would be overridden every time is worse
than no guard: it trains people to type the override without reading it.

So nothing here refuses. What makes the trade safe is the row:
`client_version` is on every result, `scripts/client_version_split.py`
splits a comparison after the fact, and the published tables caveat
themselves through `client_caveat()` in `benchmarks/agent/gen_tables.py`.
Recording the version in `client-versions.toml` dates the boundary; the row
is what makes it recoverable.
"""

from __future__ import annotations

import pathlib
import tomllib

DEFAULT_RECORD = pathlib.Path(__file__).resolve().parent.parent / "client-versions.toml"


def load_recorded(path: pathlib.Path = DEFAULT_RECORD) -> dict[str, str]:
    """name -> last recorded version. Absent file or section means nothing.

    An empty string means "this machine does not drive it" and is dropped
    here, so callers cannot confuse it with a recorded version of "".

    Reads `[reference]`, and `[clients]` as well, so a checkout that still
    has the old pin file is read rather than silently treated as empty.
    """
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    clients = raw.get("reference")
    if not isinstance(clients, dict):
        clients = raw.get("clients")
    if not isinstance(clients, dict):
        return {}
    return {
        str(k): str(v).strip()
        for k, v in clients.items()
        if isinstance(v, str) and v.strip()
    }


def moved_since(
    installed: dict[str, str | None], recorded: dict[str, str]
) -> list[tuple[str, str, str]]:
    """(name, recorded, installed) for every client that has moved.

    Nothing here refuses; this names a series boundary. A client that is
    recorded but not installed is reported with "not found" -- it cannot take
    a row, so it cannot corrupt a comparison, and the caller says so.

    Matching is on the version numbers found in the string, so
    "ollama version is 0.33.3", "codex-cli 0.152.0" and "0.152.0" all compare
    equal to a recorded "0.152.0". The tools do not agree on a format and this
    file should not have to encode each one's decoration.
    """
    from staleness import parse  # local import: preflight owns sys.path

    out: list[tuple[str, str, str]] = []
    for name, want in sorted(recorded.items()):
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
        return True, f"off ({OPENCODE_AUTOUPDATE_ENV} is set)"
    path = OPENCODE_CONFIG if config is None else config
    try:
        import json

        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return False, f"on (no {OPENCODE_AUTOUPDATE_ENV}, {path.name} unreadable)"
    if isinstance(raw, dict) and raw.get("autoupdate") is False:
        return True, f'off ("autoupdate": false in {path.name})'
    return False, (
        f'on (neither {OPENCODE_AUTOUPDATE_ENV} nor "autoupdate": false in {path.name})'
    )


#: How each client updates itself, read out of the shipped binaries on
#: 2026-09-04 with `strings`. Recorded in code rather than in prose because
#: "we turned it off" is not reproducible on a fresh install, and because the
#: operator's decision is to leave every one of these ON -- this laptop is a
#: daily driver and the tools should stay current. The point of knowing the
#: switch is to be able to say, in a log, that the version can move.
CLAUDE_AUTOUPDATE_ENV = "DISABLE_AUTOUPDATER"
CLAUDE_SETTINGS = pathlib.Path.home() / ".claude" / "settings.json"
CODEX_CONFIG = pathlib.Path.home() / ".codex" / "config.toml"


def claude_autoupdate_disabled(
    env: dict[str, str] | None = None, config: pathlib.Path | None = None
) -> tuple[bool, str]:
    """Is Claude Code's self-update off? Returns (disabled, how we know).

    Two switches, either sufficient: the `DISABLE_AUTOUPDATER` environment
    variable, or `"autoUpdates": false` in `~/.claude/settings.json`. Both
    names are strings in the shipped binary.

    Claude Code installs as `~/.local/bin/claude` symlinked into
    `~/.local/share/claude/versions/<version>`, so every version it has ever
    installed is still on disk and a downgrade is a symlink repoint --
    `ln -sfn ~/.local/share/claude/versions/<v> ~/.local/bin/claude`.
    """
    import os

    env = os.environ if env is None else env
    if env.get(CLAUDE_AUTOUPDATE_ENV):
        return True, f"off ({CLAUDE_AUTOUPDATE_ENV} is set)"
    path = CLAUDE_SETTINGS if config is None else config
    try:
        import json

        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return False, f"on (no {CLAUDE_AUTOUPDATE_ENV}, and {path.name} unreadable)"
    if isinstance(raw, dict) and raw.get("autoUpdates") is False:
        return True, f'off ("autoUpdates": false in {path.name})'
    return False, (
        f'on (neither {CLAUDE_AUTOUPDATE_ENV} nor "autoUpdates": false in {path.name})'
    )


def codex_autoupdate_disabled(
    config: pathlib.Path | None = None,
) -> tuple[bool, str]:
    """Is Codex's self-update off? Returns (disabled, how we know).

    The switch is `auto_update_enabled = false` in `~/.codex/config.toml`;
    the binary carries it as `autoUpdateEnabled`. Codex installs as
    `~/.local/bin/codex` -> `~/.codex/packages/standalone/current` ->
    `releases/<version>-<arch>`, so a version move is a repoint of `current`
    and the previous release stays on disk.
    """
    path = CODEX_CONFIG if config is None else config
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False, f"on (cannot read {path.name})"
    if raw.get("auto_update_enabled") is False:
        return True, f"off (auto_update_enabled = false in {path.name})"
    return False, f"on (no auto_update_enabled = false in {path.name})"


def autoupdate_status() -> dict[str, str]:
    """Every client's self-update state, as one line each for the log.

    Reported on every preflight. With nothing pinned, this is the sentence
    that tells a reader the version under a batch can move mid-batch -- which
    is exactly what happened to OpenCode on 2026-09-02 and to Claude Code on
    2026-09-04.
    """
    return {
        "opencode": opencode_autoupdate_disabled()[1],
        "claude": claude_autoupdate_disabled()[1],
        "codex": codex_autoupdate_disabled()[1],
    }


#: The command that deliberately moves each client to a chosen version. Named
#: here so the preflight warning can print the exact line rather than telling
#: someone to go and find out.
UPGRADE_COMMAND = {
    "opencode": "opencode upgrade",
    "claude": "claude update",
    "codex": "codex update",
}


def behind_latest(
    installed: dict[str, str | None], latest: dict[str, str | None]
) -> list[tuple[str, str, str]]:
    """(name, installed, latest) for every client older than its release.

    The operator's rule for this machine is **run the current version of
    everything**; it is a daily driver, and the benchmark rig gives way to
    that. Nothing here upgrades anything -- a harness that silently updates
    the tool under test is the class of thing #131 exists to prevent, and it
    would move the version mid-batch, which is the exact failure being
    avoided. It reports, with the command to run.

    A client whose latest release cannot be read is skipped rather than
    reported as current. Not knowing is not the same as being up to date, and
    `staleness.latest_versions()` returns None for a lookup that failed.
    """
    from staleness import parse  # local import: preflight owns sys.path

    out: list[tuple[str, str, str]] = []
    for name, want in sorted(latest.items()):
        got = installed.get(name)
        if got is None or want is None:
            continue
        here, there = parse(got), parse(want)
        if here is None or there is None:
            continue
        if here < there:
            out.append((name, got.strip(), want.strip()))
    return out
