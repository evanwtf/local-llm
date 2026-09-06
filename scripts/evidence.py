"""Re-run a finding's claims and say whether they still hold. #160

A finding is a JSON artifact, not prose: every claim carries the argv that
produced it, what was expected **before** it ran, what was observed, and what
would have falsified it. Verifying #159's three claims by hand took five
commands read out of prose and retyped; the point of this script is that the
same check becomes one command the reviewer can point at any agent's finding.

The dogfood artifact `evidence/0078-stale-not-broken.json` was written before
this file and shaped it: the schema below is what it took to express a claim
already made in prose.

What this script will never do, and why -- this paragraph is load-bearing,
because the verifier re-runs commands written by another agent, which makes it
a small execution engine pointed at this machine. Every rule below is
structural, not promissory:

- **It never runs a claim not marked `readonly: true`.** The flag is an
  assertion by the finding's author; the boundary below does not trust it but
  does require it, because a claim that admits it mutates has no place here.
- **It never hands anything to a shell.** `argv` is a JSON list of strings
  passed to subprocess as a list, so no pipe, redirect, substitution, or
  quoting trick can be *expressed*. That is why the schema takes `argv` and
  not `command`: the interactive pipeline that produced the dogfood's numbers
  (`jq | sort | tail`) had to become a single `jq -sr` program, and the
  schema is safer for it.
- **It never runs a program outside the reader allowlist**, and for `git`
  only read-only subcommands -- default-deny. The issue's denylist is kept
  underneath as a backstop, but a denylist is a list of things remembered and
  an allowlist is a boundary; if a future edit widens the readers, the
  denylist still blocks `rm`, every `git` verb that writes, `gh` (its API
  verbs blur read and write), and `run.py` (a verifier that can launch
  benchmark trials is a benchmark runner).
- **It never writes.** No program that can write a file is in the allowlist
  (`tee`, `cp`, `mv`, `sed`, `dd`, `rsync`, `python` are all absent) and no
  shell exists to redirect with, so `results*.jsonl` -- append-only evidence
  several findings read -- cannot be touched by verification.
- **It never runs while the machine claim is held**, or marked `quiet`
  (a timed run: verification would be CPU the arm did not budget for -- the
  #146 confound wearing a different hat), or while the lock is corrupt or
  foreign. It says who holds it. A stale lock only warns: the holder is gone,
  preflight already refuses to steal it, and a read perturbs nothing.
- **It never presents an after-the-fact expectation as pre-registered.** A
  claim marked `expect_authored: "post-hoc"` prints POST-HOC on its line --
  its `expect` is the author's honesty, not a prediction. A claim marked
  `pre-registered` must carry `expect_ref` naming the committed blob its
  expectation came from; git's own history then shows whether that commit
  predates the observation, and the verifier prints that date.
- **It never re-runs a `cost: "expensive"` claim unless `--include-expensive`**
  -- a 73 GiB model load is the reviewer's choice to re-run, not the
  verifier's.
- **It never writes a finding it cannot attribute.** Every finding carries
  `agent`, `agent_model` and `agent_effort` -- the session name, model and
  reasoning-effort setting, taken from `LOCAL_LLM_AGENT`, `LOCAL_LLM_MODEL`
  and `LOCAL_LLM_EFFORT` (#160 amendment 1 & 2). The identity is never
  self-reported: an agent asked what model it is will answer confidently and
  may be wrong, so it comes from the environment only. When a variable is
  unset the tooling logs `unidentified` loudly, and `new` refuses to write a
  finding at all -- an unattributed finding is worth less than no finding,
  because it looks like the attributed kind.

Usage:

    uv run python scripts/evidence.py new --issue 158 --task A --out evidence/0158.json
    uv run python scripts/evidence.py lint evidence/0078-stale-not-broken.json
    uv run python scripts/evidence.py verify evidence/0078-stale-not-broken.json
    uv run python scripts/evidence.py verify FINDING.json --include-expensive
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import pathlib
import platform
import subprocess
import sys

# preflight owns the machine lock (#160: one lock, one owner -- machine_claim
# extends it, evidence reads it, nothing adds a second one).
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)
import preflight

# The agent identity comes from the environment, never from introspection
# (#160 amendment 1 & 2). Every logger in the peer tooling carries it via a
# filter, so no call site passes it.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lib import agent_identity

logger = logging.getLogger(__name__)
agent_identity.install(logger)

SCHEMA = "local-llm/evidence-2"

#: The only programs a finding may ask the verifier to run. Default-deny: a
#: tool not listed is refused, not discussed. Every one of these is a reader.
READERS = {
    "cat",
    "file",
    "git",
    "grep",
    "head",
    "jq",
    "ls",
    "ps",
    "shasum",
    "stat",
    "sw_vers",
    "sysctl",
    "tail",
    "wc",
}

#: `git` is one program with a thousand verbs, so it gets a second gate.
#: Anything not listed is refused -- `git push` needs no denylist entry to be
#: blocked, being absent here is what blocks it.
GIT_READ_VERBS = {
    "log",
    "show",
    "diff",
    "status",
    "rev-parse",
    "cat-file",
    "ls-files",
    "grep",
    "blame",
    "describe",
    "for-each-ref",
    "merge-base",
    "shortlog",
}

#: The backstop beneath the allowlist (#160 names these explicitly). Patterns
#: are (argv0-basename, first-argument-or-None) pairs matched exactly, never
#: substrings of later arguments: `git log -- run.py` is a *read of run.py's
#: history* and must not be confused with *running run.py*.
DENY_BASENAMES = {"rm", "run.py"}
DENY_ARGV0_PREFIXES = {"gh"}  # every gh invocation, read verbs included

EXPECT_KEYS = {
    "exit",
    "exit_nonzero",
    "stdout_equals",
    "stdout_contains",
    "stderr_contains",
}
COSTS = {"cheap", "expensive"}
EXPECT_AUTHORED = {"pre-registered", "post-hoc"}

CHEAP_TIMEOUT = 60
EXPENSIVE_TIMEOUT = 900


class Refused(Exception):
    """The finding asked for something the verifier will never do."""


def load_finding(path: pathlib.Path) -> dict:
    """Parse and structurally validate a finding. Raises Refused on bad shape."""
    try:
        finding = json.loads(path.read_text())
    except OSError as exc:
        raise Refused(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise Refused(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(finding, dict):
        raise Refused(f"{path} must be a JSON object at top level")
    if finding.get("schema") != SCHEMA:
        raise Refused(
            f"{path} declares schema {finding.get('schema')!r}; this verifier speaks {SCHEMA!r}"
        )
    for key in ("issue", "agent", "agent_model", "agent_effort", "statement", "claims"):
        if key not in finding:
            raise Refused(f"{path} is missing required key {key!r}")
    for key in ("agent", "agent_model", "agent_effort"):
        if not isinstance(finding[key], str) or not finding[key]:
            raise Refused(f"{path}: {key!r} must be a non-empty string")
    if finding["agent"] == agent_identity.UNIDENTIFIED:
        raise Refused(
            f"{path} is attributed to {agent_identity.UNIDENTIFIED!r} -- an "
            "unattributed finding is worth less than no finding, because it "
            "looks like the attributed kind"
        )
    claims = finding["claims"]
    if not isinstance(claims, list) or not claims:
        raise Refused(f"{path} carries no claims")
    ids: set[str] = set()
    for claim in claims:
        _lint_claim(claim, ids, path)
    _lint_refs(claims, path)
    return finding


def _lint_claim(claim: object, ids: set[str], path: pathlib.Path) -> None:
    if not isinstance(claim, dict):
        raise Refused(f"{path}: every claim must be an object")
    for key in ("id", "statement", "readonly", "cost", "falsifier"):
        if key not in claim:
            raise Refused(f"{path}: a claim is missing {key!r}")
    if claim["id"] in ids:
        raise Refused(f"{path}: duplicate claim id {claim['id']!r}")
    ids.add(claim["id"])
    if claim["cost"] not in COSTS:
        raise Refused(
            f"{path}: claim {claim['id']!r} cost must be one of {sorted(COSTS)}"
        )
    if claim["readonly"] is not True:
        raise Refused(
            f"{path}: claim {claim['id']!r} is not marked readonly: true -- "
            "this verifier only re-runs read-only claims"
        )
    authored = claim.get("expect_authored")
    if authored not in EXPECT_AUTHORED:
        raise Refused(
            f"{path}: claim {claim['id']!r} needs expect_authored in "
            f"{sorted(EXPECT_AUTHORED)}"
        )
    if authored == "pre-registered":
        ref = claim.get("expect_ref")
        if not isinstance(ref, dict) or "path" not in ref or "blob" not in ref:
            raise Refused(
                f"{path}: claim {claim['id']!r} says pre-registered but carries "
                "no expect_ref -- an expectation with no committed blob behind "
                "it is post-hoc wearing a tag"
            )
    if "compose" in claim:
        if "argv" in claim or "cwd" in claim:
            raise Refused(
                f"{path}: compose claim {claim['id']!r} must not carry argv/cwd "
                "-- it compares other claims' outputs, it does not run itself"
            )
        pairs = claim["compose"].get("lt")
        if not pairs or not isinstance(pairs, list):
            raise Refused(
                f"{path}: compose claim {claim['id']!r} needs compose.lt pairs"
            )
        for pair in pairs:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not all(isinstance(ref, str) for ref in pair)
            ):
                raise Refused(
                    f"{path}: compose claim {claim['id']!r} has a malformed "
                    "lt pair -- expected [left-claim-id, right-claim-id]"
                )
        return
    for key in ("argv", "cwd", "expect", "observed"):
        if key not in claim:
            raise Refused(
                f"{path}: claim {claim['id']!r} is a command claim and is "
                f"missing {key!r}"
            )
    argv = claim["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(part, str) and part for part in argv)
    ):
        raise Refused(
            f"{path}: claim {claim['id']!r} argv must be a non-empty list of "
            "non-empty strings -- a JSON list, never a shell string"
        )
    if not pathlib.Path(claim["cwd"]).is_dir():
        raise Refused(
            f"{path}: claim {claim['id']!r} cwd {claim['cwd']!r} is not a directory"
        )
    unknown = set(claim["expect"]) - EXPECT_KEYS
    if unknown:
        raise Refused(
            f"{path}: claim {claim['id']!r} expect has unknown keys {sorted(unknown)}"
        )


def _lint_refs(claims: list[dict], path: pathlib.Path) -> None:
    by_id = {claim["id"]: claim for claim in claims}
    for claim in claims:
        for pair in (claim.get("compose", {}) or {}).get("lt", []):
            for ref in pair:
                if ref not in by_id:
                    raise Refused(
                        f"{path}: claim {claim['id']!r} references {ref!r}, "
                        "which does not exist"
                    )
                if "argv" not in by_id[ref]:
                    raise Refused(
                        f"{path}: claim {claim['id']!r} composes {ref!r}, which "
                        "is itself a compose claim -- compose must bottom out "
                        "in commands"
                    )


def gate_argv(claim: dict) -> None:
    """Refuse what the verifier will never run. Raises Refused with the why."""
    argv = claim["argv"]
    name = os.path.basename(argv[0])
    if name in DENY_BASENAMES:
        raise Refused(
            f"claim {claim['id']!r}: {name!r} is denylisted -- the verifier "
            "re-runs other agents' commands and this is exactly the case the "
            "denylist exists for"
        )
    if name in DENY_ARGV0_PREFIXES or name.split("/")[-1] in DENY_ARGV0_PREFIXES:
        raise Refused(
            f"claim {claim['id']!r}: {name!r} is denylisted entirely -- its "
            "verbs blur read and write, and it is network"
        )
    if name not in READERS:
        raise Refused(
            f"claim {claim['id']!r}: {name!r} is not in the reader allowlist "
            f"{sorted(READERS)} -- the allowlist is the boundary, default-deny"
        )
    if name == "git":
        verb = argv[1] if len(argv) > 1 else ""
        if verb not in GIT_READ_VERBS:
            raise Refused(
                f"claim {claim['id']!r}: git {verb!r} is not a read-only verb "
                f"(allowed: {sorted(GIT_READ_VERBS)})"
            )


def machine_gate(repo: pathlib.Path) -> tuple[bool, str]:
    """Whether the machine claim forbids verifying right now, and why.

    Reads preflight's lock -- the same file run.py takes and machine_claim.py
    extends. A lock is an explicit declaration by a session that said what it
    was doing, so like preflight's acquire there is no generosity here.
    """
    lock = preflight.read_lock()
    state, why = preflight.lock_state(lock, platform.node(), os.getpid())
    if isinstance(lock, dict) and lock.get("quiet"):
        return True, f"the run lock is marked quiet (a timed run): {why}"
    if state in ("held", "corrupt", "foreign"):
        return True, why
    if state == "stale":
        logger.warning("stale run lock, proceeding (verification only reads): %s", why)
    return False, why


def run_claim(claim: dict) -> dict:
    """Re-run one command claim. Returns {exit, stdout, stderr}. Refuses unsafe argv."""
    gate_argv(claim)
    timeout = CHEAP_TIMEOUT if claim["cost"] == "cheap" else EXPENSIVE_TIMEOUT
    try:
        got = subprocess.run(
            claim["argv"],
            cwd=claim["cwd"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise Refused(
            f"claim {claim['id']!r} exceeded its {timeout}s budget -- a "
            "read that takes that long is not cheap, say what it really costs"
        )
    except OSError as exc:
        raise Refused(f"claim {claim['id']!r} could not start: {exc}") from exc
    return {
        "exit": got.returncode,
        "stdout": got.stdout or "",
        "stderr": got.stderr or "",
    }


def check_expect(fresh: dict, expect: dict) -> list[str]:
    """Fresh output vs the claim's pre-stated expectation. [] means it holds."""
    failures = []
    if "exit" in expect and fresh["exit"] != expect["exit"]:
        failures.append(f"exit {fresh['exit']}, expected {expect['exit']}")
    if expect.get("exit_nonzero") is True and fresh["exit"] == 0:
        failures.append("expected a nonzero exit, got 0")
    if (
        "stdout_equals" in expect
        and fresh["stdout"].strip() != expect["stdout_equals"].strip()
    ):
        failures.append(
            f"stdout {fresh['stdout'].strip()[:120]!r}, expected {expect['stdout_equals'][:120]!r}"
        )
    if "stdout_contains" in expect and expect["stdout_contains"] not in fresh["stdout"]:
        failures.append(f"stdout lacks {expect['stdout_contains'][:120]!r}")
    if "stderr_contains" in expect and expect["stderr_contains"] not in fresh["stderr"]:
        failures.append(f"stderr lacks {expect['stderr_contains'][:120]!r}")
    return failures


