"""The queue is printed live from the labels, so it cannot drift.

It used to be a committed NEXT.md, which became a 377-line log and then, once
generated (#231), a snapshot that went stale and covered one platform. #463
retired the file. These tests cover the parts that would produce a wrong
queue quietly: which issues are selected, what order they come in, which
platform is asked for, and what the summary does to an issue body.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import make_next as mn


def issue(number, title="t", body="b", labels=("platform:macOS", "P1")):
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": n} for n in labels],
    }


# --- selection and order -----------------------------------------------------


def test_the_labels_are_the_order_and_nothing_else_is():
    """P0 before P1, then by number. There is no second ranking to drift
    from, which was the whole failure mode."""
    got = mn.render(
        [
            issue(300, labels=("platform:macOS", "P1")),
            issue(100, labels=("platform:macOS", "P0")),
            issue(200, labels=("platform:macOS", "P1")),
            issue(50, labels=("platform:macOS", "P0")),
        ]
    )[0]
    order = [
        int(line.split("#")[1].split("]")[0])
        for line in got.splitlines()
        if line[:1].isdigit() and "](" in line
    ]
    assert order == [50, 100, 200, 300]


def test_an_issue_with_no_priority_label_is_left_out():
    """It is invisible to every query the queue runs on; putting it in the
    file would hide that rather than fix it."""
    assert mn.priority(issue(1, labels=("platform:macOS",))) is None
    assert "#1]" not in mn.render([issue(1, labels=("platform:macOS",))])[0]


def test_two_priority_labels_is_a_defect_not_a_tie_to_break():
    """The queue cannot rank it, and picking one silently would make the
    contradiction unreportable."""
    assert mn.priority(issue(1, labels=("platform:macOS", "P0", "P1"))) is None


def test_p2_and_p3_do_not_reach_the_file():
    for label in ("P2", "P3"):
        assert mn.priority(issue(1, labels=("platform:macOS", label))) is None


# --- the summary -------------------------------------------------------------


def test_underscores_survive_the_summary():
    """An earlier version stripped them as markdown emphasis and turned
    `verify_posts.py` into `verifyposts.py`. A summary that invents
    identifiers is worse than no summary."""
    got = mn.summarize("checked with `scripts/verify_posts.py` and DS4_MTP_TIMING")
    assert "verify_posts.py" in got
    assert "DS4_MTP_TIMING" in got


def test_a_leading_provenance_block_is_not_the_summary():
    """Every sweep-sourced issue opens with the same three blocks: who
    verified the post, the quoted post, and a bare link. Half the queue read
    "Verified 2026-09-05: post exists, authored by ..." until this."""
    body = (
        "**Verified** 2026-09-05: post exists, authored by @someone.\n\n"
        "> a quoted post nobody needs in a queue\n> - 25 t/s\n\n"
        "[Post](https://x.com/someone/status/1).\n\n"
        "## Why this one is unusual\n\nSame machine class, same model."
    )
    got = mn.summarize(body)
    assert got.startswith("Why this one is unusual")
    assert "Verified" not in got
    assert "25 t/s" not in got


def test_provenance_further_down_is_kept():
    """A `Verified` line below the opening is a correction on a claim, which
    is exactly what a reader should be shown."""
    body = "The claim is X.\n\n**Verified** 2026-09-08: it was not X."
    assert "it was not X" in mn.summarize(body)


def test_an_issue_that_opens_with_substance_loses_nothing():
    assert mn.summarize("Straight to the point.") == "Straight to the point."


def test_the_summary_is_one_line_whatever_the_body_does():
    """It lands inside a list item. A fenced block or a heading there breaks
    the page for every item below it."""
    body = "# Heading\n\n```python\ncode = 1\n```\n\n| a | b |\n|---|---|\n\n> quoted\n\ntext"
    got = mn.summarize(body)
    assert "\n" not in got
    assert "```" not in got
    assert "code = 1" not in got


def test_the_summary_is_capped_and_says_it_was_cut():
    long_body = " ".join(f"word{i}" for i in range(400))
    got = mn.summarize(long_body, words=100)
    assert got.endswith("…")
    assert len(got.split(" ")) == 101  # 100 words plus the ellipsis


def test_an_empty_body_does_not_raise():
    assert mn.summarize("") == ""
    assert mn.summarize(None) == ""


# --- the size warnings, which are the point ----------------------------------


def test_too_many_p0s_warns_in_the_output_and_not_only_on_stderr():
    """A warning nobody reads is not a warning. The person who needs to
    whittle is reading the queue."""
    issues = [issue(n, labels=("platform:macOS", "P0")) for n in range(mn.MAX_P0 + 1)]
    text, warnings = mn.render(issues)
    assert warnings
    assert "P0 is 'blocks a measurement'" in warnings[0]
    assert "⚠️" in text
    assert str(mn.MAX_P0 + 1) in text


def test_too_many_p1s_warns():
    issues = [issue(n, labels=("platform:macOS", "P1")) for n in range(mn.MAX_P1 + 1)]
    text, warnings = mn.render(issues)
    assert warnings
    assert "Whittle" in warnings[0]
    assert "⚠️" in text


def test_a_queue_inside_the_limits_carries_no_warning():
    issues = [issue(n, labels=("platform:macOS", "P0")) for n in range(mn.MAX_P0)]
    text, warnings = mn.render(issues)
    assert warnings == []
    # The OpenCode banner is a ⚠️ too, so check for the warning's own words.
    assert "Whittle" not in text
    assert "blocks a measurement" not in text


def test_the_header_states_the_counts_so_a_reader_sees_the_shape():
    text, _ = mn.render([issue(1, labels=("platform:macOS", "P0")), issue(2)])
    assert "1 P0, 1 P1" in text


# --- platforms ---------------------------------------------------------------


def test_each_platform_names_its_own_label_and_runbook():
    """The DGX Spark's queue had no rendering at all while the platform was
    hard-coded to macOS (#463)."""
    for platform, label in mn.PLATFORMS.items():
        text, _ = mn.render([issue(1, labels=(label, "P0"))], platform)
        assert label in text
        assert mn.RUNBOOKS[platform] in text


def test_fetch_asks_gh_for_the_chosen_platform_only(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd

        class Out:
            stdout = "[]"

        return Out()

    monkeypatch.setattr(mn.subprocess, "run", fake_run)
    assert mn.fetch("nvidia") == []
    assert "platform:Nvidia" in seen["cmd"]
    assert "platform:macOS" not in seen["cmd"]


def test_the_default_platform_follows_the_host(monkeypatch):
    monkeypatch.setattr(mn.sys, "platform", "darwin")
    assert mn.default_platform() == "macos"
    monkeypatch.setattr(mn.sys, "platform", "linux")
    assert mn.default_platform() == "nvidia"


def test_main_prints_and_writes_nothing(monkeypatch, capsys, tmp_path):
    """No file is written: a committed snapshot is the thing #463 removed."""
    monkeypatch.setattr(
        mn, "fetch", lambda platform: [issue(7, labels=(mn.PLATFORMS[platform], "P1"))]
    )
    monkeypatch.chdir(tmp_path)
    assert mn.main(["--platform", "nvidia"]) == 0
    out = capsys.readouterr().out
    assert "#7]" in out
    assert list(tmp_path.iterdir()) == []


def test_no_committed_next_md_remains():
    assert not (mn.REPO_ROOT / "NEXT.md").exists()


def test_a_vaulted_shell_is_named_but_not_handed_over_as_a_path():
    """The generator quotes issue bodies, and some issues are about a
    retired driver.

    #264 is about a shell that used to sit under the scripts directory. Its
    body names that old path, and rendered verbatim the queue told a reader
    to run a path that no longer resolves -- `tests/test_vault.py` failed CI
    on it, correctly.

    The name has to survive: a summary about a shell that does not name the
    shell says nothing. Only the runnable prefix goes.

    The paths below are assembled rather than written out, so this file does
    not become an offender of the very guard it is here to support.
    """
    pre = "scripts/"
    shell = "route_agent_ab.sh"
    out = mn.summarize(f"{pre}{shell} passes a flag {pre}run.py removed.")
    assert shell in out
    assert pre + shell not in out
    # A live script keeps its path -- this strips the vault, not every prefix.
    assert pre + "run.py" in out


def test_a_vaulted_library_under_lib_is_stripped_too():
    """The retired server library used to live under the scripts/lib path."""
    pre = "scripts/lib/"
    shell = "ds4_server.sh"
    out = mn.summarize(f"eight drivers source {pre}{shell} today")
    assert shell in out
    assert pre + shell not in out


def test_the_vault_list_is_read_from_disk_not_hardcoded():
    """A hardcoded list goes stale the next time a driver is retired."""
    names = mn.vaulted_shells()
    assert names, "the vault is empty; the guard would assert nothing"
    assert all(n.endswith(".sh") for n in names)
