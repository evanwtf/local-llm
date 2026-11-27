"""scripts/node_image.py: one serving image, read and compared on both nodes.

#1515 (the daily gate checks a local image on the nodes, not a registry) and
#1516 (a row records the image each node served, and a run refuses a split).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import node_image as ni

_SHA = "6ca1feee4ca275b3c6ddcb3b9fc323b5ffeda1f1"


def _inspect_json(
    image_id: str = "sha256:aaaa",
    layers: list[str] | None = None,
    labels: dict[str, str] | None = None,
) -> str:
    return json.dumps(
        [
            {
                "Id": image_id,
                "RootFS": {"Type": "layers", "Layers": layers or ["sha256:l1"]},
                "Config": {"Labels": labels},
            }
        ]
    )


def test_parse_keeps_only_the_local_llm_labels() -> None:
    got = ni.parse_inspect(
        _inspect_json(
            labels={
                ni.PIN_LABEL: _SHA,
                "org.local-llm.vllm-pr": "vllm-project/vllm#68387",
                "maintainer": "someone",
                "ai.vllm.build.commit": _SHA,
            }
        )
    )
    assert got is not None
    assert got.labels == {
        ni.PIN_LABEL: _SHA,
        "org.local-llm.vllm-pr": "vllm-project/vllm#68387",
    }
    assert got.image_id == "sha256:aaaa" and got.layers == ("sha256:l1",)


@pytest.mark.parametrize("text", ["", "not json", "[]", "[{}]", '[{"Id": "x"}]'])
def test_unreadable_inspect_output_is_none(text: str) -> None:
    assert ni.parse_inspect(text) is None


def test_an_image_without_labels_parses_with_none() -> None:
    got = ni.parse_inspect(_inspect_json(labels=None))
    assert got is not None and got.labels == {}


def test_the_layer_hash_follows_the_layers_not_the_id() -> None:
    a = ni.ImageFacts("sha256:aaaa", ("sha256:l1", "sha256:l2"), {})
    b = ni.ImageFacts("sha256:bbbb", ("sha256:l1", "sha256:l2"), {})
    c = ni.ImageFacts("sha256:aaaa", ("sha256:l2", "sha256:l1"), {})
    assert a.layers_sha256 == b.layers_sha256 != c.layers_sha256
    assert a.record() == {
        "id": "sha256:aaaa",
        "layer_count": 2,
        "layers_sha256": a.layers_sha256,
        "labels": {},
    }


def test_inspect_image_asks_the_worker_over_ssh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, _inspect_json(), "")

    monkeypatch.setattr(ni.subprocess, "run", fake)
    assert ni.inspect_image(1, "img:tag") is not None
    assert calls[0][:4] == ["ssh", "-o", "BatchMode=yes", ni.WORKER_IP]
    assert ni.inspect_image(0, "img:tag") is not None
    assert calls[1][:2] == ["bash", "-c"]


def test_inspect_image_of_an_absent_image_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ni.subprocess,
        "run",
        lambda argv, **_: subprocess.CompletedProcess(argv, 1, "[]", "No such"),
    )
    assert ni.inspect_image(0, "img:tag") is None


def test_a_worker_that_hangs_is_unreachable_not_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1515 review L1: a timeout is not "the image is absent"."""

    def hang(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(ni.subprocess, "run", hang)
    for call in (
        lambda: ni.inspect_image(1, "img:tag"),
        lambda: ni.container_image_id(1, "c"),
        lambda: ni.running_containers(1),
    ):
        with pytest.raises(ni.NodeUnreachable, match="unreachable"):
            call()


def test_ssh_exit_255_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ni.subprocess,
        "run",
        lambda argv, **_: subprocess.CompletedProcess(argv, 255, "", "refused"),
    )
    with pytest.raises(ni.NodeUnreachable, match="ssh exit 255"):
        ni.inspect_image(1, "img:tag")


def test_docker_ps_failing_is_none_and_nothing_running_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1515 review L2: "could not list" and "nothing runs" stay apart."""
    rc = {"value": 3}
    monkeypatch.setattr(
        ni.subprocess,
        "run",
        lambda argv, **_: subprocess.CompletedProcess(argv, rc["value"], "", ""),
    )
    assert ni.running_containers(1) is None
    rc["value"] = 0
    assert ni.running_containers(1) == []


_FAKE_DOCKER = """#!/bin/sh
# ps lists two containers; the second stops before inspect reads it.
if [ "$1" = ps ]; then printf 'aaa\nbbb\n'; exit 0; fi
if [ "$1" = inspect ]; then
  echo '{"Id":"aaa","Config":{"Image":"img","Cmd":["vllm","serve"]}}'
  echo 'Error: No such object: bbb' >&2
  exit 1