def check_drift(fresh: dict, observed: dict) -> str | None:
    """Fresh output vs the finding's recorded observation. None means it reproduced."""
    if fresh["exit"] != observed.get("exit"):
        return f"recorded exit {observed.get('exit')}, now {fresh['exit']}"
    if fresh["stdout"].strip() != (observed.get("stdout") or "").strip():
        return (
            f"recorded stdout {(observed.get('stdout') or '')[:60]!r}, "
            f"now {fresh['stdout'].strip()[:60]!r}"
        )
    return None


def _as_value(text: str) -> datetime.datetime | str:
    """A compose operand: a parsed timestamp when it parses, else the string.

    `2026-09-01T08:15:17` and `2026-09-01T17:42:29-04:00` are both ISO
    timestamps but not mutually comparable as strings -- the offset changes
    the lexicographic order. Parse when both sides parse; fall back to
    lexicographic only when they are plain strings.
    """
    try:
        return datetime.datetime.fromisoformat(text)
    except ValueError:
        return text


def run_compose(claim: dict, by_id: dict[str, dict]) -> tuple[list[str], str]:
    """Evaluate one compose claim against freshly re-run operands."""
    values: dict[str, str] = {}
    detail = []
    for pair in claim["compose"]["lt"]:
        for ref in pair:
            if ref not in values:
                values[ref] = run_claim(by_id[ref])["stdout"].strip()
    failures = []
    for left, right in claim["compose"]["lt"]:
        lhs, rhs = values[left], values[right]
        lhs_v, rhs_v = _as_value(lhs), _as_value(rhs)
        if type(lhs_v) is not type(rhs_v):
            failures.append(
                f"cannot compare {lhs!r} and {rhs!r}: one is a timestamp, the other is not"
            )
            continue
        if not lhs_v < rhs_v:
            failures.append(f"{lhs!r} is not before {rhs!r}")
        detail.append(f"{lhs} < {rhs}")
    return failures, "; ".join(detail)


