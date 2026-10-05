"""Tests for Swift function-body excision.

The Python side gets this for free from `ast`. Swift has no such luxury here,
so the span is found by matching braces — and a brace scanner that does not
understand strings and comments will cut in the wrong place and produce a file
that still compiles. That is the failure mode worth engineering against: a
wrong span does not crash, it silently changes what the task is.
"""

from __future__ import annotations

import pathlib

import pytest
from swift_excise import TargetNotFound, body_source, excise

SAMPLE = """\
import Foundation

/// Bucket samples for display.
///
/// Multi-line doc comment.
public enum Downsample {
    /// How many buckets fit.
    public static func buckets(_ n: Int) -> [Int] {
        let out = (0..<n).map { i in i * 2 }
        return out
    }

    static func label(_ s: String) -> String {
        // a brace in a comment: {
        let brace = "}"
        return s + brace
    }
}

func topLevel() -> Int {
    return 1
}
"""


@pytest.fixture
def sample(tmp_path: pathlib.Path) -> pathlib.Path:
    p = tmp_path / "Downsample.swift"
    p.write_text(SAMPLE)
    return p


def test_a_top_level_func_body_is_removed(sample):
    removed = excise(sample, "topLevel")
    after = sample.read_text()
    assert "return 1" in removed
    assert "return 1" not in after
    assert "func topLevel() -> Int {" in after
    assert "fatalError" in after


def test_a_method_is_addressed_by_type_and_name(sample):
    removed = excise(sample, "Downsample.buckets")
    assert "let out = (0..<n).map" in removed
    assert "let out" not in sample.read_text()
    # the sibling method must survive
    assert "func label(_ s: String)" in sample.read_text()


def test_a_closure_brace_inside_the_body_does_not_end_it(sample):
    """`{ i in i * 2 }` is a closure. A naive scanner stops at its `}`."""
    removed = excise(sample, "Downsample.buckets")
    assert "return out" in removed, "scanner stopped at the closure brace"


def test_a_brace_in_a_comment_is_ignored(sample):
    removed = excise(sample, "Downsample.label")
    assert "return s + brace" in removed, "scanner was confused by `{` in a comment"


def test_a_brace_in_a_string_literal_is_ignored(sample):
    removed = excise(sample, "Downsample.label")
    assert 'let brace = "}"' in removed, "scanner was confused by `}` in a string"


def test_the_doc_comment_is_kept_by_default(sample):
    excise(sample, "Downsample.buckets")
    assert "/// How many buckets fit." in sample.read_text()


def test_the_doc_comment_goes_when_asked(sample):
    removed = excise(sample, "Downsample.buckets", keep_docstring=False)
    assert "How many buckets fit." in removed
    assert "How many buckets fit." not in sample.read_text()


def test_body_source_returns_exactly_what_excise_removes(sample):
    """If these drift, `restored_verbatim` compares unequal spans and never fires."""
    before = body_source(sample, "Downsample.buckets")
    assert before == excise(sample, "Downsample.buckets")


def test_body_source_agrees_without_the_doc_comment_too(sample):
    before = body_source(sample, "Downsample.buckets", keep_docstring=False)
    assert before == excise(sample, "Downsample.buckets", keep_docstring=False)


def test_body_source_does_not_modify_the_file(sample):
    original = sample.read_text()
    body_source(sample, "topLevel")
    assert sample.read_text() == original


def test_a_missing_symbol_raises(sample):
    with pytest.raises(TargetNotFound):
        body_source(sample, "nope")
    with pytest.raises(TargetNotFound):
        excise(sample, "Downsample.nope")


def test_the_wrong_type_does_not_match_a_right_name(sample):
    """`Other.buckets` must not silently hit `Downsample.buckets`."""
    with pytest.raises(TargetNotFound):
        excise(sample, "Other.buckets")


def test_the_result_still_parses_as_balanced_braces(sample):
    """A wrong span leaves a file that may still compile. Check the shape."""
    excise(sample, "Downsample.buckets")
    text = sample.read_text()
    assert text.count("{") == text.count("}")


# --- a declaration inside a comment or string (code review, 2026-10-05) ------
#
# The declaration search ran over raw source, so an example in a block comment
# or a multi-line string was found first: its body was "excised" and the real
# function was left intact.

COMMENTED = """\
/*
 Usage:
   func topLevel() -> Int { return 99 }
*/
let help = \"\"\"
    func label(_ s: String) -> String { return "" }
    enum Downsample { }
    \"\"\"

public enum Downsample {
    static func label(_ s: String) -> String {
        return s + "!"
    }
}

func topLevel() -> Int {
    return 1
}
"""


def test_a_func_in_a_block_comment_is_not_the_target(tmp_path):
    p = tmp_path / "C.swift"
    p.write_text(COMMENTED)
    removed = excise(p, "topLevel")
    assert "return 1" in removed and "return 99" not in removed
    after = p.read_text()
    assert "return 99" in after and "return 1\n" not in after


def test_a_type_or_func_in_a_string_is_not_the_target(tmp_path):
    p = tmp_path / "C.swift"
    p.write_text(COMMENTED)
    assert 'return s + "!"' in body_source(p, "Downsample.label")
    assert 'return s + "!"' in body_source(p, "Downsample.label", keep_docstring=False)
    excise(p, "Downsample.label", keep_docstring=False)
    after = p.read_text()
    assert 'return s + "!"' not in after
    assert 'func label(_ s: String) -> String { return "" }' in after


def test_a_commented_out_only_declaration_is_not_found(tmp_path):
    p = tmp_path / "C.swift"
    p.write_text("/* func ghost() { return } */\nfunc real() {\n    return\n}\n")
    with pytest.raises(TargetNotFound):
        body_source(p, "ghost")
