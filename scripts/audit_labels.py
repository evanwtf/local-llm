"""Check every open issue's labels against the rules the queue depends on.

`make_next.py` builds each machine's queue from two labels: one priority and
one platform. An issue without both is invisible to every queue, and nothing
says so. This script says so, and also checks the labels that let a machine
find its own work (`hardware:*`) and the family tags (`model:*`, `engine:*`).

The rules, each one a finding when it fails:

- **priority** -- exactly one of P0-P3. None hides the issue; two is a
  defect, not a tie (`make_next.priority`).
- **platform** -- a `platform:*` label, or a type label (bug, enhancement,
  documentation, question) for harness work that runs anywhere. The
  issue-sweep skill gives such work no platform; with neither label, nothing
  says what the issue is or where it runs.
- **hardware** -- a `hardware:*` label must agree with the platform:
  the M5 Max is `platform:macOS`; the GB10 and Ryzen are `platform:Nvidia`.
  `platform:macOS` alone names one machine, so its hardware label is due;
  an issue on both platforms is cross-machine and needs none.
- **retired** -- `hardware:Cortex-X925-GB10` names the single Spark. From
  2026-09-30 every DGX test runs on both Sparks as a cluster, a single-Spark
  idea included (operator), so no open issue carries it, not even beside
  `-x2`; closed issues keep it as history.
- **family** -- a model or engine named in the title needs its label.
  Only the title is read: bodies quote every engine in passing, and a
  label from a quotation is noise.

    uv run python scripts/audit_labels.py            # all open issues
    uv run python scripts/audit_labels.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

PRIORITIES = ("P0", "P1", "P2", "P3")
MAC = "platform:macOS"
NVIDIA = "platform:Nvidia"
M5 = "hardware:M5-Max-128GB"
SPARK_OLD = "hardware:Cortex-X925-GB10"
SPARK_X2 = "hardware:Cortex-X925-GB10-x2"
RYZEN = "hardware:Ryzen9-7900X-RTX3080Ti"
TYPES = {"bug", "enhancement", "documentation", "question"}
HARDWARE_PLATFORM = {M5: MAC, SPARK_OLD: NVIDIA, SPARK_X2: NVIDIA, RYZEN: NVIDIA}

# Title patterns for each family label. Word boundaries matter: "llama.cpp"
# is an engine, not the Llama model family, and "ds4" is the engine while
# "DeepSeek" is the model.
FAMILY = {
    "model:glm": r"\bglm",
    "model:qwen": r"\bqwen|flash-next|qwen4exp",
    "model:deepseek": r"deepseek|\bdsv4|\bv4\.1 flash|\bv4 flash",
    "model:gptoss": r"gpt-oss",
    "model:nemotron": r"nemotron",
    "model:gemma": r"gemma",
    "model:llama": r"\bllama[\s-]?\d",
    "model:minimax": r"minimax",
    "engine:llamacpp": r"llama\.cpp|llama-server",
    "engine:vllm": r"\bvllm",
    "engine:dwarfstar": r"\bds4\b|dwarfstar",
    "engine:mlx": r"\bmlx|omlx|mtplx",
    "engine:ollama": r"ollama",
    "engine:sglang": r"sglang",
    "engine:exl3": r"\bexl3|exllama",
    "engine:trtllm": r"tensorrt|trt-llm",
}


def names(issue: dict) -> set[str]:
    return {lab["name"] for lab in issue["labels"]}


def findings(issue: dict) -> list[str]:
    """Every rule this issue breaks, one line each, most serious first."""
    labels = names(issue)
    out: list[str] = []

    prio = sorted(labels & set(PRIORITIES))
    if not prio:
        out.append("priority: none (invisible to make_next)")
    elif len(prio) > 1:
        out.append(f"priority: {len(prio)} labels ({', '.join(prio)}); keep one")

    platforms = {lab for lab in labels if lab.startswith("platform:")}
    if not platforms and not labels & TYPES:
        out.append("platform: none, and no type label")

    for hw, plat in HARDWARE_PLATFORM.items():
        if hw in labels and plat not in labels:
            out.append(f"hardware: {hw} without {plat}")
    if platforms == {MAC} and M5 not in labels:
        out.append(f"hardware: {MAC} without {M5}")
    if SPARK_OLD in labels:
        out.append(f"retired: {SPARK_OLD} on open work; use {SPARK_X2}")

    # A title that quotes a label name ("model:qwen, engine:vllm") is about
    # the labels, not the family (#465).
    title = re.sub(r"\b(?:model|engine):\S+", "", issue["title"].lower())
    for label, pattern in FAMILY.items():
        if label not in labels and re.search(pattern, title):
            out.append(f"family: title names it, missing {label}")
    return out


def audit(issues: list[dict]) -> dict[int, list[str]]:
    """Issue number to its findings, for the issues that have any."""
    report = {i["number"]: findings(i) for i in issues}
    return {n: f for n, f in sorted(report.items()) if f}


def fetch(limit: int = 500) -> list[dict]:
    out = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,labels",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    issues: list[dict] = json.loads(out.stdout)
    return issues


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--json", action="store_true", help="print JSON, not lines")
    args = p.parse_args(argv)

    issues = fetch()
    report = audit(issues)
    if args.json:
        logger.info("%s", json.dumps(report, indent=2))
    else:
        titles = {i["number"]: i["title"] for i in issues}
        for number, found in report.items():
            logger.info("#%d %s", number, titles[number][:70])
            for line in found:
                logger.info("    %s", line)
    logger.info("%d of %d open issues break a rule", len(report), len(issues))
    return 1 if report else 0


if __name__ == "__main__":
    raise SystemExit(main())
