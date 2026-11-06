"""The DGX node names, from local config and never from the repo. #1153

This repo is public. Evan, 2026-10-18: keep the DGX host names out of it.
Some scripts still need them at run time: Prometheus labels each series by
`instance="<host>:<port>"`, and that label is the only one that tells the
head's GPU from the worker's.

So a script writes `$head` or `$worker` in a selector, and `fill` puts the real
name in at run time. The name comes from, in order:

1. the environment: `LOCAL_LLM_DGX_HEAD`, `LOCAL_LLM_DGX_WORKER`;
2. the untracked file `~/.config/local-llm/hosts.toml`:

       [dgx]
       head = "<head host name>"
       worker = "<worker host name>"

There is no default. A missing name raises `HostUnknown`, which says how to
set it. A guessed name would query a series that does not exist, and an empty
answer reads like an idle machine.
"""

from __future__ import annotations

import os
import pathlib
import string
import tomllib
from collections.abc import Mapping

ROLES = ("head", "worker")
ENV = {"head": "LOCAL_LLM_DGX_HEAD", "worker": "LOCAL_LLM_DGX_WORKER"}
HOSTS_FILE = pathlib.Path.home() / ".config" / "local-llm" / "hosts.toml"


class HostUnknown(RuntimeError):
    """No local config names this node."""


def _from_file(role: str, path: pathlib.Path) -> str | None:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise HostUnknown(f"cannot read {path}: {err}") from err
    value = data.get("dgx", {}).get(role)
    return value if isinstance(value, str) and value else None


def lookup(
    role: str,
    env: Mapping[str, str] | None = None,
    path: pathlib.Path | None = None,
) -> str:
    """The host name for `role` ("head" or "worker"). Raises HostUnknown."""
    if role not in ROLES:
        raise KeyError(role)
    env = os.environ if env is None else env
    path = HOSTS_FILE if path is None else path
    value = env.get(ENV[role]) or _from_file(role, path)
    if value:
        return value
    raise HostUnknown(
        f"the DGX {role} host name is not configured. Set {ENV[role]}, or add "
        f'`{role} = "<host>"` under [dgx] in {path}. It stays out of the '
        "public repo (#1153)."
    )


def fill(
    text: str,
    env: Mapping[str, str] | None = None,
    path: pathlib.Path | None = None,
) -> str:
    """`text` with `$head` and `$worker` replaced. Looks up only what it names."""
    template = string.Template(text)
    names = {n: lookup(n, env, path) for n in template.get_identifiers()}
    return template.substitute(names)
