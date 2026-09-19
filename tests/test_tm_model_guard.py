"""Tests for scripts/tm_model_guard.py (#440): no model weights in Time Machine.

On 2026-09-18 the network backup share was 94% full: 773 GB of weights in six
directories that the exclusion list, written on an earlier day, did not name.
The guard must catch the next such directory. These tests pin the decisions
that would let one through silently: what counts as a model file, which
directories the walk skips, and when the exit status is a failure.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import tm_model_guard as g

GIB = 1024**3


# ------------------------------------------------------------ model files


def test_a_large_gguf_is_a_model_file():
    assert g.is_model_file(pathlib.Path("/x/Qwen3.8-Flash-Next-Q4.gguf"), 20 * GIB)


def test_a_safetensors_shard_is_a_model_file():
    assert g.is_model_file(pathlib.Path("/x/model-00001-of-00008.safetensors"), 4 * GIB)


def test_the_suffix_check_ignores_case():
    assert g.is_model_file(pathlib.Path("/x/WEIGHTS.GGUF"), 2 * GIB)


def test_a_small_gguf_is_not_worth_flagging():
    """A tokenizer or a test fixture; the guard is about space, not purity."""
    assert not g.is_model_file(pathlib.Path("/x/vocab.gguf"), GIB // 2)


def test_a_large_file_with_another_suffix_is_not_a_model_file():
    """A disk image or a video is large, but it is the operator's data."""
    assert not g.is_model_file(pathlib.Path("/x/archive.dmg"), 40 * GIB)


def test_an_mlx_npz_is_a_model_file():
    assert g.is_model_file(pathlib.Path("/x/weights.npz"), 3 * GIB)


# ------------------------------------------------------------ the walk


def test_the_find_command_prunes_every_skip_path():
    argv = g.find_argv(pathlib.Path("/h"), ["/h/models", "/h/.ds4"])
    assert argv[:3] == ["find", "-x", "/h"]
    assert "/h/models" in argv
    assert "/h/.ds4" in argv
    assert argv.count("-prune") == 1
    assert "+1048576k" in argv


def test_the_find_command_still_works_with_nothing_to_skip():
    argv = g.find_argv(pathlib.Path("/h"), [])
    assert "-prune" not in argv
    assert argv[-1] == "-print0"


def test_skip_paths_outside_home_are_dropped():
    """find prunes by path; a path it will never reach only slows the walk."""
    got = g.prunable(pathlib.Path("/h"), ["/h/models", "/Volumes/x", "/h"])
    assert got == ["/h/models"]


def test_the_skip_path_list_parses_from_the_preferences_plist():
    plist = {"SkipPaths": ["/h/models", "/h/.ds4"], "AutoBackup": True}
    assert g.skip_paths(plist) == ["/h/models", "/h/.ds4"]


def test_a_missing_skip_path_list_is_empty_not_an_error():
    assert g.skip_paths({}) == []


def test_find_output_splits_on_nul():
    raw = b"/h/a b.gguf\0/h/c.safetensors\0"
    assert g.split_paths(raw) == [
        pathlib.Path("/h/a b.gguf"),
        pathlib.Path("/h/c.safetensors"),
    ]


# ------------------------------------------------------------ the verdict


def test_an_excluded_model_file_passes():
    files = [(pathlib.Path("/h/m.gguf"), 20 * GIB)]
    got = g.check(files, is_excluded=lambda p: True)
    assert got.included_models == []
    assert got.exit_code == 0


def test_an_included_model_file_fails():
    files = [(pathlib.Path("/h/git/new/gguf/m.gguf"), 20 * GIB)]
    got = g.check(files, is_excluded=lambda p: False)
    assert got.included_models == files
    assert got.exit_code == 1


def test_other_large_files_are_counted_but_do_not_fail():
    files = [(pathlib.Path("/h/Movies/x.mov"), 30 * GIB)]
    got = g.check(files, is_excluded=lambda p: False)
    assert got.included_models == []
    assert got.other_large == files
    assert got.exit_code == 0


def test_a_file_under_one_gib_of_another_kind_is_not_counted():
    files = [(pathlib.Path("/h/a.zip"), GIB - 1)]
    got = g.check(files, is_excluded=lambda p: False)
    assert got.other_large == []


def test_tmutil_output_is_read_as_excluded_or_included():
    assert g.parse_isexcluded("[Excluded]    /h/models/x.gguf\n")
    assert not g.parse_isexcluded("[Included]    /h/git/x.gguf\n")


def test_a_sparse_file_counts_by_the_bytes_it_holds(tmp_path):
    """OrbStack's disk image read 3.17 TB apparent and held 904 MB."""
    f = tmp_path / "sparse.img"
    with f.open("wb") as fh:
        fh.truncate(4 * GIB)
    assert f.stat().st_size == 4 * GIB
    assert g.allocated(f.stat()) < GIB
