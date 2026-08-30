"""Tests for the GGUF header reader.

It is a binary parser whose output gets quoted in conclusions -- #33 turns on
`split.count` and `split.tensors.count` differing between two builds. A
misparse would be invisible and would read as a finding about the model.
"""

from __future__ import annotations

import pathlib
import struct
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import gguf_meta


def _string(s: str) -> bytes:
    raw = s.encode()
    return struct.pack("<Q", len(raw)) + raw


def _build(pairs: list[tuple[str, int, bytes]], n_tensors: int = 7) -> bytes:
    out = b"GGUF" + struct.pack("<IQQ", 3, n_tensors, len(pairs))
    for key, kind, payload in pairs:
        out += _string(key) + struct.pack("<I", kind) + payload
    return out


@pytest.fixture
def sample(tmp_path: pathlib.Path) -> pathlib.Path:
    pairs = [
        ("general.architecture", 8, _string("qwen4exp")),
        ("split.count", 4, struct.pack("<I", 33)),
        ("split.tensors.count", 10, struct.pack("<Q", 1224)),
        ("qwen4exp.ple.heads_per_ngram", 4, struct.pack("<I", 8)),
        # A short array is kept whole; a long one is summarised.
        (
            "qwen4exp.ple.head_offsets",
            9,
            struct.pack("<IQ", 10, 3) + struct.pack("<3Q", 0, 20000003, 40000026),
        ),
        (
            "tokenizer.ggml.tokens",
            9,
            struct.pack("<IQ", 8, 6) + b"".join(_string(t) for t in "abcdef"),
        ),
    ]
    p = tmp_path / "m.gguf"
    p.write_bytes(_build(pairs))
    return p


def test_scalars_of_different_widths_are_read_correctly(sample):
    got = gguf_meta.read(sample)
    assert got["split.count"] == 33  # uint32
    assert got["split.tensors.count"] == 1224  # uint64
    assert got["qwen4exp.ple.heads_per_ngram"] == 8


def test_strings_are_decoded(sample):
    assert gguf_meta.read(sample)["general.architecture"] == "qwen4exp"


def test_a_short_numeric_array_is_kept_whole(sample):
    """head_offsets is compared between builds; summarising it would hide a diff."""
    assert gguf_meta.read(sample)["qwen4exp.ple.head_offsets"] == [
        0,
        20000003,
        40000026,
    ]


def test_a_long_string_array_is_summarised_not_expanded(sample):
    """A 250k-token vocabulary must not be printed or held."""
    got = gguf_meta.read(sample)["tokenizer.ggml.tokens"]
    assert "6 strings" in got


def test_every_key_is_read_so_later_keys_are_not_shifted(sample):
    """The real failure mode: one mis-sized value desynchronises the whole rest.

    `tokenizer.ggml.tokens` is last here on purpose -- if the array skip is
    wrong, the keys before it still parse and only the tail is garbage.
    """
    got = gguf_meta.read(sample)
    assert len(got) == 6


def test_a_non_gguf_file_is_refused(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"NOPE" + b"\0" * 64)
    with pytest.raises(ValueError, match="not a GGUF"):
        gguf_meta.read(p)


def test_an_unknown_value_type_raises_rather_than_guessing(tmp_path):
    """Silently skipping an unknown type would desynchronise everything after."""
    p = tmp_path / "m.gguf"
    p.write_bytes(_build([("weird", 99, b"\x00\x00\x00\x00")]))
    with pytest.raises(ValueError, match="unsupported value type"):
        gguf_meta.read(p)
