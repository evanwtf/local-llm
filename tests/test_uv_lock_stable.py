"""The lockfile round-trips, so a bare `uv run` no longer stamps every row
dirty. #101.

uv 0.12.9 rewrote `uv.lock` on **every** `uv run`: the 7-day supply-chain
cooldown lived in `uv.lock` as `[options] exclude-newer-span = "P7D"` while
being **undeclared** in `pyproject.toml`, so a bare invocation had no cooldown
to apply and stripped the `[options]` block. The harness runs under `uv run`
and samples `git status`, so the tree was dirty by the time it looked -- every
log line and every `results.jsonl` row on the Ryzen / RTX 3080 Ti desktop read
`-dirty`, and the flag distinguished nothing (`harness_dirty` had the same
defect). The cause and the four costs are in #101.

`6f9ef8e` fixed it by declaring `[tool.uv] exclude-newer = "P7D"` in
`pyproject.toml`. uv 0.9.17+ then applies the cooldown on every invocation, the
lock round-trips, and a bare `uv run` leaves it untouched.

These tests are the guard. Remove the declaration and a bare `uv run` prints
"Resolving despite existing lockfile due to removal of global exclude newer"
and rewrites the lock -- reintroducing the churn silently. The tests are
structural and offline: they read the two files, not the resolver.
"""

from __future__ import annotations

import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_pyproject_declares_the_supply_chain_cooldown() -> None:
    """`[tool.uv] exclude-newer` must be declared, or the churn returns.

    This is the exact line whose absence let a bare `uv run` strip the lock's
    `[options]` block on every call.
    """
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    exclude_newer = data.get("tool", {}).get("uv", {}).get("exclude-newer")
    assert exclude_newer, (
        "pyproject [tool.uv] exclude-newer is not declared -- a bare `uv run` "
        "will strip uv.lock's [options] block on every call and stamp every "
        "row dirty (#101)."
    )


def test_uv_lock_carries_the_matching_cooldown_span() -> None:
    """uv.lock's `[options]` block must carry the span the declaration applies.

    When the declaration is removed, uv rewrites the lock and drops this key.
    So a lock that still carries it is a lock that round-trips -- the two files
    agree, and the next `uv run` changes neither.
    """
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    span = lock.get("options", {}).get("exclude-newer-span")
    assert span == "P7D", (
        f"uv.lock [options] exclude-newer-span is {span!r}, not 'P7D'. If it is "
        "absent, the lock was written by a `uv run` with no cooldown declared "
        "and no longer round-trips (#101)."
    )
