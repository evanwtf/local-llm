"""The PR overlay builds only what its spec pins, and refuses anything else."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tomllib
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import vllm_pr_overlay as o

A = b"print('a')\n"
B = b"print('b')\n"


def _spec(**over: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "pr": 68387,
        "upstream": "vllm-project/vllm",
        "head_repo": "someone/vllm",
        "sha": "6ca1feee4ca275b3c6ddcb3b9fc323b5ffeda1f1",
        "base_image": "vllm/vllm-openai:nightly-5aea30579ad672c3c35be43112b7a1f7b293a09e",
        "base_digest": "sha256:" + "d" * 64,
        "files": {
            "vllm/a.py": hashlib.sha256(A).hexdigest(),
            "vllm/sub/b.py": hashlib.sha256(B).hexdigest(),
        },
    }
    spec.update(over)
    return spec


def _write(tmp_path: pathlib.Path, spec: dict[str, Any]) -> pathlib.Path:
    p = tmp_path / "overlay.json"
    p.write_text(json.dumps(spec))
    return p


def test_the_committed_spec_loads_and_names_the_pr_files() -> None:
    spec = o.load_spec(o.DEFAULT_SPEC)
    assert spec["pr"] == 68387
    assert len(spec["files"]) == 5
    assert spec["base_digest"].startswith("sha256:")
    assert (o.DEFAULT_SPEC.parent / "Dockerfile").is_file()


@pytest.mark.parametrize(
    "path",
    [
        "tests/x.py",  # outside the package
        "vllmx/x.py",  # a sibling whose name only starts with vllm
        "vllm/../etc/x.py",  # climbs out of the package
        "vllm/csrc/k.cu",  # compiled code
        "vllm/x.txt",  # not Python
    ],
)
def test_a_path_outside_the_python_package_is_refused(path: str) -> None:
    with pytest.raises(o.OverlayError, match="not a .py file"):
        o.check_path(path)


def test_a_short_sha_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(o.OverlayError, match="40-hex"):
        o.load_spec(_write(tmp_path, _spec(sha="6ca1feee4")))


def test_a_base_without_a_digest_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(o.OverlayError, match="base_digest"):
        o.load_spec(_write(tmp_path, _spec(base_digest="BASE_DIGEST")))


def test_a_file_without_a_sha256_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(o.OverlayError, match="not 64 hex"):
        o.load_spec(_write(tmp_path, _spec(files={"vllm/a.py": None})))


def test_the_tag_names_the_base_the_pr_and_the_sha() -> None:
    assert o.image_tag(_spec()) == (
        "local/vllm-openai:nightly-5aea30579-pr68387-6ca1feee4"
    )


def test_fetch_writes_the_files_and_their_sums(tmp_path: pathlib.Path) -> None:
    by_url = {
        "https://raw.githubusercontent.com/someone/vllm/"
        "6ca1feee4ca275b3c6ddcb3b9fc323b5ffeda1f1/vllm/a.py": A,
        "https://raw.githubusercontent.com/someone/vllm/"
        "6ca1feee4ca275b3c6ddcb3b9fc323b5ffeda1f1/vllm/sub/b.py": B,
    }
    o.fetch(_spec(), tmp_path, get=by_url.__getitem__)
    assert (tmp_path / "vllm/sub/b.py").read_bytes() == B
    sums = (tmp_path / "SHA256SUMS").read_text().splitlines()
    assert sums == [
        f"{hashlib.sha256(A).hexdigest()}  vllm/a.py",
        f"{hashlib.sha256(B).hexdigest()}  vllm/sub/b.py",
    ]


def test_fetch_refuses_a_file_that_moved(tmp_path: pathlib.Path) -> None:
    with pytest.raises(o.OverlayError, match=r"vllm/a.py: sha256"):
        o.fetch(_spec(), tmp_path, get=lambda url: b"changed upstream\n")
    assert not (tmp_path / "SHA256SUMS").exists()


def test_the_base_digest_must_match() -> None:
    spec = _spec()
    o.check_base(spec, ["vllm/vllm-openai@" + spec["base_digest"]])
    with pytest.raises(o.OverlayError, match="base"):
        o.check_base(spec, ["vllm/vllm-openai@sha256:" + "e" * 64])
    with pytest.raises(o.OverlayError, match="base"):
        o.check_base(spec, [])


def test_smoke_accepts_matching_files_and_refuses_a_drift() -> None:
    spec = _spec()
    good = {"version": "x", "imported": "m", "files": dict(spec["files"])}
    out = "noise\nSMOKE " + json.dumps(good) + "\n"
    assert o.parse_smoke(spec, out)["version"] == "x"
    bad = dict(good, files=dict(spec["files"], **{"vllm/a.py": "0" * 64}))
    with pytest.raises(o.OverlayError, match="image vllm/a.py"):
        o.parse_smoke(spec, "SMOKE " + json.dumps(bad))
    with pytest.raises(o.OverlayError, match="no SMOKE line"):
        o.parse_smoke(spec, "Traceback ...")


def test_smoke_argv_runs_python_in_the_image_without_the_server() -> None:
    argv = o.smoke_argv("img", ["m1", "m2"], ["vllm/a.py"])
    assert argv[:8] == [
        "docker", "run", "--rm", "--gpus", "all", "--entrypoint", "python3", "img"
    ]  # fmt: skip
    assert argv[-2:] == ["m1,m2", "vllm/a.py"]


def test_a_name_with_two_dots_inside_the_package_is_legal() -> None:
    o.check_path("vllm/a..b.py")


@pytest.mark.parametrize(
    ("key", "value", "match"),
    [
        ("pr", 0, "positive PR number"),
        ("pr", "68387", "positive PR number"),
        ("pr", True, "positive PR number"),
        ("upstream", "vllm", "owner/name"),
        ("head_repo", "someone/vllm/extra", "owner/name"),
        ("head_ref", "bad ref", "branch name"),
        ("base_image", "vllm/vllm-openai", "repo:tag"),
        ("base_image", "vllm/vllm-openai@sha256:" + "d" * 64, "repo:tag"),
    ],
)
def test_a_malformed_spec_field_is_refused(
    tmp_path: pathlib.Path, key: str, value: object, match: str
) -> None:
    with pytest.raises(o.OverlayError, match=match):
        o.load_spec(_write(tmp_path, _spec(**{key: value})))


def test_a_base_with_no_tag_has_no_image_tag() -> None:
    with pytest.raises(o.OverlayError, match="has no tag"):
        o.image_tag(_spec(base_image="localhost:5000/vllm"))


def test_the_committed_spec_builds_the_backends_image() -> None:
    """L1: the tag the script builds is the one the backend runs."""
    spec = o.load_spec(o.DEFAULT_SPEC)
    backends = tomllib.loads((ROOT / "benchmarks/agent/tasks.toml").read_text())
    users = [
        b for b in backends["backend"].values() if b.get("image") == o.image_tag(spec)
    ]
    assert users, f"no backend runs {o.image_tag(spec)}"
    for b in users:
        assert spec["sha"].startswith(str(b["recipe_pin"]))
        assert b["image_source"] == "local"


def test_the_dockerfile_default_base_is_the_pinned_digest() -> None:
    spec = o.load_spec(o.DEFAULT_SPEC)
    text = (o.DEFAULT_SPEC.parent / "Dockerfile").read_text()
    assert f"ARG BASE={o.base_ref(spec)}\n" in text
    assert o.base_ref(spec) == "vllm/vllm-openai@" + spec["base_digest"]


def test_build_passes_the_base_by_digest_and_replaces_the_base_labels() -> None:
    spec = _spec()
    argv = o.build_argv(spec, "local/x:t", pathlib.Path("/ctx"))
    assert argv[argv.index("--build-arg") + 1] == (
        "BASE=vllm/vllm-openai@sha256:" + "d" * 64
    )
    labels = dict(
        argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "--label"
    )
    assert labels["ai.vllm.image.tag"] == "local/x:t"
    assert labels["ai.vllm.build.commit"] == spec["sha"]
    assert labels["org.opencontainers.image.version"] == "local/x:t"
    assert labels["org.opencontainers.image.revision"] == spec["sha"]
    assert labels["org.opencontainers.image.base.name"] == spec["base_image"]
    assert labels["org.opencontainers.image.base.digest"] == spec["base_digest"]
    assert labels["ai.vllm.build.url"].endswith("/vllm-project/vllm/pull/68387")
    assert argv[-2:] == ["local/x:t", "/ctx"]


def test_smoke_imports_every_overlaid_module() -> None:
    spec = _spec(files={"vllm/a.py": "0" * 64, "vllm/sub/__init__.py": "1" * 64})
    assert o.modules_of(spec) == ["vllm.a", "vllm.sub"]


def test_a_missing_base_is_refused_not_a_traceback() -> None:
    def fake(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "No such image")

    with pytest.raises(o.OverlayError, match="docker pull"):
        o.repo_digests("vllm/vllm-openai:nope", run=fake)


@pytest.mark.parametrize(
    ("out", "match"),
    [("SMOKE {not json", "not JSON"), ('SMOKE {"version": "x"}', "no files map")],
)
def test_a_bad_smoke_line_is_refused(out: str, match: str) -> None:
    with pytest.raises(o.OverlayError, match=match):
        o.parse_smoke(_spec(), out)
