"""Check that every backend's `opencode_model` resolves in OpenCode's config.

#69. `ds4/glm-5.3-flash` was never declared in ~/.config/opencode/opencode.json,
so `opencode run` exited in 0.6s. Six client crashes were recorded as six model
failures with an empty stderr_tail, and that became GLM's entire published
OpenCode record. One lookup would have caught it before the first trial.

The config lives outside the repo and is not version-controlled; `config/
opencode.json` is a tracked reference copy, not the file OpenCode reads.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib

logger = logging.getLogger(__name__)

CONFIG = pathlib.Path(
    os.environ.get("OPENCODE_CONFIG", "~/.config/opencode/opencode.json")
).expanduser()


def declared_models(config: pathlib.Path = CONFIG) -> set[str] | None:
    """Every `provider/model` OpenCode can resolve, or None if unreadable.

    None means "cannot tell", which is not the same as "nothing declared" --
    the caller must not report a missing model on the strength of a missing
    file.
    """
    try:
        data = json.loads(config.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    out: set[str] = set()
    for provider, spec in (data.get("provider") or {}).items():
        for model in spec.get("models") or {}:
            out.add(f"{provider}/{model}")
    return out


def missing(backends: dict[str, dict], config: pathlib.Path = CONFIG) -> list[str]:
    """Backends whose opencode_model is not declared. Empty if none, or if
    the config cannot be read."""
    declared = declared_models(config)
    if declared is None:
        return []
    return sorted(
        f"{name} -> {spec['opencode_model']}"
        for name, spec in backends.items()
        if spec.get("opencode_model")
        and spec["opencode_model"] not in declared
        # A backend belonging to another machine's tier, or a retired one, will
        # never run here, so warning about its client declaration is noise --
        # and noise in a check that exists to catch #69 is how a real warning
        # gets skimmed past. run.py already drops both from the default matrix.
        and not spec.get("tier")
        and not spec.get("retired")
    )


def log_report(backends: dict[str, dict], config: pathlib.Path = CONFIG) -> None:
    declared = declared_models(config)
    if declared is None:
        logger.warning(
            "opencode: cannot read %s -- an undeclared model exits in 0.6s and "
            "records as a model failure (#69)",
            config,
        )
        return
    gaps = missing(backends, config)
    for gap in gaps:
        logger.warning(
            "opencode: %s is NOT declared in %s -- the client will exit before "
            "running and every trial will look like a model failure (#69)",
            gap,
            config,
        )
    if not gaps:
        wanted = sum(1 for s in backends.values() if s.get("opencode_model"))
        logger.info("opencode: all %d opencode_model entries resolve", wanted)
