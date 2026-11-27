"""Read and compare a serving image on both DGX Sparks (#1515, #1516).

One helper for three callers: `vllm_two_node.py` refuses a start when the two
nodes hold different layers (#1514), `preflight_daily.py` checks a locally
built image on each node (#1515), and `run.capture_versions` records the image
each node serves and refuses a run on a mismatch (#1516).

The image IDs cannot be compared across the two nodes. The head's overlay2
store reports the config digest as `.Id`, and the worker's containerd store
reports the manifest digest. `RootFS.Layers` (the layer diff IDs) is the same
in both stores, so this module compares the layers.

A local overlay image (`scripts/vllm_pr_overlay.py`) carries the PR head sha
in the label `org.local-llm.vllm-pr-sha`. `vllm.__version__` cannot tell the
overlay from its base nightly, so the label is the only record of the PR
head. When a backend declares `recipe_pin`, the label must start with it.

"Absent" and "unreachable" are different answers. A worker that ssh cannot
reach (exit 255, or a timeout) raises `NodeUnreachable`; an image that docker
does not find is None. The fix differs: start the node or fix the fabric,
against build or load the image.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shlex
import subprocess
from collections.abc import Callable, Iterable, Mapping
from typing import Any

#: The worker's fabric address. The head is this machine (rank 0).
WORKER_IP = "10.0.0.2"
#: Labels this repo writes on an image. Only these reach a row.
LABEL_PREFIX = "org.local-llm."
#: The PR head sha that `vllm_pr_overlay.build_labels` writes.
PIN_LABEL = "org.local-llm.vllm-pr-sha"
#: Tiers that serve one model across both Sparks (`scripts/machines.py`).
TWO_NODE_TIERS = frozenset({"gb10-spark-x2"})
#: ssh's own exit status for a connection failure; the remote command's
#: status is passed through otherwise.
SSH_FAILED = 255
#: The default deadline for one probe on one node.
PROBE_TIMEOUT = 60.0


class NodeUnreachable(Exception):
    """The node did not answer: ssh failed or the probe timed out."""

    def __init__(self, rank: int, detail: str) -> None:
        super().__init__(f"{where(rank)} is unreachable ({detail})")
        self.rank = rank


def node_shell(
    rank: int, script: str, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a shell script on the head (rank 0) or on the worker (rank 1)."""
    argv = (
        ["bash", "-c", script]
        if rank == 0
        else ["ssh", "-o", "BatchMode=yes", WORKER_IP, script]
    )
    return subprocess.run(
        argv, capture_output=True, text=True, check=False, timeout=timeout
    )


