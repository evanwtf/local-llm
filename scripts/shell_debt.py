#!/usr/bin/env python3
"""How much shell is left, and how much of it can still misidentify a process.

#235's done condition is two numbers, and they are not the same number:

    a 90% line reduction     3,589 -> 359 lines under scripts/
    zero pgrep/pkill         every file that matches a process by NAME

AGENTS.md quotes both, and a quoted number goes stale the day after it is
written. This computes them, so the next reader re-runs it instead of
believing me.

**They come apart, and knowing where matters.** Line count is readability;
`pgrep` is the one that has cost measurements. A pattern matches the shell
that quoted it, so `until ! pgrep -f '<driver>.sh'` waits on itself -- seven
such shells were found orphaned on 2026-09-08, up to 6h30m old, and the commit
guard refused every commit for hours because it matched them. Bracketing the
first character only ever protected against *self*-match; it does nothing
about a second copy, a waiter, or an editor holding the filename.

A `.sh` counts as REPLACED when a Python file does its job -- which is not
always its own name. `restart_between_trials_armB.sh` is replaced by
`restart_between_trials.py`, which grew both arms in #261, and
`disk_kv_mechanism_test.sh` by `disk_kv_mechanism.py`, which had to drop the
`_test` because pytest collects `*_test.py` from the whole repo. A
name-matching check reports both as unreplaced, which is how this script came
to have an explicit table instead.

    uv run python scripts/shell_debt.py
    uv run python scripts/shell_debt.py --json

Replacement is not retirement. AGENTS.md: a `.sh` is deleted only once its
Python replacement has produced a run that agrees with it.
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

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The scope, as a command anyone can re-run.
SCOPE = "scripts/*.sh"

#: The line target: 10% of the 3,589 lines standing when #235 was written.
BASELINE = 3589
TARGET = 359

#: `<shell>: <the Python that does its job>`. Not a name match -- see the
#: docstring. A file absent from here has no replacement yet.
REPLACED = {
    "scripts/greedy_mtp_ab.sh": "scripts/greedy_mtp_ab.py",
    "scripts/mtp_treatment_gate.sh": "scripts/mtp_treatment_gate.py",
    "scripts/route_agent_ab.sh": "scripts/route_agent_ab.py",
    "scripts/targets_ab.sh": "scripts/targets_ab.py",
    "scripts/strip_toggle_ab.sh": "scripts/strip_toggle_ab.py",
    "scripts/stack_agent_ab.sh": "scripts/stack_agent_ab.py",
    "scripts/metal_knob_ab.sh": "scripts/metal_knob_ab.py",
    "scripts/decode_ab.sh": "scripts/decode_ab.py",
    "scripts/decode_ab_engine.sh": "scripts/decode_ab_engine.py",
    # #261 gave one module both arms; armB.sh is `--arm B`.
    "scripts/restart_between_trials.sh": "scripts/restart_between_trials.py",
    "scripts/restart_between_trials_armB.sh": "scripts/restart_between_trials.py",
    # `_test` had to go: pytest collects *_test.py from the whole repo.
    "scripts/disk_kv_mechanism_test.sh": "scripts/disk_kv_mechanism.py",
    "scripts/ab_status.sh": "scripts/ab_status.py",
    "scripts/decode_ab_stack.sh": "scripts/decode_ab_stack.py",
    "scripts/decode_ab_repeat.sh": "scripts/decode_ab_repeat.py",
    "scripts/coherence_check.sh": "scripts/coherence_check.py",
    "scripts/lib/ds4_server.sh": "scripts/lib/ds4_server.py",
    "scripts/lib/mlx_serve.sh": "scripts/lib/mlx_serve.py",
}

#: Shell that should STAY shell, with the reason. Without this the 90% target
#: reads as "port everything", and the first file anyone would reach for is
#: the one that must not move: RECOMMENDATIONS.md tells a stranger to paste
#: `scripts/local-agent.sh`, so porting it changes published instructions and
#: gains nothing -- a Python installer would still be a script you paste.
KEEP = {
    "scripts/local-agent.sh": (
        "user-facing; RECOMMENDATIONS.md tells a stranger to run it"
    ),
    "scripts/install-metal-ceiling.sh": "installs a system artifact",
    "scripts/ds4-fast.sh": "3-line exec shim -- AGENTS.md's documented exception",
    "scripts/ds4-vanilla.sh": "3-line exec shim -- AGENTS.md's documented exception",
}

#: Shell that is retired by deleting something else: `<library>: (every file
#: that sources it)`. The library goes when the LAST of them goes, so the
#: value is a tuple even when it holds one name -- `ds4_server.sh` has eight
#: sourcers and a single-owner mapping could not say so.
#:
#: **A sourced library needs no evidence of its own, and listing it as
#: "NO EVIDENCE" misreports the plan.** A differential compares a driver's
#: recorded argv and env against the shell's; a library that is never invoked
#: on its own has no such run to produce. What clears it is that nothing
#: sources it any more -- which is a fact about other files, checked by the
#: tests below, not a test somebody has to write for this one.
#:
#: That is not the same as saying a library needs no tests. `ds4_server.sh`
#: and `mlx_serve.sh` each carry a teardown suite, and on 2026-09-09 those
#: suites gained the case that had been missing from both: a CHAINED EXIT trap
#: on a FAILING run. The bare branch and the succeeding chained branch were
#: each covered, and their combination -- the only one where the wrong status
#: is visible -- was not. See tests/test_ds4_server_teardown.py.
#:
#: - `transcript_move.sh`: the mtime filter in `lib/batch.py` does its job
#:   now, and every other mention of it in the tree is a comment explaining
#:   why that filter exists.
#: - `ds4_server.sh`: start/stop/teardown for every ds4 driver.
#: - `mlx_serve.sh`: the second engine, added for #191; `stack_agent_ab.sh` is
#:   the only file that has ever sourced it.
DIES_WITH: dict[str, tuple[str, ...]] = {
    "scripts/lib/transcript_move.sh": ("scripts/stack_agent_ab.sh",),
    "scripts/lib/ds4_server.sh": (
        "scripts/disk_kv_mechanism_test.sh",
        "scripts/greedy_mtp_ab.sh",
        "scripts/mtp_treatment_gate.sh",
        "scripts/restart_between_trials.sh",
        "scripts/restart_between_trials_armB.sh",
        "scripts/stack_agent_ab.sh",
        "scripts/strip_toggle_ab.sh",
        "scripts/targets_ab.sh",
    ),
    "scripts/lib/mlx_serve.sh": ("scripts/stack_agent_ab.sh",),
}

#: `<shell>: <the test that clears it for deletion>`. An explicit table for
#: the same reason REPLACED is one: a name match would report a shell as
#: cleared because a test file happens to share its name, and the whole point
#: of this column is that somebody looked.
#:
#: Replacement is not retirement. A .sh is deleted only once its Python
#: replacement has produced a run that AGREES with it -- and for the drivers
#: that means a differential: the real shell and the port both drive a
#: recording fake child, and the recorded argv and env are compared. The
#: entry names the test so a reader can run it, not a commit that describes
#: having run it once.
EVIDENCE = {
    "scripts/ab_status.sh": (
        "tests/test_ab_status.py::test_the_shell_and_the_port_print_the_same_status_line"
    ),
    "scripts/coherence_check.sh": (
        "tests/test_coherence_check.py::test_the_shell_and_the_port_hand_ds4_the_same_command"
    ),
    "scripts/decode_ab.sh": (
        "tests/test_decode_ab.py::test_the_shell_and_the_port_hand_ds4_bench_the_same_command"
    ),
    "scripts/decode_ab_engine.sh": (
        "tests/test_decode_ab_engine.py::test_the_shell_and_the_port_hand_ds4_bench_the_same_command"
    ),
    "scripts/decode_ab_repeat.sh": (
        "tests/test_decode_ab_repeat.py::test_the_shell_and_the_port_hand_the_harness_the_same_command"
    ),
    "scripts/decode_ab_stack.sh": (
        "tests/test_decode_ab_stack.py::test_the_shell_and_the_port_hand_ds4_bench_the_same_command"
    ),
    "scripts/disk_kv_mechanism_test.sh": (
        "tests/test_disk_kv_mechanism.py::"
        "test_the_shell_and_the_port_start_the_same_server"
    ),
    "scripts/greedy_mtp_ab.sh": (
        "tests/test_greedy_mtp_ab.py::"
        "test_the_shell_and_the_port_hand_run_py_the_same_command"
    ),
    "scripts/mtp_treatment_gate.sh": (
        "tests/test_mtp_treatment_gate_python.py::"
        "test_the_shell_and_the_port_hand_run_py_the_same_command"
    ),
    "scripts/restart_between_trials.sh": (
        "tests/test_restart_between_trials_equiv.py::"
        "test_arm_a_differs_from_its_shell_by_exactly_the_server_log"
    ),
    "scripts/restart_between_trials_armB.sh": (
        "tests/test_restart_between_trials_equiv.py::"
        "test_arm_b_matches_its_shell_exactly"
    ),
    "scripts/targets_ab.sh": (
        "tests/test_targets_ab_equiv.py::"
        "test_the_arms_run_in_the_same_ORDER_not_merely_the_same_set"
    ),
    "scripts/stack_agent_ab.sh": (
        "tests/test_stack_agent_ab_equiv.py::"
        "test_the_ds4_arm_gets_the_same_server_command"
    ),
    "scripts/strip_toggle_ab.sh": (
        "tests/test_strip_toggle_ab.py::"
        "test_the_on_arm_removes_shim_no_strip_on_both_sides"
    ),
    "scripts/metal_knob_ab.sh": (
        "tests/test_metal_knob_ab.py::test_the_shell_and_the_port_hand_ds4_bench_the_same_command"
    ),
    "scripts/route_agent_ab.sh": (
        "tests/test_route_agent_ab.py::test_the_shell_is_dead_by_the_flag_run_py_dropped"
    ),
}

#: Shell that is retired by a written deviation rather than an agreeing run.
#: Retirement normally needs "a run that agrees"; a shell that cannot produce
#: one is retired on the evidence that it cannot, named here so a future reader
#: sees it where the file is listed, not only in a commit message.
DEVIATIONS = {
    "scripts/stack_agent_ab.sh": (
        "retired on a PARTIAL differential, and the gap is deliberate. Every "
        "other retirement here drives the real .sh end to end against "
        "recording fakes; this script is 541 lines and its top level takes "
        "the machine lock, arms two chained EXIT traps, syncs worktrees, "
        "checks two engine binaries and starts a shim before it reaches a "
        "sweep. Driving all of that needs more scaffolding than the file has "
        "lines, which is the stopping rule #235 set for exactly this file. "
        "So the differential executes the shell's OWN function text -- "
        "server_argv and sweep -- and compares the two command lines that "
        "decide what gets measured, for both engines. NOT compared: the "
        "top-level sequence (lock, trap arming, worktree sync, shim start, "
        "sweep ordering), which is covered separately by "
        "tests/test_stack_agent_ab_python.py, "
        "tests/test_stack_agent_ab_failure.py and "
        "tests/test_mlx_serve_teardown.py. Evidence: "
        "tests/test_stack_agent_ab_equiv.py::"
        "test_the_ds4_arm_gets_the_same_server_command"
    ),
    "scripts/route_agent_ab.sh": (
        "not retired by an agreeing run; the shell is dead by #264 and cannot "
        "produce one. Evidence: "
        "tests/test_route_agent_ab.py::test_the_shell_is_dead_by_the_flag_run_py_dropped"
    ),
}

BY_NAME = re.compile(r"\b(pgrep|pkill)\b")


def shell_files(root: pathlib.Path = ROOT) -> list[str]:
    """Tracked shell under scripts/. The index, not the working tree (#251)."""
    out = subprocess.run(
        ["git", "ls-files", SCOPE],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(out.stdout.split())


def survey(root: pathlib.Path = ROOT) -> dict[str, object]:
    files = []
    for rel in shell_files(root):
        text = (root / rel).read_text()
        replacement = REPLACED.get(rel)
        files.append(
            {
                "path": rel,
                "lines": len(text.splitlines()),
                "by_name": len(BY_NAME.findall(text)),
                "replaced_by": replacement,
                # A mapping to a file that has since been renamed is worse
                # than no mapping: it reports done and is not.
                "replacement_exists": bool(
                    replacement and (root / replacement).exists()
                ),
                # Replaced is not deletable. This names the test that says
                # the port agrees with the shell; without one, the file stays
                # whatever its Python replacement does.
                "evidence": EVIDENCE.get(rel),
            }
        )
    lines = sum(int(f["lines"]) for f in files)
    by_name = sum(int(f["by_name"]) for f in files)
    unreplaced = [f for f in files if not f["replacement_exists"]]
    keep = [f for f in unreplaced if f["path"] in KEEP]
    dies = [f for f in unreplaced if f["path"] in DIES_WITH]
    portable = [
        f for f in unreplaced if f["path"] not in KEEP and f["path"] not in DIES_WITH
    ]
    floor = sum(int(f["lines"]) for f in keep)
    replaced = [f for f in files if f["replacement_exists"]]

    def is_cleared(f: dict[str, object]) -> bool:
        """Whether this file is defensible to delete today.

        Three ways, and the third is the library case. A driver clears on its
        own differential (EVIDENCE) or on a written deviation. A LIBRARY has
        neither and cannot: it is never invoked alone, so there is no recorded
        argv to compare against the shell's. What clears it is that every file
        which sources it is itself cleared -- at which point nothing calls it
        and deleting it removes no behaviour.

        Written as a rule rather than a table entry because the answer changes
        as its sourcers land. `lib/ds4_server.sh` has eight; a table would
        have to be re-edited eight times and would be wrong in between.
        """
        path = str(f["path"])
        if f["evidence"] or path in DEVIATIONS:
            return True
        owners = DIES_WITH.get(path)
        return bool(owners) and all(
            g["evidence"] or str(g["path"]) in DEVIATIONS
            for g in files
            if str(g["path"]) in owners
        )

    cleared = [f for f in replaced if is_cleared(f)]
    waiting = [f for f in replaced if f not in cleared]
    return {
        "cleared": [{"path": f["path"], "lines": f["lines"]} for f in cleared],
        # `blocked_on` is the sourcers a LIBRARY is still waiting for, and it
        # is empty for a driver. The two wait for different things and the
        # report said "NO EVIDENCE" to both, which reads as an oversight on a
        # file that can never have evidence of its own.
        "waiting": [
            {
                "path": f["path"],
                "lines": f["lines"],
                "blocked_on": [
                    o
                    for o in DIES_WITH.get(str(f["path"]), ())
                    if o in {str(g["path"]) for g in waiting}
                ],
            }
            for f in waiting
        ],
        # The honest progress number. `reduction_pct` is 0.0 and will stay
        # there until files are actually deleted; this says how much of the
        # deletion is currently defensible.
        "lines_if_cleared_deleted": lines - sum(int(f["lines"]) for f in cleared),
        "keep": [
            {"path": f["path"], "lines": f["lines"], "why": KEEP[str(f["path"])]}
            for f in keep
        ],
        "dies_with": [
            {
                "path": f["path"],
                "lines": f["lines"],
                "with": DIES_WITH[str(f["path"])],
            }
            for f in dies
        ],
        "deviations": [{"path": path, "why": why} for path, why in DEVIATIONS.items()],
        "portable": [{"path": f["path"], "lines": f["lines"]} for f in portable],
        "portable_lines": sum(int(f["lines"]) for f in portable),
        # What is left when every portable file is ported and every replaced
        # file retired. If this exceeds the target, the target is unreachable
        # without porting something in KEEP, and that is a decision for a
        # person -- not something to discover by accident at 359.
        "floor_lines": floor,
        "target_reachable": floor <= TARGET,
        "scope": SCOPE,
        "files": files,
        "total_lines": lines,
        "total_by_name": by_name,
        "baseline_lines": BASELINE,
        "target_lines": TARGET,
        "reduction_pct": round(100 * (1 - lines / BASELINE), 1),
        "by_name_unreplaced": sum(int(f["by_name"]) for f in unreplaced),
        "lines_if_replaced_deleted": sum(int(f["lines"]) for f in unreplaced),
        "by_name_if_replaced_deleted": sum(int(f["by_name"]) for f in unreplaced),
    }


def report(s: dict[str, object]) -> None:
    logger.info("scope: git ls-files '%s'", s["scope"])
    logger.info(
        "now:    %d files, %s lines, %s pgrep/pkill calls",
        len(s["files"]),  # type: ignore[arg-type]
        s["total_lines"],
        s["total_by_name"],
    )
    logger.info(
        "target: %s lines (10%% of the %s standing at #235); %s%% of the way",
        s["target_lines"],
        s["baseline_lines"],
        s["reduction_pct"],
    )
    logger.info("")
    logger.info("%-46s %6s %6s  %s", "file", "lines", "byname", "replaced by")
    for f in sorted(s["files"], key=lambda f: -int(f["lines"])):  # type: ignore[arg-type]
        logger.info(
            "%-46s %6s %6s  %s",
            f["path"],
            f["lines"],
            f["by_name"] or "",
            f["replaced_by"] if f["replacement_exists"] else "-- NOT REPLACED",
        )
    logger.info("")
    logger.info(
        "deleting the replaced ones leaves %s lines and %s pgrep/pkill calls",
        s["lines_if_replaced_deleted"],
        s["by_name_if_replaced_deleted"],
    )
    if not s["by_name_if_replaced_deleted"]:
        logger.info(
            "every file that matches a process by NAME has a Python "
            "replacement. Replacement is not retirement: a .sh is deleted "
            "only once its replacement has produced a run that agrees with it."
        )
    logger.info("")
    logger.info("of what is left:")
    for f in s["portable"]:  # type: ignore[union-attr]
        logger.info("  %4s  port it        %s", f["lines"], f["path"])
    for f in s["dies_with"]:  # type: ignore[union-attr]
        # The whole list, not the first name. A library whose last sourcer is
        # gone is deletable and one with seven left is not, and a line that
        # showed only one could not tell them apart.
        logger.info(
            "  %4s  dies with      %s  (%s)",
            f["lines"],
            f["path"],
            ", ".join(f["with"]),  # type: ignore[arg-type]
        )
    for f in s["deviations"]:  # type: ignore[union-attr]
        logger.info("  %4s  DEVIATION      %s  -- %s", 0, f["path"], f["why"])
    for f in s["keep"]:  # type: ignore[union-attr]
        logger.info("  %4s  STAYS SHELL    %s  -- %s", f["lines"], f["path"], f["why"])
    logger.info("")
    logger.info(
        "retirement: %d of %d replaced files are cleared to delete; "
        "deleting those leaves %s lines",
        len(s["cleared"]),  # type: ignore[arg-type]
        len(s["cleared"]) + len(s["waiting"]),  # type: ignore[arg-type]
        s["lines_if_cleared_deleted"],
    )
    for f in s["waiting"]:  # type: ignore[union-attr]
        blocked = f["blocked_on"]
        if blocked:
            logger.info(
                "  %4s  WAITS ON %-2s   %s  (%s)",
                f["lines"],
                len(blocked),  # type: ignore[arg-type]
                f["path"],
                ", ".join(pathlib.Path(o).name for o in blocked),  # type: ignore
            )
        else:
            logger.info("  %4s  NO EVIDENCE    %s", f["lines"], f["path"])
    logger.info("")
    logger.info(
        "porting the %s portable lines leaves %s, against a target of %s: %s",
        s["portable_lines"],
        s["floor_lines"],
        s["target_lines"],
        "reachable"
        if s["target_reachable"]
        else "NOT REACHABLE without porting a KEEP file",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--json", action="store_true", help="machine-readable")
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)
    s = survey()
    if args.json:
        logger.info("%s", json.dumps(s, indent=2))
    else:
        report(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
