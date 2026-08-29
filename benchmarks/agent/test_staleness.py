"""Tests for the version-drift check.

The failure that matters is a false "current": a comparison that cannot parse a
tag and quietly reports no drift is worse than one that reports nothing, because
the operator stops looking. Every unknown must stay visibly unknown.
"""
from __future__ import annotations

import staleness


def test_tags_are_compared_regardless_of_their_decoration():
    """Upstream tags carry prefixes this project's binaries do not print."""
    assert staleness.parse("v0.33.2") == (0, 33, 2)
    assert staleness.parse("rust-v0.150.1") == (0, 150, 1)
    assert staleness.parse("codex-cli 0.148.0") == (0, 148, 0)
    assert staleness.parse("2.1.251 (Claude Code)") == (2, 1, 251)
    assert staleness.parse("Warning: client version is 0.33.1") == (0, 33, 1)


def test_numeric_order_not_string_order():
    """"0.9.0" > "0.10.0" as strings. That would hide a whole release."""
    assert staleness.compare("0.9.0", "0.10.0") == "behind"
    assert staleness.compare("0.148.0", "0.150.1") == "behind"


def test_equal_versions_are_current():
    assert staleness.compare("2.1.251", "2.1.251") == "current"


def test_a_local_build_ahead_of_the_release_is_not_behind():
    """A source build from master outruns the tagged release routinely."""
    assert staleness.compare("0.34.0", "v0.33.2") == "ahead"


def test_an_unparseable_version_is_unknown_never_current():
    """The whole point. Silence must not read as agreement."""
    assert staleness.compare("mystery", "v1.2.3") == "unknown"
    assert staleness.compare("1.2.3", "") == "unknown"
    assert staleness.compare(None, "1.2.3") == "unknown"


def test_versions_of_differing_length_compare_sensibly():
    assert staleness.compare("1.2", "1.2.1") == "behind"
    assert staleness.compare("1.2.0", "1.2") == "current"


def test_only_the_leading_number_run_is_taken():
    """A build hash after the version must not be read as another component."""
    assert staleness.parse("0.148.0-dev.g1234abc") == (0, 148, 0)


def test_a_version_below_a_warning_line_is_still_found():
    """`ollama --version` leads with a connection warning when the daemon is
    down and prints the version underneath. Taking line one reported a healthy
    install as unknown."""
    assert staleness.parse("Warning: could not connect to a running Ollama "
                           "instance\nollama version is 0.33.1") == (0, 33, 1)


def test_a_feature_branch_is_not_reported_as_behind_master():
    """A worktree parked on a PR branch diverges from master by design.

    `~/git/llama.cpp-glm52pr` sits on `glm53-pr27752` because PR #27752 is not
    merged. Comparing it to origin/master reported "9 commits behind" -- which
    is mainline moving on, not the branch going stale, and pulling would have
    destroyed the build every glm53 row depends on. A warning that fires on a
    correct state is the kind nobody reads.
    """
    got = staleness.describe_drift(branch="glm53-pr27752", behind=9,
                                   tracking=None)
    assert got["stale"] is False
    assert "pr branch" in got["note"].lower()


def test_a_branch_tracking_its_own_upstream_is_judged_against_that():
    got = staleness.describe_drift(branch="main", behind=4,
                                   tracking="origin/main")
    assert got["stale"] is True


def test_a_branch_on_master_and_behind_is_stale():
    got = staleness.describe_drift(branch="master", behind=44, tracking=None)
    assert got["stale"] is True


def test_a_branch_on_master_and_current_is_not_stale():
    assert staleness.describe_drift(branch="master", behind=0,
                                    tracking=None)["stale"] is False


def test_a_git_error_string_is_not_mistaken_for_an_upstream_name():
    """`git rev-parse @{u}` prints "fatal: no upstream configured" to stderr,
    and _run falls back to stderr when stdout is empty. Treating that as a
    branch name made every PR branch look like it tracked something."""
    assert staleness.describe_drift("glm53-pr27752", 9, None)["stale"] is False


def test_known_branches_are_not_re_announced():
    """Once a branch is being used, stop shouting about it."""
    import pathlib as _p
    got = staleness.new_remote_branches(_p.Path.home() / "git/ds4",
                                        known={"glm-5.3-flash"})
    assert "glm-5.3-flash" not in got


# --- GitHub notifications -------------------------------------------------

NOTIFS = [
    {"reason": "mention", "unread": True,
     "updated_at": "2026-08-29T19:22:00Z",
     "repository": {"full_name": "antirez/ds4"},
     "subject": {"title": "metal: scale GLM 5.3 memory guard with host RAM",
                 "type": "PullRequest"}},
    {"reason": "comment", "unread": False,
     "updated_at": "2026-08-29T19:22:00Z",
     "repository": {"full_name": "antirez/ds4"},
     "subject": {"title": "GLM-5.3-Flash Metal: prefill fails", "type": "Issue"}},
    {"reason": "ci_activity", "unread": True,
     "updated_at": "2026-08-28T14:01:00Z",
     "repository": {"full_name": "evanwtf/monitor"},
     "subject": {"title": "Release workflow run failed", "type": "CheckSuite"}},
    {"reason": "mention", "unread": True,
     "updated_at": "2026-08-27T10:00:00Z",
     "repository": {"full_name": "evanwtf/some-other-repo"},
     "subject": {"title": "unrelated", "type": "Issue"}},
]


def test_only_repos_this_project_depends_on_are_reported():
    """41 of 41 notifications on this account were CI noise from other repos.

    A check that reports all of them is one nobody reads -- the same failure as
    warning about a healthy server.
    """
    got = staleness.interesting_notifications(NOTIFS, repos={"antirez/ds4"})
    assert {n["repo"] for n in got} == {"antirez/ds4"}


def test_ci_activity_is_dropped():
    got = staleness.interesting_notifications(
        NOTIFS, repos={"antirez/ds4", "evanwtf/monitor"})
    assert all(n["reason"] != "ci_activity" for n in got)


def test_a_mention_outranks_a_comment():
    """A mention is addressed to you; a comment is a thread you follow."""
    got = staleness.interesting_notifications(NOTIFS, repos={"antirez/ds4"})
    assert got[0]["reason"] == "mention"


def test_read_items_are_kept_because_email_marks_them_read():
    """The ds4 mention arrived by email and was already `read` via the API.

    Filtering to unread would have hidden the one notification that mattered.
    """
    got = staleness.interesting_notifications(NOTIFS, repos={"antirez/ds4"})
    assert len(got) == 2
    assert any(not n["unread"] for n in got)


def test_the_subject_type_is_kept_so_a_pr_is_distinguishable():
    got = staleness.interesting_notifications(NOTIFS, repos={"antirez/ds4"})
    assert got[0]["type"] == "PullRequest"


def test_malformed_entries_are_skipped_not_fatal():
    assert staleness.interesting_notifications([{}, {"reason": "mention"}],
                                               repos={"antirez/ds4"}) == []
