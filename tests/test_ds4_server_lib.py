"""The ds4-server lifecycle module (#236).

The shell file this replaces was tested only by text invariants on its argv,
because running it needs a 74 GiB model and an M5. These tests exercise the
logic instead: what the command line contains, which failure is reported when,
and whether the server is stopped on every exit path.

Every named date is a failure that reached the machine.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))

sys.path.insert(0, str(ROOT / "benchmarks" / "agent"))

import ds4_server
import preflight
import unitctl
from source_text import code_of

MTP_LINE = (
    "ds4: Qwen graph allocated: ctx=100000 QSA=BF16 "
    "MTP=Q4_K/Q8_0/BF16 verifier=block/max16"
)
PLAIN_LINE = "ds4: Qwen graph allocated: ctx=100000 QSA=BF16 MTP=off verifier=off"


@pytest.fixture(autouse=True)
def empty_census(monkeypatch) -> None:
    """No foreign ds4-server, unless a test says otherwise (#253).

    `start()` consults the real process census, so without this the suite
    would pass or fail depending on whether a server happened to be up on the
    machine running it -- and this repo's machine runs servers for a living.
    Same family as #251: a test that passes on machine state is not a test.
    """
    monkeypatch.setattr(ds4_server.preflight, "_capture", lambda _argv: "")


@pytest.fixture
def paths(tmp_path):
    return {
        "model": tmp_path / "model.gguf",
        "ple": tmp_path / "ple.bin",
        "kv": tmp_path / "kv",
        "binary": tmp_path / "ds4-server",
        "log": tmp_path / "server.log",
    }


# --- the command line --------------------------------------------------------


def test_the_control_arm_simply_has_no_mtp_flags(paths):
    """2026-09-08: `"${mtp_args[@]}"` is an unbound variable under `set -u` on
    bash 3.2 when the array is empty, and ONLY the control arm's array is
    empty. The treatment arm ran all 15 tasks; its pair never launched. A list
    that is sometimes empty must not be a special case."""
    args = ds4_server.argv(
        paths["model"], paths["ple"], paths["kv"], binary=paths["binary"]
    )
    assert not [a for a in args if a.startswith("--mtp")]
    assert "--metal" in args and "--warm-weights" in args
    assert args[args.index("--port") + 1] == str(ds4_server.DEFAULT_PORT)


def test_the_mtp_arm_carries_its_three_flags(paths):
    args = ds4_server.argv(
        paths["model"],
        paths["ple"],
        paths["kv"],
        binary=paths["binary"],
        mtp_model=paths["model"],
        mtp_draft=7,
        mtp_timing=True,
    )
    assert args[args.index("--mtp-draft") + 1] == "7"
    assert "--mtp-timing" in args
    assert "--mtp-model" in args


def test_draft_without_a_head_warns_so_the_config_error_is_visible(paths, caplog):
    """--deepseek's point: silently dropping a flag the caller passed is how a
    config error hides. Refusing is the better end state; during a port a
    behaviour change is indistinguishable from a porting bug, so it warns."""
    with caplog.at_level("WARNING", logger=ds4_server.logger.name):
        ds4_server.argv(
            paths["model"],
            paths["ple"],
            paths["kv"],
            binary=paths["binary"],
            mtp_draft=7,
        )
    assert "cannot speculate" in caplog.text


def test_draft_and_timing_are_ignored_without_a_head(paths):
    """`--mtp-draft 7` with no `--mtp-model` is an arm that cannot speculate
    while looking like one that can -- the exact shape of #151."""
    args = ds4_server.argv(
        paths["model"],
        paths["ple"],
        paths["kv"],
        binary=paths["binary"],
        mtp_draft=7,
        mtp_timing=True,
    )
    assert not [a for a in args if a.startswith("--mtp")]


# --- which failure gets reported ---------------------------------------------


def test_an_absent_graph_line_is_not_reported_as_a_wrong_head(paths):
    """2026-09-08: the driver printed `REFUSING: control arm loaded an MTP
    head` when no server had started and there was no log at all -- the
    `no:*` shell case matched an empty string. A check that fires but names
    the wrong cause is worse than no check."""
    with pytest.raises(ds4_server.ServerNeverStarted):
        ds4_server.assert_graph(paths["log"], want_mtp=False)
    paths["log"].write_text("ds4: loading weights\nds4: some other line\n")
    with pytest.raises(ds4_server.ServerNeverStarted):
        ds4_server.assert_graph(paths["log"], want_mtp=True)


def test_an_mtp_arm_that_loaded_no_head_is_refused(paths):
    paths["log"].write_text(PLAIN_LINE + "\n")
    with pytest.raises(ds4_server.GraphMismatch, match="MTP=off"):
        ds4_server.assert_graph(paths["log"], want_mtp=True)


def test_a_control_arm_that_loaded_a_head_is_refused(paths):
    paths["log"].write_text(MTP_LINE + "\n")
    with pytest.raises(ds4_server.GraphMismatch, match="control arm"):
        ds4_server.assert_graph(paths["log"], want_mtp=False)


@pytest.mark.parametrize(
    ("line", "want"), [(MTP_LINE, True), (PLAIN_LINE, False)], ids=["mtp", "control"]
)
def test_an_arm_that_loaded_what_it_asked_for_passes(paths, line, want):
    paths["log"].write_text("noise\n" + line + "\nmore noise\n")
    assert ds4_server.assert_graph(paths["log"], want_mtp=want) == line


def test_graph_line_is_none_when_there_is_no_log(paths):
    assert ds4_server.graph_line(paths["log"]) is None


# --- provenance must never take down a run -----------------------------------


def test_route_recording_never_raises(paths, monkeypatch):
    """It is provenance. A run that dies because it could not write a
    provenance file is worse than one whose rows say `unrecorded`."""
    assert ds4_server.record_route(paths["log"], 8000, 1234) is False

    def explode(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(ds4_server.ds4_route, "record_from_log", explode)
    paths["log"].write_text(MTP_LINE + "\n")
    assert ds4_server.record_route(paths["log"], 8000, 1234) is False


# --- teardown on every exit path (#145) --------------------------------------


@pytest.fixture
def fake_server(monkeypatch, tmp_path):
    """Stand in for a 74 GiB server: record start/stop, skip the real work."""
    events: list[str] = []

    def fake_start(command, log, *, cwd, allow_foreign=False, state_dir=None):
        events.append("start")
        pathlib.Path(log).write_text(MTP_LINE + "\n")
        return unitctl.Unit(
            name=ds4_server.UNIT,
            pid=4242,
            command=list(command),
            cwd=str(cwd),
            log=str(log),
            started="2026-09-08T23:00:00-04:00",
            start_key="k",
            hostname="test",
        )

    def fake_stop(why="", state_dir=None):
        events.append("stop")
        return unitctl.STOPPED

    monkeypatch.setattr(ds4_server, "start", fake_start)
    monkeypatch.setattr(ds4_server, "stop", fake_stop)
    monkeypatch.setattr(ds4_server.wait_ready, "ready", lambda *a, **k: True)
    monkeypatch.setattr(ds4_server, "record_route", lambda *a, **k: True)
    return events


def test_the_server_is_stopped_on_the_happy_path(fake_server, tmp_path):
    """#145: `stack_agent_ab.sh` restarted the server between sweeps and
    stopped none of them, so every CLEAN finish left one resident -- 97.9 GiB,
    four runs in a row, and preflight called the machine healthy."""
    with ds4_server.serving(
        ["ds4-server"],
        tmp_path / "s.log",
        cwd=tmp_path,
        model_id="m",
        want_mtp=True,
    ):
        pass
    assert fake_server == ["start", "stop"]


def test_the_server_is_stopped_when_the_body_raises(fake_server, tmp_path):
    """A sweep that dies mid-run leaks exactly the same 98 GiB as one that
    finishes, and Ctrl-C is the likeliest way to end a long batch."""
    with (
        pytest.raises(ZeroDivisionError),
        ds4_server.serving(
            ["ds4-server"],
            tmp_path / "s.log",
            cwd=tmp_path,
            model_id="m",
            want_mtp=True,
        ),
    ):
        raise ZeroDivisionError("the sweep died")
    assert fake_server == ["start", "stop"]


def test_the_exception_is_not_swallowed_by_the_teardown(fake_server, tmp_path):
    """The shell chained EXIT traps by parsing `trap -p` with sed so a
    teardown could not discard the caller's own. The teardown must not turn a
    failed run into a successful one, or the reverse."""
    with (
        pytest.raises(RuntimeError, match="the real failure"),
        ds4_server.serving(
            ["ds4-server"],
            tmp_path / "s.log",
            cwd=tmp_path,
            model_id="m",
            want_mtp=True,
        ),
    ):
        raise RuntimeError("the real failure")


def test_a_server_that_never_answers_is_stopped_and_reported(
    fake_server, tmp_path, monkeypatch
):
    """A server that starts and never serves must not be left holding 85 GiB
    while the driver moves on."""
    monkeypatch.setattr(ds4_server.wait_ready, "ready", lambda *a, **k: False)
    with (
        pytest.raises(ds4_server.NotReady),
        ds4_server.serving(
            ["ds4-server"],
            tmp_path / "s.log",
            cwd=tmp_path,
            model_id="m",
            want_mtp=True,
            timeout=1,
        ),
    ):
        pytest.fail("the body must not run when the server never served")
    assert fake_server == ["start", "stop"]


def test_a_wrong_graph_line_stops_the_server_before_any_trial(
    fake_server, tmp_path, monkeypatch
):
    """The assertion exists to spend no machine time on a mislabelled arm, so
    it must fire before the body and still tear down."""
    with (
        pytest.raises(ds4_server.GraphMismatch),
        ds4_server.serving(
            ["ds4-server"],
            tmp_path / "s.log",
            cwd=tmp_path,
            model_id="m",
            want_mtp=False,
        ),
    ):
        pytest.fail("the body must not run for a mislabelled arm")
    assert fake_server == ["start", "stop"]


# --- nothing is searched for -------------------------------------------------


def test_the_module_never_looks_for_a_process_by_name():
    """The shell library this replaces found the server with
    `pgrep -f 'ds4-server --metal'` and killed it with `pkill`. Both are gone
    from the code; the docstring still explains why, which is why this reads
    the code rather than the file."""
    code = code_of(ROOT / "scripts" / "lib" / "ds4_server.py")
    assert "pgrep" not in code
    assert "pkill" not in code
    assert "DS4_SERVER_PATTERN" not in code


# --- a foreign server, which start() must refuse (#253) ----------------------


def _one_foreign(pid: int = 4243, gib: float = 97.9):
    return [preflight.Proc(pid=pid, rss_gib=gib, command="ds4-server --metal")]


def test_start_refuses_when_a_foreign_server_is_resident(monkeypatch, tmp_path) -> None:
    """Stopping our own unit is only half of "a clean slate". Starting beside a
    server this project did not start puts two engines on the machine at once,
    and the symptom is not a crash -- it is a run that swaps, and rows that are
    slow for a reason nobody records. That is #145 through the door the port
    left open."""
    monkeypatch.setattr(ds4_server, "foreign", lambda *a: _one_foreign())
    with pytest.raises(ds4_server.ForeignServer, match="4243"):
        ds4_server.start(
            ["sleep", "60"],
            tmp_path / "s.log",
            cwd=ROOT,
            state_dir=tmp_path / "units",
        )
    assert not ds4_server.running(tmp_path / "units")


def test_the_refusal_names_the_pid_and_the_memory(monkeypatch, tmp_path) -> None:
    """A refusal an operator cannot act on gets overridden rather than obeyed."""
    monkeypatch.setattr(ds4_server, "foreign", lambda *a: _one_foreign(gib=97.9))
    with pytest.raises(ds4_server.ForeignServer) as caught:
        ds4_server.start(
            ["sleep", "60"], tmp_path / "s.log", cwd=ROOT, state_dir=tmp_path / "units"
        )
    assert "97.9 GiB" in str(caught.value)


def test_the_refusal_can_be_overridden_deliberately(monkeypatch, tmp_path) -> None:
    """A refusal with no way through gets deleted rather than satisfied."""
    monkeypatch.setattr(ds4_server, "foreign", lambda *a: _one_foreign())
    unit = ds4_server.start(
        ["sleep", "60"],
        tmp_path / "s.log",
        cwd=ROOT,
        allow_foreign=True,
        state_dir=tmp_path / "units",
    )
    assert unit.pid
    ds4_server.stop(state_dir=tmp_path / "units")


def test_a_clean_machine_does_not_refuse(monkeypatch, tmp_path) -> None:
    """The negative case. Without it the guard could refuse everything and the
    library would be unreachable until an overnight run produced nothing."""
    monkeypatch.setattr(ds4_server, "foreign", lambda *a: [])
    unit = ds4_server.start(
        ["sleep", "60"], tmp_path / "s.log", cwd=ROOT, state_dir=tmp_path / "units"
    )
    assert unit.pid
    ds4_server.stop(state_dir=tmp_path / "units")


def test_our_own_server_is_not_foreign(monkeypatch, tmp_path) -> None:
    """ "Foreign" must mean "not ours", or the check refuses our own server and
    no arm can ever start."""
    state = tmp_path / "units"
    unit = ds4_server.start(
        ["sleep", "60"], tmp_path / "s.log", cwd=ROOT, state_dir=state
    )
    census = [
        preflight.Proc(pid=unit.pid, rss_gib=97.9, command="ds4-server --metal ours"),
        preflight.Proc(
            pid=unit.pid + 99999, rss_gib=97.9, command="ds4-server --metal"
        ),
    ]
    monkeypatch.setattr(preflight, "parse_ps", lambda _text: census)
    assert [p.pid for p in ds4_server.foreign(state)] == [unit.pid + 99999]
    ds4_server.stop(state_dir=state)


def test_foreign_never_signals_anything() -> None:
    """`pkill`-ing a process this project did not start is what #235 exists to
    remove. foreign() reports; the operator decides."""
    code = code_of(ROOT / "scripts" / "lib" / "ds4_server.py")
    body = code[code.index("def foreign") : code.index("def stop")]
    for word in ("kill", "terminate", "signal", "SIGKILL", "SIGTERM"):
        assert word not in body, f"foreign() must not {word}"
