"""Tests for the model-on-disk directory listing.

The enumeration is the expensive-bug surface: a wrong glob or the wrong root
depth is exactly the false negative this module exists to prevent -- a pack that
is on disk reported as missing. The 2026-09-10 regression is pinned directly:
an mlx-serve pack under `~/.mlx-serve/models/<org>/` must be found even when
`~/models` does not contain it.
"""

from __future__ import annotations

import pathlib

import model_inventory as mi


def _touch(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")


def test_depth1_lists_dirs_and_bare_weight_files(tmp_path: pathlib.Path) -> None:
    root_dir = tmp_path / "models"
    (root_dir / "qwen-ds4-q4k").mkdir(parents=True)
    _touch(root_dir / "GLM-5.3-Flash-Q2.gguf")
    _touch(root_dir / "pack.safetensors")
    _touch(root_dir / "notes.txt")  # not a weight, not a dir -> skipped
    (root_dir / ".cache").mkdir()  # hidden -> skipped

    got = {e.path.name for e in mi.list_root(mi.Root("ds4", root_dir, depth=1))}

    assert got == {"qwen-ds4-q4k", "GLM-5.3-Flash-Q2.gguf", "pack.safetensors"}


def test_depth2_lists_org_slash_pack(tmp_path: pathlib.Path) -> None:
    root_dir = tmp_path / ".mlx-serve" / "models"
    (root_dir / "ddalcu" / "Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit").mkdir(
        parents=True
    )
    (root_dir / "mlx-community" / "gemma-4-e4b-it-4bit").mkdir(parents=True)
    _touch(root_dir / "ddalcu" / "README.md")  # a file at org level -> skipped

    got = mi.list_root(mi.Root("mlx-serve", root_dir, depth=2))
    names = {e.path.name for e in got}

    assert names == {
        "Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit",
        "gemma-4-e4b-it-4bit",
    }
    # Full path, not just the pack name: the whole point is knowing where it lives.
    assert all(str(e.path).startswith(str(root_dir)) for e in got)
    assert any(e.path.parent.name == "ddalcu" for e in got)


def test_missing_root_is_empty_not_an_error(tmp_path: pathlib.Path) -> None:
    assert mi.list_root(mi.Root("gone", tmp_path / "nope", depth=1)) == []
    assert mi.list_root(mi.Root("gone", tmp_path / "nope", depth=2)) == []


def test_mlx_serve_pack_found_when_models_lacks_it(tmp_path: pathlib.Path) -> None:
    """The 2026-09-10 regression: the pack is in the mlx-serve tree, not ~/models."""
    (tmp_path / "models" / "some-other-gguf").mkdir(parents=True)
    (tmp_path / ".mlx-serve" / "models" / "ddalcu" / "Qwen3.8-Flash-Next").mkdir(
        parents=True
    )

    inv = mi.log_inventory(home=tmp_path)

    ds4_names = {e.path.name for e in inv["ds4/llama.cpp GGUF"]}
    mlx_names = {e.path.name for e in inv["mlx-serve"]}
    assert "Qwen3.8-Flash-Next" not in ds4_names  # not where a naive ls looks
    assert "Qwen3.8-Flash-Next" in mlx_names  # but the listing finds it


def test_parse_ollama_list_drops_header_and_blanks() -> None:
    text = (
        "NAME                 ID            SIZE    MODIFIED\n"
        "qwen3.5:9b-mlx       203e30078279  8.9 GB  2 days ago\n"
        "\n"
        "ornith:latest        a75697c14589  5.6 GB  2 days ago\n"
    )
    got = mi.parse_ollama_list(text)
    assert [e.path.name for e in got] == ["qwen3.5:9b-mlx", "ornith:latest"]
    assert all(e.label == "ollama tag" for e in got)


def test_log_inventory_covers_every_root_and_never_raises(
    tmp_path: pathlib.Path,
) -> None:
    # A bare home with no model trees at all: still returns all labels, no raise.
    inv = mi.log_inventory(home=tmp_path)
    assert set(inv) == {
        "ds4/llama.cpp GGUF",
        "mlx-serve",
        "LM Studio",
        "Hugging Face cache",
        "Ollama",
    }
    assert all(v == [] for v in inv.values())