def probe(
    rank: int, script: str, timeout: float | None = PROBE_TIMEOUT
) -> subprocess.CompletedProcess[str]:
    """`node_shell`, raising `NodeUnreachable` when the node did not answer."""
    try:
        got = node_shell(rank, script, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise NodeUnreachable(rank, f"no answer in {timeout:g} s") from None
    except OSError as exc:
        raise NodeUnreachable(rank, str(exc)) from None
    if rank == 1 and got.returncode == SSH_FAILED:
        raise NodeUnreachable(rank, f"ssh exit 255: {got.stderr.strip()[:200]}")
    return got


def where(rank: int) -> str:
    """The node's name in a message."""
    return "the head" if rank == 0 else f"the worker ({WORKER_IP})"


@dataclasses.dataclass(frozen=True)
class ImageFacts:
    """One node's view of one image: its ID, layers and local-llm labels."""

    image_id: str
    layers: tuple[str, ...]
    labels: Mapping[str, str]

    @property
    def layers_sha256(self) -> str:
        """A hash of the ordered layer list: equal on both nodes for one image."""
        return hashlib.sha256("\n".join(self.layers).encode()).hexdigest()

    def record(self) -> dict[str, Any]:
        """The row's record for this node (`env.serving_image.rankN`)."""
        return {
            "id": self.image_id,
            "layer_count": len(self.layers),
            "layers_sha256": self.layers_sha256,
            "labels": dict(sorted(self.labels.items())),
        }


def parse_inspect(text: str) -> ImageFacts | None:
    """`ImageFacts` from `docker image inspect` output, or None if unreadable."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, list):
        data = data[0] if len(data) == 1 else None
    if not isinstance(data, dict):
        return None
    image_id = data.get("Id")
    layers = (data.get("RootFS") or {}).get("Layers")
    if not isinstance(image_id, str) or not image_id or not isinstance(layers, list):
        return None
    labels = (data.get("Config") or {}).get("Labels") or {}
    if not isinstance(labels, dict):
        labels = {}
    return ImageFacts(
        image_id=image_id,
        layers=tuple(str(x) for x in layers),
        labels={
            str(k): str(v) for k, v in labels.items() if str(k).startswith(LABEL_PREFIX)
        },
    )


def inspect_image(
    rank: int, ref: str, timeout: float | None = PROBE_TIMEOUT
) -> ImageFacts | None:
    """The image `ref` (a tag or an ID) on one node, or None if absent.

    Raises `NodeUnreachable` when the node does not answer.
    """
    got = probe(rank, f"docker image inspect {shlex.quote(ref)}", timeout)
    return parse_inspect(got.stdout) if got.returncode == 0 else None


def container_image_id(
    rank: int, container: str, timeout: float | None = PROBE_TIMEOUT
) -> str | None:
    """The image ID a container on one node runs, or None if unreadable.

    Raises `NodeUnreachable` when the node does not answer.
    """
    got = probe(
        rank,
        f"docker inspect --format '{{{{.Image}}}}' {shlex.quote(container)}",
        timeout,
    )
    out = got.stdout.strip()
    return out if got.returncode == 0 and out.startswith("sha256:") else None


#: Exit 3: `docker ps` failed. A container can stop between `ps` and
#: `inspect`; inspect then exits 1 but still prints the rest, so its status is
#: ignored and every line it printed is read.
PS_INSPECT = """ids=$(docker ps -q) || exit 3
[ -z "$ids" ] || docker inspect --format '{{json .}}' $ids
true
"""


def running_containers(
    rank: int, timeout: float | None = PROBE_TIMEOUT
) -> list[tuple[str, str, list[str]]] | None:
    """(id, image, command) of each running container on one node.

    None when docker on the node cannot list its containers; an empty list
    when nothing runs. Raises `NodeUnreachable` when the node does not answer.
    """
    got = probe(rank, PS_INSPECT, timeout)
    if got.returncode != 0:
        return None
    found = []
    for line in got.stdout.splitlines():
        try:
            info = json.loads(line)
        except ValueError:
            continue
        if not isinstance(info, dict):
            continue
        cfg = info.get("Config") or {}
        cmd = [*(cfg.get("Entrypoint") or []), *(cfg.get("Cmd") or [])]
        found.append((str(info.get("Id", "")), str(cfg.get("Image", "")), cmd))
    return found


def flag_value(cmd: Iterable[str], flag: str) -> str | None:
    """The value of `flag` in a command, as `--flag v` or `--flag=v`.

    The command is split on whitespace first, because a recipe may wrap
    `vllm serve` in `bash -c "..."`.
    """
    tokens = " ".join(cmd).split()
    for i, tok in enumerate(tokens):
        if tok.startswith(f"{flag}="):
            return tok.split("=", 1)[1]
        if tok == flag and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


def is_two_node(cmd: Iterable[str]) -> bool:
    """True if a vLLM command serves across two nodes (`--nnodes 2`)."""
    return flag_value(cmd, "--nnodes") == "2"


def two_node_backend(spec: Mapping[str, Any]) -> bool:
    """True if a backend's tier spans both Sparks."""
    return str(spec.get("tier") or "") in TWO_NODE_TIERS


def _pin_problem(label: str | None, pin: str | None) -> bool:
    return bool(pin) and label is not None and not label.startswith(str(pin))


def pin_refusal(facts: ImageFacts, pin: str | None, rank: int) -> str | None:
    """None unless the image's PR-sha label contradicts the backend's pin.

    An image without the label (an image this repo did not build with
    `vllm_pr_overlay.py`) passes here; `local_image_check` warns about it.
    """
    label = facts.labels.get(PIN_LABEL)
    if not _pin_problem(label, pin):
        return None
    return (
        f"the image on {where(rank)} has {PIN_LABEL}={label}, which does not "
        f"start with the backend's recipe_pin {pin}; rebuild the image from the "
        "pinned spec, or fix the pin"
    )


def layers_refusal(
    head: Iterable[str], worker: Iterable[str], ref: str = "the image"
) -> str | None:
    """None if both nodes hold the same layer list, else the refusal.

    The one same-layers rule: the launcher, the daily gate and the run all
    call it.
    """
    head, worker = tuple(head), tuple(worker)
    if head == worker:
        return None
    return (
        f"{ref} has different layers on the head and {where(1)} "
        f"(RootFS.Layers sha256 {_layer_hash(head)[:12]} on the head, "
        f"{_layer_hash(worker)[:12]} on the worker), so the two nodes run "
        "different code; copy the head's image to the worker"
    )


def _layer_hash(layers: tuple[str, ...]) -> str:
    return ImageFacts("", layers, {}).layers_sha256


@dataclasses.dataclass
class LocalImageCheck:
    """The daily gate's verdict on one local image: refusals and warnings."""

    refusals: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)