def _show_expect_ref(claim: dict, repo: pathlib.Path) -> str:
    """Surface a pre-registered expectation's git provenance, or refuse the tag.

    Pre-registration that cannot show its registration is conventional, not
    structural: the claim said pre-registered, so the verifier makes it prove
    the committed blob matches, and prints when that blob entered history --
    one command's worth of honesty either way.
    """
    ref = claim["expect_ref"]
    try:
        got = subprocess.run(
            ["git", "cat-file", "blob", ref["blob"]],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
            timeout=CHEAP_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        return f"expect_ref unreadable: {exc}"
    if got.returncode != 0:
        return f"expect_ref blob {ref['blob']!r} is not in {repo}: {got.stderr.strip()}"
    try:
        committed = json.loads(got.stdout)
    except ValueError:
        return f"expect_ref blob {ref['blob']!r} is not JSON"
    if committed != claim["expect"]:
        return "expect_ref blob does not match the claim's expect -- the expectation moved after registration"
    added = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=%cI", "-1", "--", ref["path"]],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=CHEAP_TIMEOUT,
        stdin=subprocess.DEVNULL,
    )
    when = added.stdout.strip() or "never committed"
    return f"expect pre-registered in {ref['path']} ({when})"


def verify(finding: dict, path: pathlib.Path, include_expensive: bool) -> int:
    """Re-run every admissible claim. Returns a process exit code."""
    repo = finding.get("repo") or str(pathlib.Path.cwd())
    busy, why = machine_gate(pathlib.Path(repo))
    if busy:
        logger.error("REFUSING to verify: %s", why)
        return 2
    logger.info("finding: %s", finding["statement"])
    logger.info(
        "issue #%s, by %s/%s/effort=%s, verified %s",
        finding["issue"],
        finding["agent"],
        finding["agent_model"],
        finding["agent_effort"],
        path,
    )

    by_id = {claim["id"]: claim for claim in finding["claims"]}
    status = 0
    for claim in finding["claims"]:
        marker = "POST-HOC" if claim.get("expect_authored") == "post-hoc" else ""
        if claim["cost"] == "expensive" and not include_expensive:
            logger.info(
                "SKIP    %s (expensive; --include-expensive to re-run) %s",
                claim["id"],
                marker,
            )
            continue
        try:
            if "compose" in claim:
                failures, detail = run_compose(claim, by_id)
                if failures:
                    status = 1
                    logger.error(
                        "FAIL    %s: %s | %s",
                        claim["id"],
                        "; ".join(failures),
                        claim["falsifier"],
                    )
                else:
                    logger.info(
                        "PASS    %s (compose lt: %s) %s", claim["id"], detail, marker
                    )
                continue
            fresh = run_claim(claim)
            failures = check_expect(fresh, claim["expect"])
            drift = check_drift(fresh, claim["observed"])
            note = marker
            if claim.get("expect_authored") == "pre-registered":
                note = _show_expect_ref(claim, pathlib.Path(repo))
            if failures:
                status = 1
                logger.error(
                    "FAIL    %s: %s | %s",
                    claim["id"],
                    "; ".join(failures),
                    claim["falsifier"],
                )
            elif drift:
                status = 1
                logger.error(
                    "DRIFT   %s: %s -- it does not reproduce, the finding goes back %s",
                    claim["id"],
                    drift,
                    marker,
                )
            else:
                logger.info("PASS    %s %s", claim["id"], note)
        except Refused as exc:
            status = 1
            logger.error("REFUSED %s: %s", claim["id"], exc)
    verdict = "clean" if status == 0 else "FAILED"
    logger.info("%s: %d claim(s) checked", verdict, len(finding["claims"]))
    return status


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def new_finding(issue: int, task: str, out: pathlib.Path) -> int:
    """Scaffold a finding artifact. Refuses when the identity is unidentified.

    The identity is the whole point of the schema (#160 amendment 1 & 2): a
    finding that cannot say which agent, model and effort produced it is worth
    less than no finding, because it looks like the attributed kind. So this
    refuses to write rather than write a blank.
    """
    if not agent_identity.is_identified():
        logger.error(
            "REFUSING to write a finding: agent identity is %s -- set %s, %s, %s",
            agent_identity.log_label(),
            agent_identity.AGENT_VAR,
            agent_identity.MODEL_VAR,
            agent_identity.EFFORT_VAR,
        )
        return 2
    agent, model, effort = agent_identity.identity()
    finding = {
        "schema": SCHEMA,
        "task": task,
        "issue": issue,
        "agent": agent,
        "agent_model": model,
        "agent_effort": effort,
        "repo": str(pathlib.Path.cwd()),
        "authored_at": _now_iso(),
        "statement": "",
        "provenance": {"machine": platform.node(), "trees": {}, "binaries": {}},
        "claims": [],
    }
    out.write_text(json.dumps(finding, indent=2) + "\n")
    logger.info("wrote finding skeleton to %s", out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    verify_parser = sub.add_parser("verify", help="re-run a finding's claims")
    verify_parser.add_argument("finding", type=pathlib.Path)
    verify_parser.add_argument(
        "--include-expensive",
        action="store_true",
        help="also re-run cost:expensive claims (model loads and the like)",
    )
    lint_parser = sub.add_parser("lint", help="validate schema only; runs nothing")
    lint_parser.add_argument("finding", type=pathlib.Path)
    new_parser = sub.add_parser("new", help="scaffold a finding artifact")
    new_parser.add_argument("--issue", type=int, required=True)
    new_parser.add_argument("--task", required=True)
    new_parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    # Every line carries the agent identity (#160 amendment 1 & 2): the report
    # is provenance, and provenance that cannot say who produced it is the
    # defect this issue exists to close.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(agent)s %(name)s %(levelname)s %(message)s",
    )
    if args.cmd == "new":
        return new_finding(args.issue, args.task, args.out)
    try:
        finding = load_finding(args.finding)
    except Refused as exc:
        logger.error("%s", exc)
        return 2
    if args.cmd == "lint":
        logger.info("lint clean: %d claim(s)", len(finding["claims"]))
        return 0
    return verify(finding, args.finding, args.include_expensive)


if __name__ == "__main__":
    raise SystemExit(main())
