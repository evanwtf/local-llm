"""The bit-exact A/B instrument, against a fake ds4-cli.

The instrument's first duty is refusing to answer when the machine cannot
answer (the same tree disagreeing with itself under identical arguments), so
that is the first test. Every test drives a fake ds4-cli -- never a real
engine, never a model load.
"""

from __future__ import annotations

import json
import logging
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import bitexact_ab as ab

FAKE_CLI = '''#!/usr/bin/env python3
"""A fake ds4-cli: whitespace tokenizer, canned dump-logprobs JSON."""
import json, os, pathlib, sys

ARGS = sys.argv[1:]

def flag(name):
    return ARGS[ARGS.index(name) + 1]

with pathlib.Path(os.environ["FAKE_CALLS"]).open("a") as calls:
    calls.write(json.dumps({"argv": ARGS,
                            "pin": os.environ.get("DS4_MTP_SPEC_DISABLE")}) + "\\n")

if "--dump-tokens" in ARGS:
    words = pathlib.Path(flag("--prompt-file")).read_text().split()
    extra = int(os.environ.get("FAKE_TOK_EXTRA", "0"))
    ids = list(range(1, len(words) + 1 + extra))
    print("[" + ", ".join(str(i) for i in ids) + "]")
    for i in ids:
        piece = " pad%d" % i if i > len(words) else (
            words[i - 1] if i == 1 else " " + words[i - 1])
        print("%6d  %s" % (i, piece))
    sys.exit(0)

OUT = pathlib.Path(flag("--dump-logprobs"))
STATE = pathlib.Path(os.environ["FAKE_STATE"])
n = int(STATE.read_text()) if STATE.exists() else 0
plan = os.environ.get("FAKE_PLAN", "ok").split(",")
mode = plan[n] if n < len(plan) else "ok"
STATE.write_text(str(n + 1))

STEPS = int(os.environ.get("FAKE_STEPS", "4"))
PTOK = int(os.environ.get("FAKE_PROMPT_TOKENS", "8"))

def step(j):
    sel = " tok%d" % j
    return {"step": j, "selected": sel,
            "top_logprobs": [
                {"token": sel, "logit": 12.5 - 0.1 * j, "logprob": -0.01},
                {"token": " alt%d" % j, "logit": 11.0 - 0.1 * j, "logprob": -1.5}]}

def baseline(count=None, ptok=None):
    return {"source": "ds4",
            "prompt_tokens": PTOK if ptok is None else ptok,
            "ctx": 24, "top_k": 20,
            "steps": [step(j) for j in range(STEPS if count is None else count)]}

if mode == "fail":
    print("ds4: fake arm failure", file=sys.stderr)
    sys.exit(1)
if mode == "trunc":
    OUT.write_text(json.dumps(baseline(2))[:-40])
    sys.exit(1)
if mode == "oktrunc":
    OUT.write_text(json.dumps(baseline(2))[:-40])
    sys.exit(0)
parts = mode.split("@")
doc = baseline()
if parts[0] == "diverge":
    doc["steps"][int(parts[1])]["selected"] = " DIFF"
elif parts[0] == "logprobs":
    doc["steps"][int(parts[1])]["top_logprobs"][0]["logit"] = 99.25
elif parts[0] == "short":
    doc = baseline(count=int(parts[1]))
elif parts[0] == "ptok":
    doc = baseline(ptok=int(parts[1]))
OUT.write_text(json.dumps(doc, indent=1))
sys.exit(0)
'''


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """Two fake trees, a 64-word corpus, and the fake's state files."""
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FAKE_CALLS", str(tmp_path / "calls.jsonl"))
    trees = {}
    for name in ("tree-a", "tree-b"):
        tree = tmp_path / name
        tree.mkdir()
        cli = tree / ab.CLI_NAME
        cli.write_text(FAKE_CLI)
        cli.chmod(0o755)
        trees[name] = tree
    gguf = tmp_path / "model.gguf"
    gguf.write_text("")
    corpus = tmp_path / "corpus.txt"
    corpus.write_text(" ".join(f"w{i:02d}" for i in range(1, 65)) + "\n")
    return {
        "trees": trees,
        "gguf": gguf,
        "corpus": corpus,
        "out": tmp_path / "out",
        "calls": tmp_path / "calls.jsonl",
    }


def invoke(bench, extra=()):
    """Run the instrument over both frontiers; return (rc, report or None)."""
    argv = [
        "new",
        str(bench["trees"]["tree-a"]),
        "old",
        str(bench["trees"]["tree-b"]),
        str(bench["gguf"]),
        "--corpus",
        str(bench["corpus"]),
        "--out",
        str(bench["out"]),
        "--no-lock",
        "--gen",
        "4",
        "--frontier",
        "8",
        "--frontier",
        "16",
        *extra,
    ]
    rc = ab.main(argv)
    report_path = bench["out"] / "report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else None
    return rc, report


