"""#148: a trial's draft accounting is its own, and absent is not zero."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import run

MICRO = "ds4: mtp timing micro drafted={d} committed={c} draft=1.0 ms snapshot=0.1 ms verify=2.0 ms total=3.1 ms"
SKIP = "ds4: mtp timing margin-skip drafted=2 committed=1 margin=0.5 threshold=3.0 draft=1.0 ms verify=2.0 ms total=3.0 ms"


def test_no_server_log_yields_no_field_at_all(tmp_path):
    """Absent must stay absent. A zero here would assert something we
    did not measure."""
    probe = run.DraftProbe(None)
    assert probe.sample() is None
    assert run.draft_fields(None) is None


def test_the_probe_ignores_everything_written_before_it_existed(tmp_path):
    """The smoke gate drafts too, and it is not trial 1's work."""
    log = tmp_path / "server.log"
    log.write_text((MICRO.format(d=7, c=5) + "\n") * 3)
    probe = run.DraftProbe(log)  # constructed after the gate ran
    assert probe.sample().cycles == ()


def test_each_trial_gets_only_its_own_cycles(tmp_path):
    log = tmp_path / "server.log"
    log.write_text("")
    probe = run.DraftProbe(log)

    with log.open("a") as handle:
        handle.write(MICRO.format(d=7, c=5) + "\n")
    first = run.draft_fields(probe.sample())
    assert first["cycles"] == 1 and first["accepted"] == 4

    with log.open("a") as handle:
        handle.write(SKIP + "\n")
    second = run.draft_fields(probe.sample())
    assert second["cycles"] == 1, "trial 2 must not inherit trial 1's cycle"
    assert second["accepted"] == 0
    assert second["used"] is False


def test_fields_carry_the_raw_counts_so_used_can_be_recomputed(tmp_path):
    log = tmp_path / "server.log"
    log.write_text(MICRO.format(d=7, c=5) + "\n")
    probe = run.DraftProbe(log)
    probe.offset = 0
    fields = run.draft_fields(probe.sample())
    assert fields["proposed"] == 6
    assert fields["accepted"] == 4
    assert fields["used"] is True
    assert fields["accept_rate"] == 4 / 6
