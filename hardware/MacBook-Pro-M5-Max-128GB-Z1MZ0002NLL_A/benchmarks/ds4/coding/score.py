"""Score HumanEval completions by running their unit tests.

This executes model-written code. Each candidate runs in its own subprocess,
in a scratch working directory, under a wall-clock timeout, so a hang or a
crash costs one problem rather than the run.
"""

import argparse
import json
import logging
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[5] / "scripts" / "lib"))

import logs

logger = logging.getLogger(__name__)


def prelude(prompt, entry_point):
    """Return the imports and helpers that precede the target signature.

    Instruct-tuned models often drop the prompt's imports. Re-adding them tests
    the function the model wrote rather than its memory of the header.

    Cut at the entry point specifically, not at the first `def`: several
    problems (HumanEval/38 is one) define a helper above the target, and the
    model is asked for the target alone. Cutting at the first `def` would drop
    the helper and fail correct code.
    """
    lines = prompt.split("\n")
    for i, line in enumerate(lines):
        if line.startswith((f"def {entry_point}", f"class {entry_point}")):
            return "\n".join(lines[:i])
    # No entry-point definition in the prompt; fall back to the first block.
    for i, line in enumerate(lines):
        if line.startswith(("def ", "class ")):
            return "\n".join(lines[:i])
    return ""


def run_one(problem, completion, timeout, workdir):
    program = "\n".join(
        [
            prelude(problem["prompt"], problem["entry_point"]),
            completion,
            "",
            problem["test"],
            f"check({problem['entry_point']})",
        ]
    )
    path = os.path.join(workdir, "candidate.py")
    with open(path, "w") as fh:
        fh.write(program)
    try:
        proc = subprocess.run(
            [sys.executable, "-I", path],
            cwd=workdir,
            capture_output=True,
            timeout=timeout,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    if proc.returncode == 0:
        return True, "ok"
    err = (proc.stderr or "").strip().split("\n")
    return False, err[-1][:200] if err else f"exit {proc.returncode}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--problems", default="HumanEval.jsonl")
    ap.add_argument("--samples", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    logs.configure()

    problem_lines = pathlib.Path(args.problems).read_text().splitlines()
    problems = {
        json.loads(line)["task_id"]: json.loads(line)
        for line in problem_lines
        if line.strip()
    }
    samples = [
        json.loads(line)
        for line in pathlib.Path(args.samples).read_text().splitlines()
        if line.strip()
    ]

    results = []
    passed = 0
    with tempfile.TemporaryDirectory() as workdir:
        for i, sample in enumerate(samples, 1):
            prob = problems[sample["task_id"]]
            ok, detail = run_one(
                prob, sample.get("completion", ""), args.timeout, workdir
            )
            passed += ok
            results.append(
                {"task_id": sample["task_id"], "passed": ok, "detail": detail}
            )
            if not ok:
                logger.info("%-16s FAIL  %s", sample["task_id"], detail)

    total = len(samples)
    logger.info("pass@1 = %d/%d = %.1f%%", passed, total, 100.0 * passed / total)

    if args.out:
        with open(args.out, "w") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in results)
        logger.info("wrote %s", args.out)


if __name__ == "__main__":
    main()
