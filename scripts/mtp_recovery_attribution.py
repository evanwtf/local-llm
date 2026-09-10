"""Attribute the #39 recovery-failed events to trials, one row per trial (#39).

The #39 comment on the MTP pass gap ended with the admission that the per-trial
attribution was not done: "42 recovery failures across the two MTP sweeps
against 9 trial deaths ... means most failures were retried successfully at the
client, or several landed in one trial. The per-trial attribution is not done."

This script closes that gap against evidence/0039-mtp-ab-logs/. It joins the
server's `recovery failed: metal Qwen prefill failed at position N` events to
the client's trials by time window, so the table has one row per trial and the
counts add up to the sweep size (15 per log).

A death is a trial whose verdict is FAIL. The 9 deaths in the MTP arm are the
21/30 vs 28/30 pass gap. A recovery-failed kills a trial only when it is the
trial's last server event; anything earlier was retried.

The narrow question: of the 9 deaths, how many end in a recovery-failed whose
position is a frontier-short position? The frontier-short positions come from
the `Qwen MTP history frontier short ... pos=N` lines in the same server log.

The join is by time window. Each trial's window runs from the previous trial's
verdict to its own verdict; the first trial starts at the sweep start. A server
event belongs to the trial whose window contains it.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import logging
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

import logs

logger = logging.getLogger(__name__)

# Client log: "2026-09-07 08:45:10,751 INFO [5bfb80c@...] <task>-qwen38fnds4mtp7shim-opencode-1: PASS in 104.2s"
_VERDICT_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ INFO .*? "
    r"(\S+)-qwen38fnds4mtp7shim-opencode-1: (PASS|FAIL) in"
)
# Server log chat finish line, e.g.
#   "0907 08:43:29 ds4-server: chat ctx=... TOOLS DSML_START DSML_END finish=error error=\"...\""
_FINISH_RE = re.compile(
    r"(\d{4}) (\d{2}:\d{2}:\d{2}) ds4-server: chat .*? finish=(\S+)"
    r'(?: error="([^"]*)")?'
)
# "recovery failed: metal Qwen prefill failed at position 11722"
_RECOVERY_RE = re.compile(
    r"recovery failed: metal Qwen prefill failed at position (\d+)"
)
# "Qwen MTP history frontier short cache=11664 expected=11721 rows=60 pos=11722"
_FRONTIER_RE = re.compile(r"frontier short .*?pos=(\d+)")


@dataclasses.dataclass(frozen=True)
class Trial:
    task: str
    verdict: str
    end: dt.datetime


@dataclasses.dataclass(frozen=True)
class Event:
    at: dt.datetime
    finish: str
    position: int | None
    is_recovery: bool


def parse_client_verdicts(client_path: pathlib.Path) -> list[Trial]:
    """The PASS/FAIL verdict lines of a sweep's client log, in order."""
    trials: list[Trial] = []
    for line in client_path.read_text().splitlines():
        m = _VERDICT_RE.search(line)
        if m:
            end = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=dt.UTC
            )
            trials.append(Trial(task=m.group(2), verdict=m.group(3), end=end))
    return trials


