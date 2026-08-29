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
