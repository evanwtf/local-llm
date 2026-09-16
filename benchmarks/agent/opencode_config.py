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
import sys

logger = logging.getLogger(__name__)


class _Unset:
    """Sentinel: `own_tier` was not supplied, so probe the machine.

    None is a real value here -- "this machine has no tier" -- so it cannot
    double as "not given".
    """


UNSET = _Unset()

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


def _own_tier() -> str | None:
    """This machine's tier, or None if it cannot be determined.

    Imported lazily and defensively: this guard must never be the reason a run
    fails to start. If the lookup raises, every tier is treated as foreign,
    which is the behavior that held before #345 -- quieter than it should be,
    but never louder or fatal.
    """
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
        import hardware_id

        return hardware_id.this_machine_tier()
    except Exception:  # pragma: no cover - a broken probe must not block a run
        logger.debug("could not determine this machine's tier", exc_info=True)
        return None


def missing(
    backends: dict[str, dict],
    config: pathlib.Path = CONFIG,
    own_tier: str | None | _Unset = UNSET,
) -> list[str]:
    """Backends whose opencode_model is not declared. Empty if none, or if
    the config cannot be read.

    `own_tier` is this machine's tier; pass it explicitly in tests. A backend
    on ANOTHER machine's tier is skipped, because it will never run here and a
    warning about it is noise -- and noise in a check that exists to catch #69
    is how a real warning gets skimmed past.

    A backend on THIS machine's tier is checked like any untiered one. That is
    #345: until a second Linux box existed, "has a tier" and "is foreign" were
    the same statement, so the code read the first to mean the second. On the
    DGX Spark all ten backends carry `gb10-spark`, so the old form disabled
    this guard entirely on the one machine where every backend would trip it.
    """
    declared = declared_models(config)
    if declared is None:
        return []
    tier = _own_tier() if isinstance(own_tier, _Unset) else own_tier
    return sorted(
        f"{name} -> {spec['opencode_model']}"
        for name, spec in backends.items()
        if spec.get("opencode_model")
        and spec["opencode_model"] not in declared
        and not _is_foreign_tier(spec, tier)
        and not spec.get("retired")
    )


def _is_foreign_tier(spec: dict, own_tier: str | None) -> bool:
    """True when this backend belongs to hardware this is not.

    The rule is symmetric: a machine can serve exactly the backends whose tier
    matches its own, and "untiered" is a tier like any other -- it is the M5
    Max's, because that is the hardware the default matrix assumes.

    So on the DGX Spark the seventeen `gb10-spark` backends are ours and the
    thirty-four untiered ones are the Mac's, which is why checking their client
    declarations here produced seventeen unactionable warnings before this was
    made symmetric. On the Mac, `own_tier` is None and the behavior is
    identical to what it was before #345: untiered checked, every tier skipped.
    """
    return (spec.get("tier") or None) != own_tier


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
    # #301: a static small_model loads a SECOND model beside the one being
    # measured. Reported beside the #69 check because both are "the client's
    # config decides whether this batch measures what it claims to".
    selected = {
        s["opencode_model"] for s in backends.values() if s.get("opencode_model")
    }
    conflict = small_model_conflict(selected, config)
    if conflict:
        logger.warning("opencode: %s", conflict)


def sampling_for(model: str, config: pathlib.Path | None = None) -> dict | None:
    """The sampler options OpenCode is configured to send for `model`.

    None means "cannot tell" -- an unreadable config or a model that is not
    declared -- and is not the same as "sends nothing". A caller must not
    refuse a run on the strength of a missing file.

    #151: ds4 reaches its Qwen MTP path only at `temperature <= 0.0f`
    (`ds4.c:80120 at ds4-metal ba01f5d`), and a request that omits the field
    gets `DS4_DEFAULT_TEMPERATURE`, `1.0f` (`ds4.h:56 at ds4-metal ba01f5d`).
    OpenCode sends no temperature unless its config sets one, so an MTP arm
    driven by it never speculates. This is where the harness can see that
    before a trial rather than after.

    `config=None` resolves to `CONFIG` at call time, not at definition time.
    A `config: pathlib.Path = CONFIG` default binds the module-level value
    when the function is defined, so a test that points `CONFIG` at a fixture
    still reads the operator's real config and passes for the wrong reason --
    which is how the first version of these tests behaved.
    """
    config = CONFIG if config is None else config
    try:
        data = json.loads(config.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    provider, _, name = model.partition("/")
    spec = ((data.get("provider") or {}).get(provider) or {}).get("models") or {}
    if name not in spec:
        return None
    return dict((spec[name] or {}).get("options") or {})


def small_model_conflict(selected: set[str], config: pathlib.Path = CONFIG):
    """A complaint when `small_model` names a model no selected backend serves.

    #301. OpenCode uses `small_model` for incidental work -- session titles,
    summaries -- *during* a session whose main model is whatever the harness
    selected. So a static `small_model` loads a SECOND model, on a second
    engine, beside the backend under measurement.

    That is a confound anywhere. On unified memory it is fatal: measuring
    `qwen38fnq3dgx` (83.81 GiB resident in llama.cpp) with `small_model` on
    `ollama/qwen3.6:27b-coding` (25 GB) put 109 GB of weights across two
    engines on a 121.7 GiB machine, and the kernel OOM-killed llama-server.

    Absent is safe and is the configuration this checks for. A value equal to
    one of the selected backends' own models is also safe -- it is the model
    already resident. Anything else is the failure.

    Returns None when there is nothing to say, so the caller can `if`.
    """
    try:
        data = json.loads(config.read_text())
    except (OSError, json.JSONDecodeError):
        # Same rule as declared_models: "cannot tell" is not "nothing set".
        return None
    small = data.get("small_model")
    if not small or small in selected:
        return None
    return (
        f"opencode small_model is {small!r}, which no selected backend serves "
        f"({sorted(selected)}). OpenCode loads it BESIDE the backend under "
        f"measurement, on whatever engine it names -- 109 GB across two "
        f"engines is what OOM-killed llama-server in #301. Remove it from "
        f"{config}, or point it at the model being measured."
    )
