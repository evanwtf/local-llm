"""Run a vaulted shell where its siblings still resolve (#235).

The retired drivers in `vault/` find their helpers with
`$(dirname "$0")/lib/metal_knob.py` and friends. Three of those helpers are
live Python and stayed in `scripts/`, so inside the archive those paths now
point at nothing:

    vault/metal_knob_ab.sh   ->  lib/metal_knob.py     (lives in scripts/lib/)
    vault/decode_ab.sh       ->  prompt_meta.py        (lives in scripts/)
    vault/restart_*.sh       ->  kv_prefix_audit.py    (lives in scripts/)

That breakage is correct: nothing in `vault/` may run, and one that tried
would fail at the first helper. But a **differential** still has to execute the
shell's text to prove the port agrees with it, and it needs the helpers the
shell had on the day it was retired.

The fix is a temporary directory that looks like the old `scripts/`: the shell
itself, plus symlinks to everything live. Symlinks, not copies -- a copy of
`metal_knob.py` inside the archive would be a second implementation of a live
library, drifting silently, which is the whole class of bug the vault exists
to end.

`vault/lib/ds4_server.sh` and `vault/lib/mlx_serve.sh` moved WITH their callers
and resolve on their own; they are linked here too so a caller sees one `lib/`.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
VAULT = ROOT / "vault"


def neighbourhood(tmp_path: pathlib.Path, shell: str) -> pathlib.Path:
    """A directory where `vault/<shell>` sees its original siblings.

    Returns the path to run: `bash <that path>`. `$0`'s dirname is the temp
    directory, so every `$(dirname "$0")/...` inside the script resolves to a
    live helper, and `../benchmarks/agent/preflight.py` resolves the way it did
    from `scripts/` because the directory sits one level under the repo root.
    """
    home = tmp_path / "scripts-view"
    home.mkdir(parents=True, exist_ok=True)
    lib = home / "lib"
    lib.mkdir(exist_ok=True)

    for src in sorted(SCRIPTS.iterdir()):
        if src.is_file():
            (home / src.name).symlink_to(src)
    for src in sorted((SCRIPTS / "lib").iterdir()):
        if src.is_file():
            (lib / src.name).symlink_to(src)
    for src in sorted((VAULT / "lib").glob("*.sh")):
        target = lib / src.name
        if not target.exists():
            target.symlink_to(src)

    placed = home / shell
    if placed.exists() or placed.is_symlink():
        placed.unlink()
    placed.symlink_to(VAULT / shell)
    return placed
