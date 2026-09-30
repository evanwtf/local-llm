"""Refuse to run a Metal-lane tool off macOS (#325).

**Why this exists.** The ds4 Metal A/B drivers export `DS4_METAL_*` knobs and
read the Metal route from the engine's log. On the DGX (Linux, CUDA) those
knobs mean nothing, so a run there would produce a confusing result rather
than an error. The drivers were Metal-only by existence; this makes them
Metal-only by assertion.

Tests run these drivers on the Linux CI runner with every engine faked, so
`conftest.py` sets `LOCAL_LLM_ANY_PLATFORM=1` for the whole suite. Nothing else
should set it.
"""

from __future__ import annotations

import os
import sys

#: The escape for the test suite, which fakes every engine.
ENV = "LOCAL_LLM_ANY_PLATFORM"


def require_darwin(tool: str) -> None:
    """Exit with a refusal unless this is macOS, where the Metal engine runs."""
    if sys.platform == "darwin" or os.environ.get(ENV) == "1":
        return
    raise SystemExit(
        f"REFUSING: {tool} is a Metal-lane tool (the M5 Max); it drives ds4's "
        f"Metal build and its DS4_METAL_* knobs, which mean nothing on "
        f"{sys.platform}. Run it on the Mac (#325)."
    )
