"""Tests for scripts/weights_report.py (#464).

The report never deletes, but a wrong verdict is still expensive: the operator
acts on it. So the verdict rules are pinned with absolute sizes, one test per
band and per signal, and the partial-download detector is tested on real
directory shapes.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import weights_report as w

GB = 10**9


def facts(size: int | None, **kw: object) -> w.Facts:
    base: dict[str, object] = {
        "size": size,
        "incomplete": 0,
        "has_weights": True,
        "backends": (),
        "ledger_rows": 0,
        "recommended": False,
        "issues": None,
    }
    base.update(kw)
    return w.Facts(**base)  # type: ignore[arg-type]


def test_a_partial_download_is_delete_whatever_its_size() -> None:
    assert w.verdict(facts(3 * GB, incomplete=2))[0] == w.DELETE
    assert w.verdict(facts(300 * GB, incomplete=1, recommended=True))[0] == w.DELETE


def test_a_directory_with_no_weight_files_is_delete() -> None:
    label, reason = w.verdict(facts(8192, has_weights=False))
    assert label == w.DELETE
    assert "no weight files" in reason


def test_a_recommended_model_is_kept_even_over_200_gb() -> None:
    assert w.verdict(facts(250 * GB, recommended=True))[0] == w.KEEP


def test_over_200_gb_is_probably_delete_even_with_rows_and_a_backend() -> None:
    label, reason = w.verdict(facts(201 * GB, backends=("x",), ledger_rows=90))
    assert label == w.PROBABLY_DELETE
    assert "201" in reason


def test_exactly_200_gb_is_in_the_middle_band() -> None:
    assert w.verdict(facts(200 * GB, backends=("x",)))[0] == w.KEEP


def test_under_10_gb_is_probably_keep_even_with_no_footprint() -> None:
    assert w.verdict(facts(9 * GB))[0] == w.PROBABLY_KEEP


def test_middle_band_signals() -> None:
    assert w.verdict(facts(50 * GB, backends=("q3",)))[0] == w.KEEP
    assert w.verdict(facts(50 * GB, ledger_rows=12))[0] == w.CONSIDER
    assert w.verdict(facts(50 * GB))[0] == w.UNKNOWN


def test_an_issue_mention_moves_unknown_to_consider() -> None:
    assert w.verdict(facts(50 * GB, issues=(111,)))[0] == w.CONSIDER
    assert w.verdict(facts(50 * GB, issues=()))[0] == w.UNKNOWN


def test_unknown_size_uses_the_middle_band() -> None:
    assert w.verdict(facts(None))[0] == w.UNKNOWN


def test_needle_for_each_root_shape() -> None:
    hf = pathlib.Path("/h/.cache/huggingface/hub/models--unsloth--GLM-5.3-Flash-GGUF")
    assert w.needle(w.Item("Hugging Face cache", hf)) == "GLM-5.3-Flash-GGUF"
    assert w.repo_id(hf) == "unsloth/GLM-5.3-Flash-GGUF"
    gguf = pathlib.Path("/h/models/x-Q4.gguf")
    assert w.needle(w.Item("ds4/llama.cpp GGUF", gguf)) == "x-Q4"
    tag = pathlib.Path("qwen3.5:9b-mlx")
    assert w.needle(w.Item("Ollama", tag, ollama=True)) == "qwen3.5:9b-mlx"


def test_reacquire_commands() -> None:
    hf = pathlib.Path("/h/.cache/huggingface/hub/models--a--b")
    assert w.reacquire(w.Item("Hugging Face cache", hf)) == "hf download a/b"
    tag = pathlib.Path("gemma4:latest")
    assert (
        w.reacquire(w.Item("Ollama", tag, ollama=True)) == "ollama pull gemma4:latest"
    )
    assert w.reacquire(w.Item("mlx-serve", pathlib.Path("/h/o/p"))) == ""


def test_ollama_sizes_parse_from_list_output() -> None:
    text = (
        "NAME             ID            SIZE      MODIFIED\n"
        "qwen3.5:2b-mlx   9dd89c688f18  3.1 GB    3 weeks ago\n"
        "big:latest       aaaaaaaaaaaa  518 MB    2 days ago\n"
        "huge:1t          bbbbbbbbbbbb  1.2 TB    now\n"
    )
    assert w.parse_ollama_sizes(text) == {
        "qwen3.5:2b-mlx": 3_100_000_000,
        "big:latest": 518_000_000,
        "huge:1t": 1_200_000_000_000,
    }


def test_weight_scan_finds_partial_and_empty(tmp_path: pathlib.Path) -> None:
    empty = tmp_path / "glm"
    (empty / ".cache").mkdir(parents=True)
    assert w.scan_weights(empty) == (0, False)

    hf = tmp_path / "models--a--b"
    (hf / "blobs").mkdir(parents=True)
    (hf / "blobs" / "abc.incomplete").write_bytes(b"x")
    snap = hf / "snapshots" / "rev"
    snap.mkdir(parents=True)
    (snap / "model-00001.safetensors").symlink_to(hf / "blobs" / "abc.incomplete")
    assert w.scan_weights(hf) == (1, True)

    single = tmp_path / "m.gguf"
    single.write_bytes(b"g")
    assert w.scan_weights(single) == (0, True)


def test_rows_count_once_per_item_across_its_needles() -> None:
    a = w.Item("r", pathlib.Path("/m/Qwen-Q3"))
    b = w.Item("r", pathlib.Path("/m/absent"))
    lines = ['{"a": "/m/Qwen-Q3/x.gguf"}', '{"b": "x.gguf"}', '{"c": "other"}']
    groups = {a: ("Qwen-Q3", "x.gguf"), b: ("absent",)}
    assert w.count_rows(lines, groups) == {a: 2, b: 0}


def test_file_needles_are_gguf_names_only(tmp_path: pathlib.Path) -> None:
    (tmp_path / "Qwen-Q4.gguf").write_bytes(b"g")
    (tmp_path / "model-00001.safetensors").write_bytes(b"s")
    assert w.file_needles(tmp_path) == ("Qwen-Q4.gguf",)
    assert w.file_needles(tmp_path / "Qwen-Q4.gguf") == ()


def test_backends_naming_a_needle() -> None:
    backends = {
        "q3": {"model": "qwen3.8-flash-next-q3", "server": "/m/Qwen-Q3/x.gguf"},
        "mlx": {"model": "other"},
    }
    assert w.backends_naming(backends, "Qwen-Q3") == ("q3",)
    assert w.backends_naming(backends, "nothing") == ()


def test_the_report_never_deletes() -> None:
    source = (pathlib.Path(w.__file__)).read_text()
    for call in ("unlink(", "rmtree(", "os.remove(", '"rm"', "'rm'"):
        assert call not in source


def test_a_symlink_into_another_entry_is_not_a_saving(tmp_path: pathlib.Path) -> None:
    """LM Studio's Q3_K_XL links into ~/models; its 90 GB was counted twice."""
    gguf = tmp_path / "models" / "Qwen3.8-Flash-Next-GGUF"
    (gguf / "UD-Q3_K_XL").mkdir(parents=True)
    lms = tmp_path / "lmstudio" / "Qwen3.8-Flash-Next-UD-Q3_K_XL"
    lms.parent.mkdir()
    lms.symlink_to(gguf / "UD-Q3_K_XL")
    other = tmp_path / "models" / "REAP320"
    other.mkdir()
    a = w.Item("gguf", gguf)
    b = w.Item("lmstudio", lms)
    c = w.Item("gguf", other)
    items = [a, b, c]
    real = {i: i.path.resolve() for i in items}
    assert w.link_targets(items, real) == {b: a}


def test_two_entries_with_one_real_path_keep_the_first(tmp_path: pathlib.Path) -> None:
    d = tmp_path / "m"
    d.mkdir()
    link = tmp_path / "l"
    link.symlink_to(d)
    a, b = w.Item("x", d), w.Item("y", link)
    real = {i: i.path.resolve() for i in (a, b)}
    assert w.link_targets([a, b], real) == {b: a}
