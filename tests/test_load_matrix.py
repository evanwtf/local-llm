"""Tests for the #158 load matrix.

The load matrix starts a real ds4-server per model, which is expensive and
machine-bound. The tests pin the two pure functions instead: the server
command carries the model and PLE sidecar, and the port check refuses a held
port. The load itself is verified by the evidence artifact, not by a unit
test.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import load_matrix


def test_server_command_carries_model_and_ple():
    """The command names the model, the PLE sidecar, and the port."""
    cmd = load_matrix._server_command(
        pathlib.Path("/x/ds4"), pathlib.Path("/x/model.gguf"), 8000
    )
    assert cmd[0] == "/x/ds4/ds4-server"
    assert "-m" in cmd and "/x/model.gguf" in cmd
    assert "--ple" in cmd and str(load_matrix.PLE) in cmd
    assert "--port" in cmd and "8000" in cmd


def test_port_free_true_when_nothing_listens():
    """A free port reports free."""
    assert load_matrix._port_free(0) is True


def test_port_free_false_when_held():
    """A held port reports held."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert load_matrix._port_free(port) is False
