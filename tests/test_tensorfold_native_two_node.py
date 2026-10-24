from __future__ import annotations

import pathlib
import shlex
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import tensorfold_native_two_node as t

ARGS = ["--port", "8888", "--context", "262144"]


def _cmd(rank: int) -> list[str]:
    return shlex.split(
        t.rank_command(
            rank=rank, binary="/b/tf", nccl_dir="/n", model="/m", serve_args=ARGS
        )
    )


def _env(words: list[str]) -> dict[str, str]:
    return dict(w.split("=", 1) for w in words[: words.index("timeout")])


def test_both_ranks_rendezvous_at_rank_zero_on_the_fabric() -> None:
    for rank in (0, 1):
        env = _env(_cmd(rank))
        assert env["NCCL_COMM_ID"] == "10.0.0.1:29591"
        assert env["TF_GLM_WORLD"] == "2"
        assert env["TF_GLM_RANK"] == str(rank)


def test_nccl_is_found_through_the_copied_library_dir() -> None:
    # The archive carries no libnccl; without this the binary stops with
    # NcclUnavailable (2026-10-24).
    assert _env(_cmd(0))["LD_LIBRARY_PATH"] == "/n"


def test_nccl_is_pinned_to_all_four_fabric_paths() -> None:
    env = _env(_cmd(1))
    assert env["NCCL_SOCKET_IFNAME"].count(",") == 3
    assert env["NCCL_IB_HCA"].count(",") == 3


def test_both_ranks_get_the_same_serve_arguments_and_only_rank_zero_listens_wide() -> (
    None
):
    def serve(words: list[str]) -> list[str]:
        return words[words.index("serve") : words.index("--host")]

    assert serve(_cmd(0)) == serve(_cmd(1)) == ["serve", "/m", *ARGS]
    assert _cmd(0)[-1] == "0.0.0.0"
    assert _cmd(1)[-1] == "127.0.0.1"


def test_every_rank_has_a_lifetime() -> None:
    words = _cmd(0)
    assert words[words.index("timeout") + 1] == str(t.LIFETIME_S)


def test_detached_records_the_pid_and_returns() -> None:
    line = t.detached("echo hi", "/tmp/x.log", "/tmp/x.pid")
    assert line.startswith("setsid nohup sh -c ")
    assert line.endswith("echo $! > /tmp/x.pid")


def test_stop_signals_every_group_in_the_session_not_only_the_leader() -> None:
    # timeout runs the server in its own process group; signalling the
    # leader's group alone left the server running (2026-10-24).
    cmd = t.stop_command("/tmp/x.pid")
    assert "ps -o pgid= --sid $sid" in cmd
    assert "kill -TERM -- -$g" in cmd


def test_stop_keeps_the_pid_file_while_the_session_lives() -> None:
    cmd = t.stop_command("/tmp/x.pid")
    assert cmd.index("rm -f /tmp/x.pid") > cmd.index(
        '[ -z "$(ps -o pid= --sid $sid)" ]; then'
    )
    assert "exit 1" in cmd