def calls(bench):
    return [json.loads(x) for x in bench["calls"].read_text().splitlines()]


def dump_calls(bench):
    return [c for c in calls(bench) if "--dump-logprobs" in c["argv"]]


def read_report(bench):
    return json.loads((bench["out"] / "report.json").read_text())


# --- the first test: the same tree disagreeing with itself ------------------


def test_same_tree_divergence_refuses_and_never_runs_the_other_arm(
    bench, monkeypatch, caplog
):
    """Temperature 0 is not automatically deterministic on Metal. If tree A
    disagrees with itself under identical arguments, the instrument cannot
    answer at all -- it must refuse loudly and must not blame tree B."""
    monkeypatch.setenv("FAKE_PLAN", "ok,diverge@1")
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    assert rc == 2
    assert "cannot answer" in caplog.text
    assert "disagrees with itself" in caplog.text
    # Only the two self-check runs happened; tree B was never spent on a
    # question the machine had already said it cannot answer.
    assert len(dump_calls(bench)) == 2
    report = read_report(bench)
    assert report["frontier_results"][0]["outcome"] == "self_check_failed"
    assert report["verdict"].startswith("REFUSED")


# --- the three outcomes -----------------------------------------------------


def test_identical_runs_report_identical_for_all_steps(bench, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("FAKE_PLAN", "ok,ok,ok")
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    assert rc == 0
    assert "identical for all 4 steps" in caplog.text
    assert "IDENTICAL at print resolution" in caplog.text
    report = read_report(bench)
    result = report["frontier_results"][0]
    assert result["outcome"] == "identical"
    assert result["self_check"]["identical"] is True
    assert "first_divergence" not in result
    # The limit is stated, not implied.
    assert "print resolution" in report["verdict"]
    assert report["sampling_record"]["engine_reports_sampling"] is False


def test_first_divergence_position_is_reported_not_just_a_boolean(
    bench, monkeypatch, caplog
):
    """'Differs at token 47 of 128' and 'identical for all 128' are different
    findings; the report must carry the position."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("FAKE_PLAN", "ok,ok,diverge@2")
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    assert rc == 0  # a valid measurement; the answer is "not bit-exact"
    assert "diverged at step 2 of 4" in caplog.text
    report = read_report(bench)
    result = report["frontier_results"][0]
    assert result["outcome"] == "diverged"
    assert result["first_divergence"] == {
        "step": 2,
        "kind": "selected",
        "detail": result["first_divergence"]["detail"],
    }
    assert result["first_divergence"]["detail"].startswith(
        "arm A selected ' tok2', arm B ' DIFF'"
    )
    assert "DIVERGED" in report["verdict"]
    assert "does not hold at print resolution" in report["verdict"]


def test_logit_divergence_with_matching_tokens_is_its_own_finding(bench, monkeypatch):
    """Identical text over different logits is not bit-exact. The selected
    tokens can hide a numeric divergence; the top-k comparison must not."""
    monkeypatch.setenv("FAKE_PLAN", "ok,ok,logprobs@1")
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    assert rc == 0
    report = read_report(bench)
    result = report["frontier_results"][0]
    assert result["outcome"] == "diverged"
    assert result["first_divergence"]["step"] == 1
    assert result["first_divergence"]["kind"] == "logprobs"
    assert "top-k logits" in result["first_divergence"]["detail"]


def test_arm_failure_is_not_reported_as_not_bit_exact(bench, monkeypatch):
    """A run where an arm errors is the source_repo_intact collapse: it is no
    answer at all, and must never be classified as a divergence."""
    monkeypatch.setenv("FAKE_PLAN", "ok,ok,fail")
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    assert rc == 2  # no valid comparison exists; exit nonzero
    report = read_report(bench)
    result = report["frontier_results"][0]
    assert result["outcome"] == "arm_failed"
    assert result["arm"] == "B"
    assert "failed to run" in result["detail"]
    assert "not 'not bit-exact'" in result["detail"]
    assert "NO ANSWER" in report["verdict"]


def test_a_self_check_arm_failure_is_also_no_answer(bench, monkeypatch):
    monkeypatch.setenv("FAKE_PLAN", "fail")
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    assert rc == 2
    report = read_report(bench)
    result = report["frontier_results"][0]
    assert result["outcome"] == "arm_failed"
    assert result["arm"] == "A"
    assert "self-check" in result["detail"]


def test_a_crash_truncated_dump_is_an_arm_failure(bench, monkeypatch):
    """The dump writer fclose()s only at the end; a decode that dies mid-run
    leaves a truncated file. Exited 0 with a truncated dump is still an arm
    that did not finish cleanly."""
    monkeypatch.setenv("FAKE_PLAN", "ok,ok,oktrunc")
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    assert rc == 2
    report = read_report(bench)
    result = report["frontier_results"][0]
    assert result["outcome"] == "arm_failed"
    assert "truncated" in result["detail"]


# --- pinning and recording --------------------------------------------------


def test_seed_zero_is_refused(bench, caplog):
    """ds4 treats seed 0 as unset and seeds from time/pid/clock. A 'fixed'
    seed of 0 fixes nothing."""
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--seed",
            "0",
        ]
    )
    assert rc == 2
    assert "seed 0" in caplog.text
    assert "nonzero" in caplog.text


def test_sampling_is_pinned_and_recorded_with_the_conditionality_note(
    bench, monkeypatch
):
    """#26: a comparison whose sampling was not recorded is conditional on
    something nobody wrote down. The engine will not report its sampling
    parameters, so the instrument records the invocation instead and says so."""
    monkeypatch.setenv("FAKE_PLAN", "ok,ok,ok")
    ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
            "--seed",
            "7",
        ]
    )
    report = read_report(bench)
    record = report["sampling_record"]
    assert record["engine_reports_sampling"] is False
    assert record["requested"]["temperature"] == 0
    assert record["requested"]["seed"] == 7
    assert "conditional" in record["note"]
    assert report["env_pins"]["DS4_MTP_SPEC_DISABLE"] == "1"
    argv_a = report["frontier_results"][0]["argv"]["A"]
    assert "--temp" in argv_a and "0" in argv_a
    assert "--seed" in argv_a and "7" in argv_a
    assert "--raw" in argv_a
    # The pin reached the arm, not just the report.
    assert all(c["pin"] == "1" for c in dump_calls(bench))


# --- frontier mechanics -----------------------------------------------------


def test_default_frontiers_are_2048_and_16384():
    minimal = ["a", "tree-a", "b", "tree-b", "m.gguf"]
    assert ab.DEFAULT_FRONTIERS == (2048, 16384)
    assert ab.frontiers_of(ab.parse_args(minimal)) == [2048, 16384]
    assert ab.frontiers_of(ab.parse_args([*minimal, "--frontier", "64"])) == [64]


def test_the_frontier_prompt_is_cut_at_a_true_token_boundary_and_verified(
    bench, monkeypatch
):
    monkeypatch.setenv("FAKE_PLAN", "ok,ok,ok")
    ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "16",
        ]
    )
    prompt = bench["out"] / "prompt-16.txt"
    assert prompt.read_text() == " ".join(f"w{i:02d}" for i in range(1, 17))
    report = read_report(bench)
    assert report["frontier_results"][0]["prompt_tokens"] == 16


def test_a_corpus_that_is_not_prefix_stable_is_refused_not_mislabeled(
    bench, monkeypatch, caplog
):
    """The verify step compares ids, not just counts. A corpus whose cut does
    not re-tokenize to the same ids is refused, never mislabeled."""
    monkeypatch.setenv("FAKE_TOK_EXTRA", "2")
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    assert rc == 2
    assert "prefix-stable" in caplog.text
    assert len(dump_calls(bench)) == 0  # no arm ever ran


def test_a_corpus_too_short_for_the_frontier_is_refused(bench, caplog):
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--frontier",
            "2048",
        ]
    )
    assert rc == 2
    assert "tokenizes to 64 tokens" in caplog.text


def test_a_prompt_file_override_skips_the_cut_but_checks_room(bench, monkeypatch):
    prompt = bench["corpus"].parent / "exact.txt"
    prompt.write_text("alpha beta")
    monkeypatch.setenv("FAKE_PLAN", "ok,ok,ok")
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--prompt-file",
            str(prompt),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    assert rc == 0
    report = read_report(bench)
    assert report["frontier_results"][0]["prompt_tokens"] == 2


def test_a_prompt_file_longer_than_ctx_is_refused(bench, monkeypatch, caplog):
    """ds4 refuses a prompt with no generation room; the instrument says so
    before spending an arm on it. ctx at frontier 8 with gen 4 is 76, so an
    80-token prompt is over the line."""
    prompt = bench["corpus"].parent / "huge.txt"
    prompt.write_text(" ".join(f"w{i:02d}" for i in range(1, 65)))
    monkeypatch.setenv("FAKE_TOK_EXTRA", "16")
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--prompt-file",
            str(prompt),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    assert rc == 2
    assert "generation room" in caplog.text


# --- input validation -------------------------------------------------------


def test_a_missing_cli_binary_is_refused(bench, tmp_path):
    bench["trees"]["tree-b"].joinpath(ab.CLI_NAME).unlink()
    rc = ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
        ]
    )
    assert rc == 2


def test_the_report_records_both_tree_commits(bench, monkeypatch):
    """The fake trees are not git repos, so the commit is recorded as unknown
    rather than left out -- a report that cannot say what ran is #118's gap."""
    monkeypatch.setenv("FAKE_PLAN", "ok,ok,ok")
    ab.main(
        [
            "new",
            str(bench["trees"]["tree-a"]),
            "old",
            str(bench["trees"]["tree-b"]),
            str(bench["gguf"]),
            "--corpus",
            str(bench["corpus"]),
            "--out",
            str(bench["out"]),
            "--no-lock",
            "--gen",
            "4",
            "--frontier",
            "8",
        ]
    )
    report = read_report(bench)
    assert report["a"]["commit"] == "unknown"
    assert report["b"]["commit"] == "unknown"


# --- the parsers, directly --------------------------------------------------


def test_parse_dump_reads_a_complete_file(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(
        json.dumps(
            {
                "source": "ds4",
                "prompt_tokens": 2048,
                "ctx": 2240,
                "top_k": 20,
                "steps": [
                    {
                        "step": 0,
                        "selected": " alpha",
                        "top_logprobs": [
                            {"token": " alpha", "logit": 12.5, "logprob": -0.001}
                        ],
                    }
                ],
            }
        )
    )
    dump = ab.parse_dump(path)
    assert dump["prompt_tokens"] == 2048
    assert dump["ctx"] == 2240
    assert dump["truncated"] is False
    assert dump["steps"][0]["selected"] == " alpha"


def test_parse_dump_recovers_steps_from_a_crash_truncated_file(tmp_path):
    """The writer fprintf()s each step as it goes, so a decode killed before
    fclose() leaves the completed steps parseable even though the JSON never
    closes. An incomplete trailing step is discarded, not guessed at."""
    path = tmp_path / "d.json"
    path.write_text(
        '{\n  "source":"ds4",\n  "prompt_tokens":16384,\n  "ctx":16576,\n'
        '  "top_k":20,\n  "steps":[\n'
        '    {"step":0,"selected":" alpha","top_logprobs":['
        '{"token":" alpha","logit":12.5,"logprob":-0.001}]},\n'
        '    {"step":1,"selected":" beta","top_logprobs":['
        '{"token":" beta","logit":11.5,"logprob":-0.01}]}'
    )
    dump = ab.parse_dump(path)
    assert dump["truncated"] is True
    assert dump["prompt_tokens"] == 16384
    assert [s["selected"] for s in dump["steps"]] == [" alpha", " beta"]


def test_compare_flags_a_tokenizer_disagreement():
    a = {
        "prompt_tokens": 2048,
        "ctx": 2240,
        "steps": [{"step": 0, "selected": " x", "top_logprobs": []}],
    }
    b = {
        "prompt_tokens": 2047,
        "ctx": 2240,
        "steps": [{"step": 0, "selected": " x", "top_logprobs": []}],
    }
    kind, step, detail = ab.first_divergence(a, b)
    assert kind == "prompt_tokens"
    assert step == "prefill"
    assert "tokenizers disagree" in detail


def test_compare_reports_a_length_mismatch_as_a_divergence():
    steps = [{"step": 0, "selected": " x", "top_logprobs": []}]
    a = {"prompt_tokens": 8, "ctx": 24, "steps": steps}
    b = {"prompt_tokens": 8, "ctx": 24, "steps": steps + steps}
    kind, step, _detail = ab.first_divergence(a, b)
    assert kind == "length"
    assert step == 1


def test_compare_is_none_when_two_dumps_agree():
    steps = [
        {
            "step": 0,
            "selected": " x",
            "top_logprobs": [{"token": " x", "logit": 1.5, "logprob": -0.1}],
        }
    ]
    a = {"prompt_tokens": 8, "ctx": 24, "steps": steps}
    b = {"prompt_tokens": 8, "ctx": 24, "steps": [dict(s) for s in steps]}
    assert ab.first_divergence(a, b) is None


def test_the_cli_name_is_a_real_make_target(tmp_path):
    """The binary is `ds4`; ds4_cli.c is the source that builds it.

    The instrument first looked for `ds4-cli`, a name taken from the source
    file. There is no such target -- `make ds4-cli` fails outright -- so the
    tool refused every real tree with "missing or not executable" while these
    tests, which built their fake under the same wrong name, passed. Assert
    against ds4's Makefile so the fake and the real tree cannot disagree again.
    """
    makefile = pathlib.Path.home() / "git" / "ds4-main" / "Makefile"
    if not makefile.exists():
        pytest.skip("no ds4 checkout at ~/git/ds4-main")
    text = makefile.read_text()
    assert f"\n{ab.CLI_NAME}: ds4_cli.o" in text, (
        f"{ab.CLI_NAME} is not the Makefile target built from ds4_cli.o"
    )
