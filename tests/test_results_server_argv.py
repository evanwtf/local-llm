"""#213: rows with different graph-changing server_argv must not pool.

Only 3 of 1,646 rows carry `env.server_argv` -- the shim-fronted backends that
ran the MTP A/B never captured it. A row with no argv is unknown, and unknown
must not compare equal to known: a row that says nothing about its graph could
have run anything. Two known rows agree only when every graph-changing flag
matches. Paths and ports are harmless and must not refuse a pool.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent"))

import results


def row(argv: str | None) -> dict:
    env = {"server_argv": argv} if argv is not None else {}
    return {"env": env}


# --- graph_flags ------------------------------------------------------------

def test_graph_flags_ignores_paths_and_ports():
    a = "./ds4-server -m /a/model.gguf -c 100000 --port 8000"
    b = "./ds4-server -m /b/model.gguf -c 100000 --port 9000"
    assert results.graph_flags(a) == results.graph_flags(b)


def test_graph_flags_catches_ctx_change():
    a = "./ds4-server -m /a.gguf -c 100000"
    b = "./ds4-server -m /a.gguf -c 200000"
    assert results.graph_flags(a) != results.graph_flags(b)


def test_graph_flags_catches_prefill_chunk():
    a = "./ds4-server -m /a.gguf --prefill-chunk 4096"
    b = "./ds4-server -m /a.gguf --prefill-chunk 8192"
    assert results.graph_flags(a) != results.graph_flags(b)


def test_graph_flags_catches_mtp_model():
    a = "./ds4-server -m /a.gguf --mtp /mtp.gguf"
    b = "./ds4-server -m /a.gguf --mtp /other.gguf"
    assert results.graph_flags(a) != results.graph_flags(b)


def test_graph_flags_catches_power():
    a = "./ds4-server -m /a.gguf --power 50"
    b = "./ds4-server -m /a.gguf --power 80"
    assert results.graph_flags(a) != results.graph_flags(b)


def test_graph_flags_catches_kv_disk_family():
    a = "./ds4-server -m /a.gguf --kv-disk-dir /ssd"
    b = "./ds4-server -m /a.gguf"
    assert results.graph_flags(a) != results.graph_flags(b)


def test_graph_flags_catches_ssd_streaming_family():
    a = "./ds4-server -m /a.gguf --ssd-streaming-cold"
    b = "./ds4-server -m /a.gguf"
    assert results.graph_flags(a) != results.graph_flags(b)


def test_graph_flags_boolean_presence_is_a_value():
    """A boolean flag present in one argv and absent in the other differs."""
    a = "./ds4-server -m /a.gguf --ssd-streaming"
    b = "./ds4-server -m /a.gguf"
    assert results.graph_flags(a) != results.graph_flags(b)


def test_graph_flags_short_and_long_ctx_are_both_caught():
    assert results.graph_flags("./ds4-server -m /a.gguf -c 100000") == {
        "-c": "100000",
    }
    assert results.graph_flags("./ds4-server -m /a.gguf --ctx 100000") == {
        "--ctx": "100000",
    }


def test_graph_flags_ignores_the_model_path():
    """The weights path is a path; the model identity lives in gguf_path."""
    a = "./ds4-server -m /a/model.gguf -c 100000"
    b = "./ds4-server -m /b/model.gguf -c 100000"
    assert results.graph_flags(a) == results.graph_flags(b)


# --- server_argv_compatible -------------------------------------------------

def test_both_unknown_are_compatible():
    assert results.server_argv_compatible(row(None), row(None)) is True


def test_known_vs_unknown_is_incompatible():
    """A row that says nothing about its graph could have run anything."""
    assert results.server_argv_compatible(row(None), row("./ds4-server -m /a.gguf")) is False
    assert results.server_argv_compatible(row("./ds4-server -m /a.gguf"), row(None)) is False


def test_known_with_same_graph_is_compatible():
    a = row("./ds4-server -m /a.gguf -c 100000 --port 8000")
    b = row("./ds4-server -m /a.gguf -c 100000 --port 9000")
    assert results.server_argv_compatible(a, b) is True


def test_known_with_different_graph_is_incompatible():
    a = row("./ds4-server -m /a.gguf -c 100000")
    b = row("./ds4-server -m /a.gguf -c 200000")
    assert results.server_argv_compatible(a, b) is False


# --- pool_compatible / compatible_subset ------------------------------------

def test_all_unknown_pool_is_compatible():
    assert results.pool_compatible([row(None), row(None), row(None)]) is True


def test_mixed_pool_is_incompatible():
    assert results.pool_compatible([row(None), row("./ds4-server -m /a.gguf")]) is False


def test_pool_with_different_graphs_is_incompatible():
    a = row("./ds4-server -m /a.gguf -c 100000")
    b = row("./ds4-server -m /a.gguf -c 200000")
    assert results.pool_compatible([a, b]) is False


def test_compatible_subset_keeps_the_majority():
    """One known outlier must not void the whole cell."""
    unknown = [row(None) for _ in range(3)]
    known = row("./ds4-server -m /a.gguf -c 100000")
    kept = results.compatible_subset(unknown + [known])
    assert kept == unknown


def test_compatible_subset_empty_input():
    assert results.compatible_subset([]) == []


def test_compatible_subset_all_known_same_graph_keeps_all():
    rows = [
        row("./ds4-server -m /a.gguf -c 100000 --port 8000"),
        row("./ds4-server -m /a.gguf -c 100000 --port 9000"),
    ]
    assert results.compatible_subset(rows) == rows
