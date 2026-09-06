"""Gate a run on ds4's Metal tensor-route equivalence test (#149, item 2).

`ds4_test --metal-tensor-equivalence` runs five fixtures through the reference
kernels and through the Metal 4 tensor route and reports how far apart the two
came out. It has existed all along. **Nothing ran it**, and preflight logged
`Metal tensor API is on` for weeks as a line of information rather than a
warning -- which is how all four ds4 arms came to run a route whose numerical
behavior nobody had checked against this model.

What the test says on this machine, measured 2026-09-06 on ds4-metal `ba01f5d`
with DeepSeek-V4-Flash 0731, three consecutive runs byte-identical:

    cases=5 capture_fail=0 logits_fail=0 greedy_fail=0 top1_mismatch=0
    min_top5_overlap=3/5 worst_rank_delta=7 worst_rms=1.01843
    worst_max_abs=5.3259

Read that carefully, because it is two findings and not one:

- **No greedy token flips**, on any of the five fixtures, including the long
  code audit -- the case shaped like our agent suite. On this head, the fast
  route agrees with the reference kernels on every token it actually emits.
- **The logits are not the same.** `worst_max_abs 5.33` and a rank delta of 7
  on that same audit case is real drift; the top-5 overlap is 3 of 5. Greedy
  agreement is not bit-exactness, and any claim that quotes a ds4 Metal number
  as exact is still wrong.

So the gate's verdict is **greedy agreement**, not zero drift. A run refuses
when a token would change and proceeds, with the numbers recorded, when only
the logits move.

**The cost is why this caches.** The test maps 93 GiB and takes minutes. Its
answer is a property of a build and a model, not of the moment, so the verdict
is stored against a fingerprint of both and re-run only when one changes. A
cached pass for a different binary reads as `stale`, never as `pass` -- a
rebuilt engine sliding through on yesterday's answer is exactly the failure
this gate exists to stop.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import subprocess
import time

SUMMARY_MARK = "Tensor summary"
DEFAULT_CACHE = pathlib.Path(
    os.environ.get(
        "DS4_EQUIVALENCE_CACHE", pathlib.Path.home() / ".ds4" / "metal-equivalence.json"
    )
)

# Fields worth keeping. `worst_*` describe the drift; the `*_fail` counts and
# `top1_mismatch` decide the verdict.
_INT = ("cases", "capture_fail", "logits_fail", "greedy_fail", "top1_mismatch",
        "worst_rank_delta")
_FLOAT = ("worst_rms", "worst_max_abs", "worst_top20_max_abs")


def parse_summary(text: str) -> dict:
    """The `Tensor summary` line as numbers. {} when the run printed none."""
    line = next((ln for ln in text.splitlines() if SUMMARY_MARK in ln), None)
    if line is None:
        return {}
    got: dict[str, object] = {}
    for key in _INT:
        if m := re.search(rf"\b{key}=(-?\d+)", line):
            got[key] = int(m.group(1))
    for key in _FLOAT:
        if m := re.search(rf"\b{key}=(-?[\d.]+(?:e-?\d+)?)", line):
            got[key] = float(m.group(1))
    if m := re.search(r"min_top5_overlap=(\d+/\d+)", line):
        got["min_top5_overlap"] = m.group(1)
    return got


# The instrument's own limit. `ds4_test` takes DS4_TEST_MODEL and nothing else,
# so a model that needs a sidecar cannot be loaded by it at all -- our fast
# pick, Qwen3.8-Flash-Next, needs a PLE file and says so and stops.
#
# That has to be a distinct verdict. Calling it "fail" would refuse every run
# of the model we run most; calling it "unknown" would demand a re-run that
# cannot succeed. Either way the gate becomes a flag people always pass, which
# is how an assertion stops being one.
UNSUPPORTED_MARKERS = ("requires --ple", "requires a PLE")


def verdict(returncode: int, text: str) -> str:
    """"pass", "fail", "unsupported" or "unknown".

    Never inferred from the exit code alone: a build could tolerate a flipped
    greedy token and still exit 0. The counts in the summary are the claim; the
    exit code is a necessary condition, not a sufficient one.
    """
    summary = parse_summary(text)
    if any(marker in text for marker in UNSUPPORTED_MARKERS):
        return "unsupported"
    if returncode != 0:
        return "fail"
    if not summary:
        return "unknown"
    if summary.get("greedy_fail", 1) or summary.get("top1_mismatch", 1):
        return "fail"
    if summary.get("capture_fail", 1) or summary.get("logits_fail", 1):
        return "fail"
    return "pass"


def fingerprint(binary: pathlib.Path, model: pathlib.Path) -> str | None:
    """Identify the build and weights this verdict describes.

    Size and mtime, not a content hash: the model is 97 GB and hashing it per
    run is not free. The same stand-in `run.py` already uses for a GGUF.
    """
    parts = []
    for path in (binary, model):
        try:
            stat = pathlib.Path(path).resolve().stat()
        except OSError:
            return None
        parts.append(f"{pathlib.Path(path).resolve()}:{stat.st_size}:{int(stat.st_mtime)}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def write_verdict(
    path: pathlib.Path,
    *,
    fingerprint: str,
    verdict: str,
    summary: dict,
    label: str = "",
) -> None:
    """Record one verdict, keeping the others.

    **One entry per (binary, model), not one entry per machine.** The route's
    behavior is model-specific and measurably so: on ds4-metal ba01f5d the same
    binary and the same route pass on DeepSeek-V4-Flash 0731 and fail on
    GLM-5.3-Flash-Q2, flipping the greedy token on both long fixtures. A single
    slot would make switching models discard the other model's answer and pay
    minutes to re-measure it, every time.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cache = read_cache(path) or {}
    entries = dict(cache.get("entries") or {})
    entries[fingerprint] = {
        "verdict": verdict,
        "summary": summary,
        "label": label,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"entries": entries}, indent=2) + "\n")
    tmp.replace(path)


def read_cache(path: pathlib.Path) -> dict | None:
    try:
        got = json.loads(pathlib.Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return got if isinstance(got, dict) else None


def cached_entry(path: pathlib.Path, fingerprint: str | None) -> dict | None:
    """The stored entry for this exact (binary, model), or None."""
    got = read_cache(path)
    if not got or fingerprint is None:
        return None
    entry = (got.get("entries") or {}).get(fingerprint)
    return entry if isinstance(entry, dict) else None


def cached_verdict(path: pathlib.Path, fingerprint: str | None) -> str:
    """"pass"/"fail" for this exact build and model, else "stale" or "absent".

    "stale" means the file holds verdicts, but none for what is about to run --
    a rebuilt binary or a different model. It is deliberately not "absent":
    the distinction tells a reader whether nothing has ever been checked or
    whether this particular combination has not.
    """
    got = read_cache(path)
    if not got or not got.get("entries"):
        return "absent"
    entry = cached_entry(path, fingerprint)
    if entry is None:
        return "stale"
    return str(entry.get("verdict", "absent"))


def run_test(
    tree: pathlib.Path, model: pathlib.Path, timeout: int = 1800
) -> tuple[str, dict, str]:
    """Run the equivalence test now. Returns (verdict, summary, output)."""
    binary = pathlib.Path(tree) / "ds4_test"
    env = dict(os.environ, DS4_TEST_MODEL=str(model))
    try:
        got = subprocess.run(
            [str(binary), "--metal-tensor-equivalence"],
            cwd=str(tree),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "unknown", {}, str(exc)
    text = got.stdout + got.stderr
    return verdict(got.returncode, text), parse_summary(text), text
