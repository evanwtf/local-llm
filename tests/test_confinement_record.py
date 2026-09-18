"""What a row says about its own isolation. #477

A boolean cannot carry this. The mechanisms do not correspond across the two
machines -- macOS has `sandbox-exec` and nothing cgroup-like, Linux has
namespaces and cgroups and no `sandbox-exec` -- so the row records which
dimensions were enforced and by what. The failure this prevents is pooling a
confined row with an unconfined one on a task where the sandbox is the thing
that moved (`script-transform`, 12/20, #476).
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "benchmarks" / "agent")
)

import run


def test_every_declared_dimension_is_recorded():
    """A dimension missing from the record is a guarantee nobody can check."""
    record = run.confinement_record("sandbox-exec", ["/home/x/bench-solutions"], 24)
    for dimension in run.CONFINEMENT_DIMENSIONS:
        assert dimension in record, dimension


def test_a_confined_trial_names_its_mechanism_and_deny_list():
    record = run.confinement_record(
        "sandbox-exec", ["/home/x/bench-solutions", "/home/x/git/gmail-archive"], 24
    )
    assert record["mechanism"] == "sandbox-exec"
    assert record["paths"] == "deny-list"
    assert record["denied_count"] == 2


def test_an_unconfined_trial_says_so_explicitly():
    """Absence of the field means "an older harness", which is a different
    claim from "this run was unconfined". Both must be distinguishable."""
    record = run.confinement_record("none", [], 24)
    assert record["mechanism"] == "none"
    assert record["paths"] == "none"
    assert record["denied_count"] == 0


def test_tmp_and_network_are_recorded_as_unenforced_today():
    """Both are reachable on both machines right now, and the two #389
    failures were a `/tmp` write nothing refused. Recording "unenforced" is
    what stops a later, stricter row being pooled with this one."""
    record = run.confinement_record("sandbox-exec", ["/x"], 24)
    assert record["tmp"] == "unenforced"
    assert record["network"] == "unenforced"


def test_the_memory_dimension_names_its_enforcer_and_cap():
    """`run.py`'s memcap polls and kills on both platforms (#380). A Linux
    cgroup ceiling on top would read differently, and that is a later step."""
    assert run.confinement_record("none", [], 24)["memory"] == "harness:24GiB"
    assert run.confinement_record("none", [], None)["memory"] == "none"
    assert run.confinement_record("none", [], 0)["memory"] == "none"
