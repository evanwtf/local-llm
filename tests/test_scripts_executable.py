"""Every driver in scripts/ is executable.

scripts/decode_ab_engine.sh lost its executable bit and nobody noticed until a
run of it failed with `Permission denied` (2026-09-06). It is one of the two
drivers behind published A/B numbers, so the bit is not cosmetic: the failure
lands at the moment someone tries to measure, and the obvious workaround --
`bash scripts/foo.sh` -- hides it again for the next person.
"""

from __future__ import annotations

import os
import pathlib

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def test_every_shell_script_is_executable():
    """A driver that is not executable fails only when someone runs it."""
    not_executable = sorted(
        p.name for p in SCRIPTS.glob("*.sh") if not os.access(p, os.X_OK)
    )
    assert not not_executable, (
        f"not executable: {not_executable} -- run chmod +x and "
        "`git update-index --chmod=+x`, so the bit survives a fresh clone"
    )


def test_every_shell_script_has_a_shebang():
    """The bit is useless without one, and the pair fails the same way."""
    missing = sorted(
        p.name
        for p in SCRIPTS.glob("*.sh")
        if not p.read_text(errors="replace").startswith("#!")
    )
    assert not missing, f"no shebang: {missing}"
