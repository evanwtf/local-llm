"""Score how relevant an outside claim is to THIS project, procedurally (#230).

The sweep skill has always carried a one-line filter -- *would this change a
number on an M5 Max, 128 GB, Metal?* -- and it is a good question that two
people answer differently. This turns it into a procedure.

The design rule is that **no axis is a judgment call**. Each one resolves
against a registry that already exists in this repo and is maintained for
another reason, so the rubric cannot drift away from what we actually run:

    hardware   hardware/<dir>            the machines we own
    model      tasks.toml `model =`      the models we actually serve
    engine     tasks.toml `engine =`     the engines we actually run
    metric     a fixed table below       what the claim measured
    effect     a fixed table below       against the instrument that would
                                         have to resolve it

The output is **green, yellow or red**, and the reasons. Three colors on
purpose: a 0-100 score, or a P0-P3 tier, would imply a precision this does not
have and would read as science. It is triage. Green means spend the machine on
it, yellow means it needs something before it can be queued, red means it does
not belong in this queue -- and the reasons say which, because a verdict with
no reason is a number nobody can argue with.

    uv run python scripts/relevance_score.py \\
        --hardware "M5 Max" --model "Qwen3.8-Flash-Next" --engine ds4 \\
        --metric prefill-tps --effect 10 --exclusivity only-us \\
        --evidence https://x.com/someone/status/123

## Three rules that do most of the de-subjectivizing

**`unstated` is a question, not a low score.** A claim that names no hardware
is not 25% relevant; it is unscoreable, and the useful output is the question
to go ask. On 2026-09-08 a verified 110 t/s claim on our exact model named no
machine at all -- scoring it would have been inventing the missing axis.

**A hardware mismatch is a gate, not a penalty.** #171 measured this: a CUDA
Q4 prefill regression of -12.23% that matched us on model, engine and use case
had *no Metal analog* -- three runs at +0.5%, -0.9%, +0.0%. Three of four
axes matching bought a null. So a non-Apple result can never take a machine
slot. It lands in CONTEXT instead, which is not zero: @iammac2's ROCm result
on `bbf5a796` is what told us the Metal half of that commit was unanswered,
and it set up the measurement that found -20.6%.

**An effect below our instrument's resolution scores down, not up.** However
well a claim matches, we cannot answer it with the tools we have. #228: the
upstream claim was +3.8% prefill and the agent harness resolves 17-26% paired
wall, so an A/B there buys a 3.5-hour null. That is a property of the claim's
size against our kit, and it is arithmetic rather than taste.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import re
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parent.parent
TASKS = REPO / "benchmarks" / "agent" / "tasks.toml"
HARDWARE = REPO / "hardware"

UNSTATED = "unstated"

# What each instrument can actually resolve, in percent. These are measured
# numbers with issues behind them, not estimates, and they are the reason an
# effect size can be scored rather than admired.
#
#   agent wall  ~17-26% paired at n=60/arm -- printed in every run-record.txt
#               and re-confirmed by #225, which spent 3.5 hours to learn the
#               harness cannot separate two mlx-serve builds.
#   ds4-bench   within-run repeat spread at one frontier, 1.5-2.6 pp across
#               the six knob runs and the three #228 runs on 2026-09-08.
#   pass rate   #112's strip-on/strip-off separation, 23 points at n=60/arm,
#               Fisher p = 0.004.
RESOLUTION: dict[str, float] = {
    "agent-wall": 17.0,
    "pass-rate": 10.0,
    "decode-tps": 2.6,
    "prefill-tps": 2.6,
    "quality": 5.0,
}

# Metrics this project has measured as predictive of the thing it cares about
# -- a coding agent you would actually use.
PREDICTIVE = {"agent-wall", "pass-rate", "quality"}

# Rate metrics, with the caveat each one has actually earned. Decode rate has
# a measured negative result behind it; prefill has no result either way, and
# saying otherwise would be citing #138/#146/#225 for something they did not
# test. Two entries rather than one set, because the difference is the whole
# point of citing at all.
RATE_CAVEAT: dict[str, str] = {
    "decode-tps": (
        "decode rate has failed three times here to predict agent wall time "
        "(#138, #146, #225): a reason to test, not a number to repeat"
    ),
    "prefill-tps": (
        "prefill rate is a rate metric and has never been shown here to "
        "predict agent wall time either way -- not evidence against it, but "
        "not the outcome this project ranks on"
    ),
}
NOT_PREDICTIVE = set(RATE_CAVEAT)

# Can anyone else answer it? Deliberately three names and no numbers -- a
# weight here would be invented, and inventing one is how a rubric starts
# looking like a measurement.
#
#   only-us  a hardware gate in the source, or a build nobody else has.
#            c1909040 gates on [g_device.name hasPrefix:@"Apple M5"] and says
#            its M5 numbers are unverified. Highest-value item of 2026-09-08.
#   few      a handful of people could, and might not.
#   anyone   any Mac owner can reproduce it, so it keeps.
EXCLUSIVITY = ("only-us", "few", "anyone")


def _read_tasks() -> dict:
    if not TASKS.is_file():
        raise SystemExit(f"REFUSING: {TASKS} is missing; the registries live there")
    return tomllib.loads(TASKS.read_text(encoding="utf-8"))


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation that differs between how people write a
    name and how a config file does. 'Qwen 3.8 Flash Next' and
    'qwen3.8-flash-next' must resolve to the same token."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def known_models(tasks: dict | None = None) -> set[str]:
    """Every model this project actually serves, from tasks.toml."""
    tasks = tasks if tasks is not None else _read_tasks()
    out = set()
    for backend in tasks.get("backend", {}).values():
        model = backend.get("model")
        if model:
            out.add(str(model))
    return out


def known_engines(tasks: dict | None = None) -> set[str]:
    """Every engine this project actually runs, from tasks.toml."""
    tasks = tasks if tasks is not None else _read_tasks()
    out = set()
    for backend in tasks.get("backend", {}).values():
        engine = backend.get("engine")
        if engine:
            out.add(str(engine))
    return out


def known_hardware() -> set[str]:
    """The machines we own, from the hardware/ directory names."""
    if not HARDWARE.is_dir():
        return set()
    return {d.name for d in HARDWARE.iterdir() if d.is_dir()}


def this_machine() -> str:
    """The directory for the Mac this queue is about."""
    for name in sorted(known_hardware()):
        if "M5-Max" in name:
            return name
    return ""


def match_hardware(claimed: str) -> str:
    """exact | apple-other | our-other-tier | foreign | unstated.

    'exact' is this Mac. 'our-other-tier' is the Linux/RTX box, which is a real
    machine we own and a different queue. 'apple-other' is an M1-M4 result,
    which the sweep skill has always treated as a lead rather than noise --
    most developers have no M5 and an improvement there usually shows up here.
    """
    if not claimed or claimed.strip().lower() == UNSTATED:
        return UNSTATED
    n = _normalize(claimed)
    if "m5" in n:
        return "exact"
    if re.search(r"m[1-4]\b", claimed.lower()) or "apple" in n or "mac" in n:
        return "apple-other"
    other = {_normalize(h) for h in known_hardware() if "M5-Max" not in h}
    if any(tok and tok in n for tok in ("rtx", "ryzen")) or n in other:
        return "our-other-tier"
    return "foreign"


def match_registry(claimed: str, registry: set[str]) -> str:
    """exact | related | foreign | unstated, against a repo registry.

    'related' is a substring match in either direction: 'qwen3.8-flash-next-mtplx'
    against our 'mtplx-qwen38-27b-optimized-speed' is the same model family in
    a different build, which is a different claim but not an unrelated one.
    """
    if not claimed or claimed.strip().lower() == UNSTATED:
        return UNSTATED
    n = _normalize(claimed)
    norm = {_normalize(r): r for r in registry}
    if n in norm:
        return "exact"
    for key in norm:
        if key and (key in n or n in key):
            return "exact"
    # Family match: share a distinctive alphanumeric run of 5+ characters.
    for key in norm:
        for chunk in re.findall(r"[a-z0-9]{5,}", key):
            if chunk in n:
                return "related"
    return "foreign"


def match_metric(claimed: str) -> str:
    if not claimed or claimed.strip().lower() == UNSTATED:
        return UNSTATED
    key = claimed.strip().lower()
    if key in RESOLUTION:
        return key
    return "other"


def effect_verdict(effect: float | None, metric: str) -> str:
    """above-resolution | below-resolution | unstated | no-instrument."""
    if effect is None:
        return UNSTATED
    if metric not in RESOLUTION:
        return "no-instrument"
    return (
        "above-resolution" if abs(effect) >= RESOLUTION[metric] else "below-resolution"
    )


def score(
    hardware: str,
    model: str,
    engine: str,
    metric: str,
    effect: float | None,
    exclusivity: str,
) -> dict:
    """Resolve every axis, then apply the gates in order. Returns the verdict
    and the reasons; the reasons are the point."""
    if exclusivity not in EXCLUSIVITY:
        raise SystemExit(
            f"REFUSING: exclusivity must be one of {sorted(EXCLUSIVITY)}, "
            f"got {exclusivity!r}"
        )
    tasks = _read_tasks()
    axes = {
        "hardware": match_hardware(hardware),
        "model": match_registry(model, known_models(tasks)),
        "engine": match_registry(engine, known_engines(tasks)),
        "metric": match_metric(metric),
    }
    axes["effect"] = effect_verdict(effect, axes["metric"])
    reasons: list[str] = []

    # Gate 1: an unstated decisive axis is a question, not a score. Hardware
    # and model decide whether a result can transfer at all; without them
    # there is nothing to weigh, and inventing a value is how a rubric starts
    # producing confident numbers about things nobody checked.
    missing = [a for a in ("hardware", "model") if axes[a] == UNSTATED]
    if missing:
        return {
            "color": "yellow",
            "axes": axes,
            "reasons": [
                f"{a} is unstated -- unscoreable until someone asks, and a "
                "missing axis is not a low score"
                for a in missing
            ],
            "ask": [
                {
                    "hardware": "which machine was this measured on?",
                    "model": "which model and which quantization?",
                }[a]
                for a in missing
            ],
        }

    # Gate 2: hardware mismatch never takes a machine slot. #171 measured the
    # cost of assuming otherwise.
    if axes["hardware"] in ("foreign", "our-other-tier"):
        reasons.append(
            f"hardware is {axes['hardware']}: cannot take a slot on this Mac "
            "(#171 -- a 3-of-4 axis match on CUDA had no Metal analog)"
        )
        reasons.append(
            "red here means 'not this queue', not 'worthless': a cross-backend "
            "result scopes our question, and @iammac2's ROCm null on bbf5a796 "
            "is what set up the -20.6% we measured on Metal (#162)"
        )
        return {"color": "red", "axes": axes, "reasons": reasons, "ask": []}

    # Gate 3: below our resolution is unanswerable with the kit we have.
    if axes["effect"] == "below-resolution":
        reasons.append(
            f"claimed effect is below the {RESOLUTION[axes['metric']]}% this "
            f"project resolves for {axes['metric']} -- #228 declined an agent "
            "A/B on exactly this arithmetic"
        )

    matched = sum(1 for a in ("hardware", "model", "engine") if axes[a] == "exact")
    if axes["hardware"] == "exact":
        matched_note = "on this exact machine"
    else:
        matched_note = f"on {axes['hardware']} hardware -- a lead, not a result"
    reasons.append(f"{matched} of 3 identity axes exact, {matched_note}")

    if axes["metric"] in RATE_CAVEAT:
        reasons.append(RATE_CAVEAT[axes["metric"]])
    if exclusivity == "only-us":
        reasons.append(
            "only we can answer it -- a hardware gate or a build nobody else has; "
            "this is what made ds4#952's M5 kernels worth pre-empting the queue"
        )
    elif exclusivity == "anyone":
        reasons.append("anyone with a Mac can reproduce it, so it keeps")

    return {
        "color": _color(axes, exclusivity),
        "axes": axes,
        "reasons": reasons,
        "ask": [],
    }


def _color(axes: dict, exclusivity: str) -> str:
    """The decision table. Written as rules so two readers get one answer.

    Green is deliberately hard to reach: this machine, this model, an engine we
    run, and an effect our instruments can actually resolve. Everything that
    merely looks promising is yellow, which is the honest home for most of what
    a sweep turns up.
    """
    exact_identity = (
        axes["hardware"] == "exact"
        and axes["model"] == "exact"
        and axes["engine"] in ("exact", "related")
    )
    answerable = axes["effect"] in ("above-resolution", UNSTATED, "no-instrument")
    # Exclusivity decides urgency, not relevance -- so it is the difference
    # between green and yellow rather than a separate axis. A perfect match
    # anyone with a Mac could reproduce is still worth doing; it is just not
    # worth pre-empting the queue for, because it will still be true next week.
    if exact_identity and answerable and exclusivity != "anyone":
        return "green"
    if axes["hardware"] == "exact" or axes["model"] == "exact":
        return "yellow"
    return "red"


def render(result: dict, evidence: str) -> list[str]:
    lines = [f"{result['color'].upper()}"]
    for axis, value in result["axes"].items():
        lines.append(f"  {axis:9} {value}")
    for reason in result["reasons"]:
        lines.append(f"  - {reason}")
    for question in result["ask"]:
        lines.append(f"  ASK: {question}")
    if evidence:
        lines.append(f"  evidence: {evidence}")
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--hardware", default=UNSTATED, help="machine the claim names")
    p.add_argument("--model", default=UNSTATED, help="model the claim names")
    p.add_argument("--engine", default=UNSTATED, help="engine the claim names")
    p.add_argument(
        "--metric",
        default=UNSTATED,
        help=f"what was measured: {', '.join(sorted(RESOLUTION))}, other",
    )
    p.add_argument(
        "--effect",
        type=float,
        default=None,
        help="claimed effect size in percent; omit when the claim states none",
    )
    p.add_argument(
        "--exclusivity",
        default="anyone",
        choices=sorted(EXCLUSIVITY),
        help="can anyone else answer this? only-us promotes a yellow to green",
    )
    p.add_argument("--evidence", default="", help="post URL, commit, or issue")
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    result = score(
        args.hardware,
        args.model,
        args.engine,
        args.metric,
        args.effect,
        args.exclusivity,
    )
    for line in render(result, args.evidence):
        logger.info(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
