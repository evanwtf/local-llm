"""One status line, and the two fields that used to print the wrong thing.

`scripts/ab_status.py` is the port of `scripts/ab_status.sh` (#235). The shell
set the rule in its own header -- *"a field that cannot be computed says so
rather than printing empty"* -- and then broke it in two fields, in opposite
directions. Both were confirmed by RUNNING the shell, not by reading it:

    $ ROWS=$(cat /nonexistent/*.csv 2>/dev/null | grep -c '^[0-9]' || echo 0)
    ROWS=[0
    0]
    $ TEMP=$(echo "no json here" | sed -E 's/.*"die_max_c": ([0-9.]+).*/\1C/') \
        || TEMP="unreadable"
    TEMP=[no json here]

So the tests that matter here are: a status line is ONE line, and a field that
cannot be computed says `unreadable` rather than a number-shaped lie.
"""

from __future__ import annotations

import logging
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ab_status


def run_dir(base: pathlib.Path, name: str, *, rows: int = 0) -> pathlib.Path:
    """A directory `is_complete` accepts: two arms, same reps, same lengths."""
    d = base / name
    d.mkdir(parents=True)
    for arm in ("a", "b"):
        for rep in (1, 2):
            body = "ctx,rate\n" + "".join(
                f"{2048 * i},10.0\n" for i in range(1, rows + 1)
            )
            (d / f"{arm}-rep{rep}.csv").write_text(body)
    return d


# --- the current run, and which one it is ------------------------------------


def test_run_dirs_are_name_ordered_not_filesystem_ordered(tmp_path) -> None:
    """The shell took `ls -d ...* | tail -1`. A monitor whose "current run"
    depends on directory order is worse than no monitor."""
    for name in ("r-run3", "r-run1", "r-run2"):
        (tmp_path / name).mkdir()
    got = ab_status.run_dirs(str(tmp_path / "r-run"))
    assert [d.name for d in got] == ["r-run1", "r-run2", "r-run3"]


def test_a_file_matching_the_prefix_is_not_a_run(tmp_path) -> None:
    (tmp_path / "r-run1").mkdir()
    (tmp_path / "r-run1.log").write_text("")
    assert [d.name for d in ab_status.run_dirs(str(tmp_path / "r-run"))] == ["r-run1"]


def test_completeness_is_post_ab_runs_answer_not_a_second_copy(tmp_path) -> None:
    """Three copies of this rule drifted apart in one afternoon (a03ca8d)."""
    full = run_dir(tmp_path, "r-run1", rows=2)
    half = tmp_path / "r-run2"
    half.mkdir()
    (half / "a-rep1.csv").write_text("ctx,rate\n2048,10.0\n")
    got = ab_status.complete(ab_status.run_dirs(str(tmp_path / "r-run")))
    assert got == [full]


# --- the row count: the "0\n0" defect ----------------------------------------


def test_a_run_with_only_headers_counts_zero_rows(tmp_path) -> None:
    """Zero is a number. This is the case the shell got wrong: `grep -c` exits
    1 on no match, so `|| echo 0` ran too and the field held "0\\n0"."""
    d = run_dir(tmp_path, "r-run1", rows=0)
    assert ab_status.rows(d) == 0


def test_the_status_line_is_one_line_when_no_row_has_been_written(tmp_path) -> None:
    """The direct regression. A monitor watching for the first row is exactly
    the reader the two-line status line broke."""
    run_dir(tmp_path, "r-run1", rows=0)
    text, _ = ab_status.line(str(tmp_path / "r-run"))
    assert "\n" not in text
    assert " at 0 rows " in text


def test_rows_are_counted_across_every_csv_in_the_run(tmp_path) -> None:
    d = run_dir(tmp_path, "r-run1", rows=3)
    assert ab_status.rows(d) == 12  # 2 arms x 2 reps x 3 rows


def test_a_run_directory_with_no_csv_has_no_row_count(tmp_path) -> None:
    """Not zero. "No CSV yet" and "a CSV with no rows" are different facts."""
    (tmp_path / "r-run1").mkdir()
    assert ab_status.rows(tmp_path / "r-run1") is None


def test_no_run_directory_at_all_reads_unreadable(tmp_path) -> None:
    assert ab_status.rows(None) is None
    text, code = ab_status.line(str(tmp_path / "absent"))
    assert "\n" not in text
    assert text.count(ab_status.UNREADABLE) == 2  # the name and the row count
    assert code == 0


# --- the die temperature: the sed-passthrough defect -------------------------


def test_a_failing_sensor_reads_unreadable_not_its_own_error_text(
    tmp_path, monkeypatch
) -> None:
    """The shell's `sed ... || TEMP="unreadable"` never fired: sed SUCCEEDS
    when its pattern does not match and passes the line through, so a failing
    thermals.py put its last line of output in the `die=` field, where a
    reader takes it for a temperature."""
    monkeypatch.setattr(
        ab_status.thermals,
        "reading",
        lambda: (_ for _ in ()).throw(RuntimeError("powermetrics: no such sensor")),
    )
    assert ab_status.die_temp() is None
    text, _ = ab_status.line(str(tmp_path / "absent"))
    assert f"die={ab_status.UNREADABLE}" in text
    assert "powermetrics" not in text


def test_a_sensor_that_answers_without_a_die_reading_is_not_a_temperature(
    monkeypatch,
) -> None:
    """A reading dict is not a promise that the die sensor was in it."""
    monkeypatch.setattr(ab_status.thermals, "reading", lambda: {"fan_rpm": 2400})
    assert ab_status.die_temp() is None


