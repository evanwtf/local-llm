"""The label audit finds the issues every queue would silently skip.

`make_next.py` reads one priority and one platform label. These tests pin
each rule the audit states, and the negative cases that must stay quiet:
a family word inside another word, or an engine named only in the body.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import audit_labels as al

GOOD = ("P2", "platform:macOS", "hardware:M5-Max-128GB")


def issue(number=1, title="t", labels=GOOD):
    return {"number": number, "title": title, "labels": [{"name": n} for n in labels]}


def test_a_well_labelled_issue_has_no_findings():
    assert al.findings(issue()) == []


def test_no_priority_is_a_finding():
    got = al.findings(issue(labels=("platform:macOS", "hardware:M5-Max-128GB")))
    assert got == ["priority: none (invisible to make_next)"]


def test_two_priorities_is_a_finding():
    got = al.findings(issue(labels=(*GOOD, "P1")))
    assert got == ["priority: 2 labels (P1, P2); keep one"]


def test_no_platform_and_no_type_is_a_finding():
    assert al.findings(issue(labels=("P2",))) == ["platform: none, and no type label"]


def test_harness_work_with_a_type_label_needs_no_platform():
    """The issue-sweep skill's rule: code that runs anywhere gets a type."""
    assert al.findings(issue(labels=("P2", "bug"))) == []


def test_both_platforms_need_no_machine_label():
    assert al.findings(issue(labels=("P2", "platform:macOS", "platform:Nvidia"))) == []


def test_hardware_must_agree_with_the_platform():
    got = al.findings(
        issue(
            labels=(
                "P2",
                "platform:macOS",
                "hardware:M5-Max-128GB",
                "hardware:Cortex-X925-GB10-x2",
            )
        )
    )
    assert got == ["hardware: hardware:Cortex-X925-GB10-x2 without platform:Nvidia"]


def test_macos_implies_the_m5_max():
    got = al.findings(issue(labels=("P2", "platform:macOS")))
    assert got == ["hardware: platform:macOS without hardware:M5-Max-128GB"]


def test_nvidia_alone_is_not_a_finding():
    """Two machines are Nvidia, so the platform does not imply one."""
    assert al.findings(issue(labels=("P2", "platform:Nvidia"))) == []


def test_the_retired_spark_label_is_a_finding():
    got = al.findings(
        issue(labels=("P2", "platform:Nvidia", "hardware:Cortex-X925-GB10"))
    )
    want = "retired: hardware:Cortex-X925-GB10 on open work; use hardware:Cortex-X925-GB10-x2"
    assert got == [want]


def test_the_retired_label_is_a_finding_even_beside_x2():
    """Every DGX test runs on the cluster from 2026-09-30 (operator), so the
    old label has no job on open work, not even as a pointer to its ledger."""
    labels = (
        "P2",
        "platform:Nvidia",
        "hardware:Cortex-X925-GB10",
        "hardware:Cortex-X925-GB10-x2",
    )
    assert len(al.findings(issue(labels=labels))) == 1


def test_a_family_named_in_the_title_needs_its_label():
    got = al.findings(issue(title="Qwen3.8 on llama.cpp"))
    assert got == [
        "family: title names it, missing model:qwen",
        "family: title names it, missing engine:llamacpp",
    ]


def test_llama_cpp_is_not_the_llama_model_family():
    got = al.findings(issue(title="llama.cpp build", labels=(*GOOD, "engine:llamacpp")))
    assert got == []


def test_the_llama_model_family_is_found():
    assert (
        al.findings(issue(title="Llama 4 Scout", labels=(*GOOD, "model:llama"))) == []
    )
    assert al.findings(issue(title="Llama-4 Scout")) == [
        "family: title names it, missing model:llama"
    ]


def test_ds4_matches_only_as_a_word():
    assert al.findings(issue(title="ds4 PR", labels=(*GOOD, "engine:dwarfstar"))) == []
    assert al.findings(issue(title="nds4x tool")) == []


def test_audit_keeps_only_issues_with_findings_in_number_order():
    got = al.audit([issue(3, labels=("P2",)), issue(1), issue(2, labels=())])
    assert list(got) == [2, 3]


def test_a_quoted_label_name_is_not_a_family():
    """#465 is about the taxonomy; its title quotes labels, not models."""
    got = al.findings(issue(title="Family labels (model:qwen, engine:vllm, ...)"))
    assert got == []