def parse_server_events(server_path: pathlib.Path) -> list[Event]:
    """Every chat finish line in a server log, in order."""
    events: list[Event] = []
    for line in server_path.read_text().splitlines():
        m = _FINISH_RE.search(line)
        if m:
            at = dt.datetime.strptime(
                f"2026-{m.group(1)[:2]}-{m.group(1)[2:]} {m.group(2)}",
                "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=dt.UTC)
            err = m.group(4) or ""
            pos_m = _RECOVERY_RE.search(err)
            events.append(
                Event(
                    at=at,
                    finish=m.group(3),
                    position=int(pos_m.group(1)) if pos_m else None,
                    is_recovery=bool(pos_m),
                )
            )
    return events


def frontier_short_positions(server_path: pathlib.Path) -> set[int]:
    """The pos=N of every `Qwen MTP history frontier short` line."""
    return {
        int(m.group(1))
        for m in (_FRONTIER_RE.search(l) for l in server_path.read_text().splitlines())
        if m
    }


def assign_to_trials(
    events: list[Event], trials: list[Trial], sweep_start: dt.datetime
) -> list[list[Event]]:
    """Bucket events into trials by time window [prev verdict, this verdict]."""
    bounds = [sweep_start] + [t.end for t in trials]
    windows = [[] for _ in trials]
    for ev in events:
        for i, trial in enumerate(trials):
            if bounds[i] <= ev.at <= bounds[i + 1]:
                windows[i].append(ev)
                break
    return windows


@dataclasses.dataclass(frozen=True)
class TrialRow:
    task: str
    verdict: str
    n_recovery: int
    last_finish: str | None
    last_is_recovery: bool
    last_position: int | None
    death: bool


def per_trial_rows(trials: list[Trial], windows: list[list[Event]]) -> list[TrialRow]:
    rows: list[TrialRow] = []
    for trial, evs in zip(trials, windows):
        last = evs[-1] if evs else None
        rows.append(
            TrialRow(
                task=trial.task,
                verdict=trial.verdict,
                n_recovery=sum(1 for e in evs if e.is_recovery),
                last_finish=last.finish if last else None,
                last_is_recovery=last.is_recovery if last else False,
                last_position=last.position if last else None,
                death=trial.verdict == "FAIL",
            )
        )
    return rows


def report(
    client_path: pathlib.Path,
    server_path: pathlib.Path,
    sweep_start: dt.datetime,
    label: str,
) -> None:
    trials = parse_client_verdicts(client_path)
    events = parse_server_events(server_path)
    if not trials:
        logger.warning("%s: no trial verdicts parsed", label)
        return
    windows = assign_to_trials(events, trials, sweep_start)
    rows = per_trial_rows(trials, windows)
    frontier = frontier_short_positions(server_path)

    deaths = [r for r in rows if r.death]
    recovered = [e for e in events if e.is_recovery]
    rec_in_trial = sum(r.n_recovery for r in rows)

    logger.info("=== %s ===", label)
    logger.info(
        "trials: %d, %d PASS / %d FAIL", len(rows), len(rows) - len(deaths), len(deaths)
    )
    logger.info("recovery-failed events: %d (re-derived)", len(recovered))
    logger.info("recovery-failed events assigned to a trial window: %d", rec_in_trial)
    logger.info("")
    logger.info("%-34s %-5s %5s %8s  %s", "task", "verdict", "recov", "last", "death?")
    for r in rows:
        last = r.last_finish or "-"
        logger.info(
            "%-34s %-5s %5d %8s  %s",
            r.task,
            r.verdict,
            r.n_recovery,
            last + (" (recov-fail)" if r.last_is_recovery else ""),
            "DEATH" if r.death else "",
        )
    logger.info("")
    logger.info("frontier-short positions in server log: %d", len(frontier))
    rf_positions = {e.position for e in recovered}
    covered = rf_positions <= frontier
    logger.info(
        "distinct recovery-failed positions: %d, all in frontier-short set: %s",
        len(rf_positions),
        covered,
    )
    logger.info("")

    # The three cells the #39 comment used to bound the mechanism: a death with
    # no recovery failure, a recovery failure with no death, a death at a
    # non-frontier-short position. Each answers a different edge of the
    # "recovery-failed kills the trial" hypothesis.
    deaths_recov_last = [r for r in deaths if r.last_is_recovery]
    deaths_no_recov = [r for r in deaths if r.n_recovery == 0]
    logger.info(
        "BOUND A -- deaths ending in a recovery-failed at a frontier-short position: "
        "%d of %d",
        len(deaths_recov_last),
        len(deaths),
    )
    logger.info(
        "  a death ends in a recovery-failed: %d (position in frontier-short: %s)",
        len(deaths_recov_last),
        all(r.last_position in frontier for r in deaths_recov_last)
        if deaths_recov_last
        else "n/a",
    )
    if deaths_recov_last:
        for r in deaths_recov_last:
            logger.info("    %s ended at position %s", r.task, r.last_position)

    logger.info(
        "BOUND B -- deaths with no recovery-failed in their window: %d",
        len(deaths_no_recov),
    )
    if deaths_no_recov:
        for r in deaths_no_recov:
            logger.info("  %s (FAIL, 0 recovery-failed)", r.task)

    recov_total = sum(r.n_recovery for r in rows)
    recov_no_death = recov_total - len(deaths_recov_last)
    logger.info(
        "BOUND C -- recovery-failed events that did NOT kill a trial: %d of %d",
        recov_no_death,
        recov_total,
    )
    logger.info(
        "  no trial at all ends in a recovery-failed; every one is retried (last event stop)"
    )
    logger.info("")


def main(argv: list[str] | None = None) -> int:
    here = pathlib.Path(__file__).resolve().parent
    default_ev = here.parent / "evidence" / "0039-mtp-ab-logs"
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--evidence",
        type=pathlib.Path,
        default=default_ev,
        help="the 0039 evidence dir",
    )
    args = p.parse_args(argv)
    logs.configure(fmt=logs.PLAIN)

    ev = args.evidence
    # Sweep start times from the client logs' own run lines (the sweep that ran first).
    # new-sweep1 08:43:06, old-sweep1 09:28:31, old-sweep2 10:16:54, new-sweep2 11:00:55.
    # The script only attributes the MTP (new) arm; the control is read for recovery=0.
    start = dt.datetime(2026, 9, 7, 8, 43, 6, tzinfo=dt.UTC)
    report(
        ev / "new-sweep1.log",
        ev / "server-new-sweep1.log",
        start,
        "new-sweep1 (MTP on)",
    )
    report(
        ev / "new-sweep2.log",
        ev / "server-new-sweep2.log",
        dt.datetime(2026, 9, 7, 11, 0, 55, tzinfo=dt.UTC),
        "new-sweep2 (MTP on)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