def local_image_check(
    ref: str,
    *,
    two_node: bool,
    pins: Iterable[str],
    inspect: Callable[[int, str], ImageFacts | None] = inspect_image,
) -> LocalImageCheck:
    """Each reason the locally built image `ref` is not fit to serve (#1515).

    A local image has no registry to compare with. Instead it must exist on
    each node it serves from, hold the same layers on both nodes of a
    two-node backend, and carry a PR-sha label that matches every pin a live
    backend declares for it. An image without the label cannot be checked
    against a pin, which is a warning, not a pass.
    """
    pins = sorted(set(pins))
    ranks = (0, 1) if two_node else (0,)
    found: dict[int, ImageFacts] = {}
    out = LocalImageCheck()
    for rank in ranks:
        try:
            facts = inspect(rank, ref)
        except NodeUnreachable as exc:
            out.refusals.append(
                f"local image {ref}: cannot check it, {exc}; start the node or "
                "fix the fabric, then rerun"
            )
            continue
        if facts is None:
            out.refusals.append(
                f"local image {ref}: not on {where(rank)} (docker image inspect "
                "found nothing); build or load it there"
            )
            continue
        found[rank] = facts
        for pin in pins:
            if why := pin_refusal(facts, pin, rank):
                out.refusals.append(f"local image {ref}: {why}")
        if pins and PIN_LABEL not in facts.labels:
            out.warnings.append(
                f"local image {ref} on {where(rank)}: unlabeled ({PIN_LABEL}): "
                f"pin {', '.join(pins)} not checked"
            )
    both = 0 in found and 1 in found
    if both and (why := layers_refusal(found[0].layers, found[1].layers, ref)):
        out.refusals.append(f"local image {why}")
    return out


def serving_record(
    head: ImageFacts | None,
    worker: ImageFacts | None,
    two_node: bool,
    errors: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """`env.serving_image` for a row (#1516), or None when nothing is known.

    A two-node server always carries both ranks. A rank that could not be
    read is None, and `errors[rankN]` says why (ssh down, no rank-1
    container, a failed inspect); `serving_refusal` refuses it. A one-node
    server carries `rank0` only.
    """
    errors = dict(errors or {})
    if not two_node:
        return {"rank0": head.record()} if head is not None else None
    record: dict[str, Any] = {
        "rank0": head.record() if head is not None else None,
        "rank1": worker.record() if worker is not None else None,
    }
    if errors:
        record["errors"] = dict(sorted(errors.items()))
    return record


def serving_refusal(record: Any, pin: str | None) -> str | None:
    """None if the served image passes, else why the run must stop (#1516).

    Refuses a two-node server whose image could not be read on a node, whose
    two nodes hold different layers or different `org.local-llm.*` labels,
    or whose PR-sha label contradicts the backend's `recipe_pin`. No record
    (no container found) is not a refusal here: the caller's unstamped-server
    rule covers that.
    """
    if not isinstance(record, dict):
        return None
    errors = record.get("errors") or {}
    for rank in (0, 1):
        key = f"rank{rank}"
        if key in record and record[key] is None:
            cause = errors.get(key) or "the cause was not recorded"
            return (
                f"cannot read the serving image on {where(rank)} ({cause}), so "
                "the run cannot show that both nodes serve the same code"
            )
    head, worker = record.get("rank0"), record.get("rank1")
    if isinstance(head, dict) and isinstance(worker, dict):
        if head.get("layers_sha256") != worker.get("layers_sha256"):
            return (
                "the head and the worker serve images with different layers "
                f"(RootFS.Layers sha256 {str(head.get('layers_sha256'))[:12]} and "
                f"{str(worker.get('layers_sha256'))[:12]}); copy the head's image "
                "to the worker and restart the server"
            )
        if (head.get("labels") or {}) != (worker.get("labels") or {}):
            return (
                "the head and the worker serve images with different "
                f"{LABEL_PREFIX}* labels ({head.get('labels')} against "
                f"{worker.get('labels')}); copy the head's image to the worker "
                "and restart the server"
            )
    for rank in (0, 1):
        node = record.get(f"rank{rank}")
        if not isinstance(node, dict):
            continue
        label = (node.get("labels") or {}).get(PIN_LABEL)
        if _pin_problem(label, pin):
            return (
                f"the image on {where(rank)} has {PIN_LABEL}={label}, "
                f"which does not start with the backend's recipe_pin {pin}"
            )
    return None