fi
exit 2
"""


def test_a_container_that_stops_between_ps_and_inspect_is_skipped(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1515 review L3: the real script, run under bash with a fake docker.
    `docker inspect` exits 1 when one ID has gone, but still prints the
    others; the listing keeps them."""
    docker = tmp_path / "docker"
    docker.write_text(_FAKE_DOCKER)
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    assert ni.running_containers(0) == [("aaa", "img", ["vllm", "serve"])]


@pytest.mark.parametrize(
    ("cmd", "two"),
    [
        (["vllm", "serve", "/m", "--nnodes", "2", "--node-rank", "0"], True),
        (["bash", "-c", "vllm serve /m --nnodes=2"], True),
        (["vllm", "serve", "/m", "--nnodes", "1"], False),
        (["vllm", "serve", "/m"], False),
    ],
)
def test_two_node_comes_from_nnodes(cmd: list[str], two: bool) -> None:
    assert ni.is_two_node(cmd) is two


def _rec(layers: str = "aa", sha: str | None = _SHA) -> dict:
    labels = {} if sha is None else {ni.PIN_LABEL: sha}
    return {
        "id": "sha256:x",
        "layer_count": 2,
        "layers_sha256": layers,
        "labels": labels,
    }


def test_a_matching_two_node_server_passes() -> None:
    assert ni.serving_refusal({"rank0": _rec(), "rank1": _rec()}, "6ca1feee4") is None


def test_a_server_with_other_layers_on_the_worker_refuses() -> None:
    why = ni.serving_refusal({"rank0": _rec("aa"), "rank1": _rec("bb")}, None)
    assert why and "different layers" in why


def test_a_server_whose_worker_could_not_be_read_refuses_with_the_cause() -> None:
    record = {
        "rank0": _rec(),
        "rank1": None,
        "errors": {"rank1": "no rank-1 vLLM container on the worker"},
    }
    why = ni.serving_refusal(record, None)
    assert why and "the worker" in why and "no rank-1 vLLM container" in why


def test_a_server_whose_nodes_carry_other_labels_refuses() -> None:
    """#1516 review L9: same layers, other labels is still two images."""
    other = _rec()
    other["labels"] = {**other["labels"], "org.local-llm.vllm-pr": "x#1"}
    why = ni.serving_refusal({"rank0": _rec(), "rank1": other}, None)
    assert why and "labels" in why


def test_a_server_whose_head_could_not_be_read_refuses() -> None:
    why = ni.serving_refusal({"rank0": None, "rank1": _rec()}, None)
    assert why and "the head" in why


def test_a_label_off_the_pin_refuses_and_names_the_node() -> None:
    record = {"rank0": _rec(), "rank1": _rec(sha="5aea30579" + "0" * 31)}
    why = ni.serving_refusal(record, "6ca1feee4")
    assert why and "the worker" in why and ni.PIN_LABEL in why and "6ca1feee4" in why


def test_no_label_or_no_pin_is_no_claim_to_check() -> None:
    assert ni.serving_refusal({"rank0": _rec(sha=None)}, "6ca1feee4") is None
    assert ni.serving_refusal({"rank0": _rec(sha="ffff")}, None) is None


def test_no_record_is_not_a_refusal_here() -> None:
    assert ni.serving_refusal(None, "6ca1feee4") is None


def test_the_record_keeps_an_unread_rank_as_none() -> None:
    head = ni.ImageFacts("sha256:aaaa", ("sha256:l1",), {})
    assert ni.serving_record(head, None, two_node=True) == {
        "rank0": head.record(),
        "rank1": None,
    }
    assert ni.serving_record(head, None, True, {"rank1": "ssh down"}) == {
        "rank0": head.record(),
        "rank1": None,
        "errors": {"rank1": "ssh down"},
    }
    assert ni.serving_record(head, None, two_node=False) == {"rank0": head.record()}
    assert ni.serving_record(None, None, two_node=False) is None


def test_the_one_layers_rule_names_both_hashes() -> None:
    assert ni.layers_refusal(["a", "b"], ("a", "b")) is None
    why = ni.layers_refusal(["a"], ["b"], "img:tag")
    assert why and why.startswith("img:tag has different layers")


def test_the_local_check_names_an_unreachable_worker() -> None:
    def inspect(rank: int, ref: str) -> ni.ImageFacts | None:
        if rank:
            raise ni.NodeUnreachable(1, "ssh exit 255")
        return ni.ImageFacts("sha256:a", ("l",), {})

    got = ni.local_image_check("img", two_node=True, pins=[], inspect=inspect)
    (why,) = got.refusals
    assert "unreachable" in why and "build or load" not in why
