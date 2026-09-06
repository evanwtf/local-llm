"""The Metal tensor-route equivalence gate (#149, item 2).

`ds4_test --metal-tensor-equivalence` compares the fast Metal 4 tensor route
against the reference kernels on five fixtures and says how far apart they are.
It has been available all along and was never run by anything -- preflight
logged `Metal tensor API is on` for weeks and nobody read it as a warning,
which is how four ds4 arms came to run a route whose output nobody had checked.

The gate turns that into a refusal. Two properties matter and are tested here:
it must **parse the verdict rather than the exit code alone**, and it must
know when its cached answer no longer describes the binary on disk -- a stale
pass is the failure mode that would let a rebuilt engine through unchecked.
"""

from __future__ import annotations

import json

import metal_equivalence as me

SUMMARY = (
    "ds4-test: Tensor summary route=auto cases=5 capture_fail=0 logits_fail=0 "
    "greedy_fail=0 top1_mismatch=0 min_top5_overlap=3/5 min_overlap=16/20 "
    "worst_rank_delta=7 worst_rms=1.01843 worst_max_abs=5.3259 "
    "worst_top20_max_abs=2.92744"
)

# The real run on ds4-metal @ ba01f5d, DeepSeek-V4-Flash 0731, 2026-09-06.
# Three consecutive runs produced this byte for byte.
REAL_LOG = f"""\
metal-tensor-equivalence:
ds4: Metal device Apple M5 Max, 128.00 GiB RAM
ds4: Metal 4 tensor API enabled for Tensor kernels
ds4-test: Tensor equivalence long_code_audit top1 ref=671 cand=671 top5_overlap=3/5
{SUMMARY}
metal-tensor-equivalence: OK
ds4 tests: ok
"""


def test_the_summary_line_is_parsed_into_numbers():
    got = me.parse_summary(REAL_LOG)
    assert got["cases"] == 5
    assert got["greedy_fail"] == 0
    assert got["top1_mismatch"] == 0
    assert got["worst_rms"] == 1.01843
    assert got["worst_max_abs"] == 5.3259
    assert got["worst_rank_delta"] == 7


def test_a_log_with_no_summary_parses_to_nothing():
    assert me.parse_summary("ds4: cannot open model\n") == {}


def test_a_greedy_agreement_run_passes():
    assert me.verdict(0, REAL_LOG) == "pass"


def test_a_flipped_greedy_token_fails_even_on_a_zero_exit():
    """The whole reason the exit code is not the verdict.

    #149 is about the first *sampled* token flipping on long prompts. A build
    whose test tolerates that and exits 0 must still not gate a run open.
    """
    flipped = REAL_LOG.replace("greedy_fail=0", "greedy_fail=1")
    assert me.verdict(0, flipped) == "fail"


def test_a_top1_mismatch_fails():
    mismatched = REAL_LOG.replace("top1_mismatch=0", "top1_mismatch=2")
    assert me.verdict(0, mismatched) == "fail"


def test_a_nonzero_exit_fails_whatever_the_log_says():
    assert me.verdict(1, REAL_LOG) == "fail"


def test_a_run_with_no_summary_is_unknown_not_a_pass():
    """A model that would not load produced exit 1 and no summary. Say so."""
    assert me.verdict(1, "ds4: cannot open model 'ds4flash.gguf'\n") == "fail"
    assert me.verdict(0, "nothing useful here\n") == "unknown"


def test_drift_short_of_a_flip_still_passes_but_is_recorded(tmp_path):
    """The route is not bit-exact and the record must not imply it is.

    worst_rms 1.02 and worst_max_abs 5.33 on a code audit is real drift. It
    does not flip a greedy token on these five fixtures, which is what the gate
    is for -- but a reader of the cache should see the numbers, not just 'pass'.
    """
    path = tmp_path / "eq.json"
    me.write_verdict(path, fingerprint="abc", verdict="pass", summary=me.parse_summary(REAL_LOG))
    got = json.loads(path.read_text())
    assert got["summary"]["worst_max_abs"] == 5.3259
    assert got["verdict"] == "pass"


def test_a_cached_pass_is_only_believed_for_the_same_binary(tmp_path):
    """A rebuilt engine invalidates the answer. This is the stale-pass guard."""
    path = tmp_path / "eq.json"
    me.write_verdict(path, fingerprint="abc", verdict="pass", summary={})
    assert me.cached_verdict(path, "abc") == "pass"
    assert me.cached_verdict(path, "def") == "stale"


def test_no_cache_at_all_is_absent_not_a_pass(tmp_path):
    assert me.cached_verdict(tmp_path / "absent.json", "abc") == "absent"


def test_a_corrupt_cache_is_absent_not_a_pass(tmp_path):
    path = tmp_path / "eq.json"
    path.write_text("{oh no")
    assert me.cached_verdict(path, "abc") == "absent"


def test_the_fingerprint_changes_when_the_binary_changes(tmp_path):
    binary = tmp_path / "ds4_test"
    model = tmp_path / "m.gguf"
    binary.write_text("v1")
    model.write_text("weights")
    first = me.fingerprint(binary, model)
    binary.write_text("v2 is longer")
    assert me.fingerprint(binary, model) != first


def test_the_fingerprint_changes_when_the_model_changes(tmp_path):
    binary = tmp_path / "ds4_test"
    model = tmp_path / "m.gguf"
    binary.write_text("v1")
    model.write_text("weights")
    first = me.fingerprint(binary, model)
    model.write_text("different weights")
    assert me.fingerprint(binary, model) != first


def test_a_missing_binary_fingerprints_to_none_rather_than_raising(tmp_path):
    assert me.fingerprint(tmp_path / "nope", tmp_path / "also-nope") is None