def test_a_die_reading_is_reported_as_a_number(monkeypatch) -> None:
    monkeypatch.setattr(ab_status.thermals, "reading", lambda: {"die_max_c": 41.5})
    assert ab_status.die_temp() == 41.5


# --- the median line, which belongs to the report ----------------------------


def test_no_complete_run_says_so_rather_than_reporting_on_nothing() -> None:
    assert ab_status.median_line([]) == "no complete run yet"


def test_the_report_is_captured_not_echoed(tmp_path, monkeypatch, capsys) -> None:
    """The bug the first cut had.

    Capturing by ADDING a handler leaves the report's own output on the root
    handlers, so a script whose whole promise is one status line printed 118
    lines of A/B tables first. The shell never had it: a command substitution
    captures stdout by taking it away.
    """
    said = []

    def fake_main(argv):
        logging.getLogger("decode_ab_report").info("4 runs: median b/a 1.176 (+17.6%)")
        said.append(argv)
        return 0

    monkeypatch.setattr(ab_status.decode_ab_report, "main", fake_main)
    root = logging.getLogger()
    root.addHandler(logging.StreamHandler(sys.stdout))
    got = ab_status.median_line([tmp_path / "r-run1"])
    assert got == "4 runs: median b/a 1.176 (+17.6%)"
    assert "1.176" not in capsys.readouterr().out
    assert said and said[0][0] == "decode_ab_report"


def test_a_root_logger_at_warning_still_hears_the_report(tmp_path, monkeypatch) -> None:
    """The capture has to raise the level as well as own the handlers.

    `logs.configure` is a no-op once the root has a handler, so the report's
    own call to it does not set the level either. A root left at WARNING
    drops every line the report writes and the capture reads as "the report
    said nothing" -- which renders as `report gave no median line`, a
    sentence about the report that is really about the logger.
    """
    root = logging.getLogger()
    saved = root.level
    root.setLevel(logging.WARNING)
    try:

        def fake_main(argv):
            logging.getLogger("decode_ab_report").info("4 runs: median b/a 1.10")
            return 0

        monkeypatch.setattr(ab_status.decode_ab_report, "main", fake_main)
        assert ab_status.median_line([tmp_path / "r"]) == "4 runs: median b/a 1.10"
        assert root.level == logging.WARNING, "the level must be put back"
    finally:
        root.setLevel(saved)


def test_the_root_handlers_come_back_even_when_the_report_raises(
    tmp_path, monkeypatch
) -> None:
    """Losing them would silence every later line, including this script's."""
    before = logging.getLogger().handlers[:]

    def boom(argv):
        raise ValueError("bad csv")

    monkeypatch.setattr(ab_status.decode_ab_report, "main", boom)
    got = ab_status.median_line([tmp_path / "r-run1"])
    assert got == "REPORT FAILED: bad csv"
    assert logging.getLogger().handlers == before


def test_a_nonzero_report_reports_its_last_line(tmp_path, monkeypatch) -> None:
    def unhappy(argv):
        logging.getLogger("decode_ab_report").info("no usable rows in q4-rep1.csv")
        return 1

    monkeypatch.setattr(ab_status.decode_ab_report, "main", unhappy)
    got = ab_status.median_line([tmp_path / "r-run1"])
    assert got == "REPORT FAILED: no usable rows in q4-rep1.csv"


def test_a_report_with_no_median_line_says_that_rather_than_guessing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(ab_status.decode_ab_report, "main", lambda argv: 0)
    assert ab_status.median_line([tmp_path / "r-run1"]) == "report gave no median line"


def test_the_paired_spelling_is_read_too(tmp_path, monkeypatch) -> None:
    """One complete run prints `paired median`; several print `runs: median`."""

    def one_run(argv):
        logging.getLogger("decode_ab_report").info(
            "paired median pr964/main across frontiers: 1.165  (+16.5%)"
        )
        return 0

    monkeypatch.setattr(ab_status.decode_ab_report, "main", one_run)
    assert ab_status.median_line([tmp_path / "r"]).startswith("paired median")


# --- the exit code a waiting loop branches on --------------------------------


def test_every_wanted_run_complete_exits_10(tmp_path) -> None:
    for n in (1, 2):
        run_dir(tmp_path, f"r-run{n}", rows=2)
    _, code = ab_status.line(str(tmp_path / "r-run"), want=2)
    assert code == 10


def test_one_run_short_exits_0(tmp_path) -> None:
    run_dir(tmp_path, "r-run1", rows=2)
    _, code = ab_status.line(str(tmp_path / "r-run"), want=2)
    assert code == 0


# --- against runs this machine actually produced -----------------------------

REAL = ROOT / "benchmarks/ds4/decode-ab-964"


@pytest.mark.skipif(not REAL.is_dir(), reason="the #964 run directories are absent")
def test_the_real_964_runs_report_the_reports_own_number() -> None:
    """Four complete runs already in the repo, reported end to end.

    The median line must be the REPORT's, not one this script computed: the
    whole reason ab_status delegates is that a second copy of the ratio would
    drift from the first.
    """
    dirs = ab_status.run_dirs(str(REAL))
    assert len(dirs) == 4
    lines, status = ab_status.report_lines(dirs)
    assert status == 0
    text, code = ab_status.line(str(REAL), want=4)
    assert code == 10
    assert "\n" not in text
    assert "4/4 complete" in text
    assert text.rsplit(" | ", 1)[-1] in lines
