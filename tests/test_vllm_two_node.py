from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import vllm_two_node as v


def _run(rank: int) -> list[str]:
    return v.docker_run(
        rank=rank,
        name="supervl",
        image="vllm/vllm-openai:v0.32.0",
        model="ckpt",
        port=8888,
        vllm_args=["--max-num-seqs", "1"],
    )


def _env(argv: list[str]) -> dict[str, str]:
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]
    return dict(p.split("=", 1) for p in pairs)


def _after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


@pytest.mark.fast
def test_head_serves_the_api_and_the_worker_is_headless() -> None:
    head, worker = _run(0), _run(1)
    assert _after(head, "--node-rank") == "0"
    assert _after(head, "--port") == "8888"
    assert "--headless" not in head
    assert _after(worker, "--node-rank") == "1"
    assert "--headless" in worker
    assert "--port" not in worker


@pytest.mark.fast
def test_both_nodes_get_the_same_vllm_arguments() -> None:
    def serve_args(argv: list[str]) -> list[str]:
        tail = argv[argv.index("serve") :]
        return tail[: tail.index("--node-rank")]

    assert serve_args(_run(0)) == serve_args(_run(1))
    assert serve_args(_run(0))[:4] == ["serve", "/models/ckpt", "--max-num-seqs", "1"]


@pytest.mark.fast
def test_nccl_is_pinned_to_all_four_fabric_paths() -> None:
    env = _env(_run(0))
    assert env["NCCL_SOCKET_IFNAME"].count(",") == 3
    assert env["NCCL_IB_HCA"].count(",") == 3
    assert env["NCCL_IB_GID_INDEX"] == "3"


@pytest.mark.fast
def test_each_node_advertises_its_own_fabric_address() -> None:
    assert _env(_run(0))["VLLM_HOST_IP"] == "10.0.0.1"
    assert _env(_run(1))["VLLM_HOST_IP"] == "10.0.0.2"
    assert _after(_run(1), "--master-addr") == "10.0.0.1"


@pytest.mark.fast
def test_shell_line_leaves_the_models_mount_for_the_remote_shell_to_expand() -> None:
    line = v.shell_line(_run(1))
    assert "-v ~/models:/models:ro" in line
    assert shlex.split(line)[0] == "docker"


def test_extra_env_reaches_both_nodes() -> None:
    for rank in (0, 1):
        argv = v.docker_run(
            rank=rank,
            name="glm",
            image="vllm/vllm-openai:v0.34.0",
            model="ckpt",
            port=8888,
            vllm_args=[],
            extra_env={"VLLM_USE_DEEP_GEMM": "0"},
        )
        assert _env(argv)["VLLM_USE_DEEP_GEMM"] == "0"
        # The variable goes to docker, before the image name.
        assert argv.index("VLLM_USE_DEEP_GEMM=0") < argv.index("--entrypoint")


@pytest.mark.parametrize(
    "key",
    [
        "NCCL_IB_HCA",
        "NCCL_NET",
        "NCCL_IB_TC",
        "UCX_NET_DEVICES",
        "GLOO_DEVICE_TRANSPORT",
        "MASTER_ADDR",
        "TP_SOCKET_IFNAME",
        "NVSHMEM_x",
        "VLLM_HOST_IP",
        "HF_HUB_OFFLINE",
    ],
)
def test_extra_env_cannot_change_the_fabric_setup(key: str) -> None:
    with pytest.raises(ValueError, match="fabric setup"):
        v.docker_run(
            rank=0,
            name="glm",
            image="i",
            model="ckpt",
            port=8888,
            vllm_args=[],
            extra_env={key: "x"},
        )


@pytest.mark.parametrize("key", ["1ABC", "A-B", "A B", "A=B", ""])
def test_extra_env_refuses_a_bad_key_name(key: str) -> None:
    with pytest.raises(ValueError, match="not a valid variable name"):
        v.docker_run(
            rank=1,
            name="glm",
            image="i",
            model="ckpt",
            port=8888,
            vllm_args=[],
            extra_env={key: "x"},
        )


@pytest.mark.parametrize("bad", ["NOEQUALS", "=value"])
def test_parse_env_refuses_a_malformed_pair(bad: str) -> None:
    with pytest.raises(ValueError, match="KEY=VALUE"):
        v.parse_env([bad])


def test_parse_env_keeps_an_equals_sign_in_the_value() -> None:
    assert v.parse_env(["A=b=c", "EMPTY="]) == {"A": "b=c", "EMPTY": ""}


# --- the FlashInfer autotune cache (#1514) ----------------------------------

_KEY_INPUTS: dict[str, object] = {
    "image_id": "sha256:aaaa",
    "model": "ckpt",
    "vllm_args": ["--served-model-name", "hy4-flash-nvfp4", "--max-num-seqs", "4"],
    "extra_env": {},
}


def _key(**over: object) -> str:
    return v.cache_key(**{**_KEY_INPUTS, **over})  # type: ignore[arg-type]


def test_two_images_give_two_cache_keys() -> None:
    assert _key(image_id="sha256:aaaa") != _key(image_id="sha256:bbbb")


def test_the_cache_key_is_stable_and_names_the_served_model() -> None:
    assert _key() == _key()
    assert _key().startswith("hy4-flash-nvfp4-")
    assert re.fullmatch(r"[A-Za-z0-9._-]+", _key())


def test_two_arms_give_two_cache_keys() -> None:
    other_args = ["--served-model-name", "hy4-flash-nvfp4", "--max-num-seqs", "8"]
    assert _key(vllm_args=other_args) != _key()
    assert _key(extra_env={"VLLM_FLASHINFER_AUTOTUNE_SKIP_OPS": "x"}) != _key()
    assert _key(model="other-ckpt") != _key()


def test_a_key_without_a_served_name_uses_the_checkpoint_and_stays_safe() -> None:
    key = _key(vllm_args=[], model="org--Model Name/with$odd")
    assert re.fullmatch(r"[A-Za-z0-9._-]+", key)
    assert key.startswith("org--Model-Name-with-odd-")


def _cache(states: tuple[str, str] = ("new", "found")) -> v.AutotuneCache:
    return v.AutotuneCache(key="m-0123456789abcdef", states=states)


def test_both_nodes_mount_the_cache_writable_and_point_vllm_at_it() -> None:
    for rank in (0, 1):
        argv = v.docker_run(
            rank=rank,
            name="n",
            image="i",
            model="ckpt",
            port=8888,
            vllm_args=[],
            autotune=_cache(),
        )
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        assert f"~/{v.AUTOTUNE_SUBDIR}/m-0123456789abcdef:/flashinfer-autotune" in (
            mounts
        )
        assert (
            _env(argv)["VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR"] == "/flashinfer-autotune"
        )
        labels = dict(
            argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "--label"
        )
        assert labels == {
            "local-llm.autotune-cache.key": "m-0123456789abcdef",
            "local-llm.autotune-cache.rank0": "new",
            "local-llm.autotune-cache.rank1": "found",
        }
        # Never the switch that turns autotune off (#1514).
        assert "--no-enable-flashinfer-autotune" not in argv


def test_without_a_cache_there_is_no_mount_and_the_label_says_off() -> None:
    argv = _run(0)
    assert not any("flashinfer-autotune" in a for a in argv)
    assert "VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR" not in _env(argv)
    assert argv[argv.index("--label") + 1] == "local-llm.autotune-cache=off"


def test_extra_env_cannot_move_the_autotune_cache() -> None:
    for cache in (None, _cache()):
        with pytest.raises(ValueError, match="launcher sets"):
            v.docker_run(
                rank=0,
                name="n",
                image="i",
                model="ckpt",
                port=8888,
                vllm_args=[],
                extra_env={"VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR": "/x"},
                autotune=cache,
            )


def test_shell_line_leaves_the_cache_mount_for_the_remote_shell_to_expand() -> None:
    argv = v.docker_run(
        rank=1,
        name="n",
        image="i",
        model="ckpt",
        port=8888,
        vllm_args=["--speculative-config", '{"a": 1}'],
        autotune=_cache(),
    )
    line = v.shell_line(argv)
    assert f"-v ~/{v.AUTOTUNE_SUBDIR}/m-0123456789abcdef:/flashinfer-autotune " in line
    # Everything else is still quoted.
    assert "'{\"a\": 1}'" in line
    assert shlex.split(line)[: shlex.split(line).index("--entrypoint")].count("-v") == 2


# prepare_cache runs its real shell script on the head (bash -c), with HOME
# pointed at a temporary directory.


def test_a_missing_cache_directory_is_created_and_reads_new(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert v.prepare_cache(0, "k1") == "new"
    assert (tmp_path / v.AUTOTUNE_SUBDIR / "k1").is_dir()


def test_a_cache_with_a_file_reads_found_and_a_partial_write_does_not(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    hashdir = tmp_path / v.AUTOTUNE_SUBDIR / "k1" / "abc123"
    hashdir.mkdir(parents=True)
    # vLLM writes to a dot-prefixed .tmp file, then renames it into place.
    (hashdir / ".autotune_configs.json.x1.tmp").write_text("{}")
    assert v.prepare_cache(0, "k1") == "new"
    (hashdir / "autotune_configs_dp0_rank0.json").write_text("{}")
    assert v.prepare_cache(0, "k1") == "found"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores mode bits")
def test_a_cache_directory_that_is_not_writable_refuses(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / v.AUTOTUNE_SUBDIR / "k1"
    d.mkdir(parents=True)
    d.chmod(0o555)
    try:
        with pytest.raises(SystemExit, match="not writable"):
            v.prepare_cache(0, "k1")
    finally:
        d.chmod(0o755)


def test_a_cache_directory_that_cannot_be_created_refuses(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    # A file where the parent directory should be.
    (tmp_path / ".cache").write_text("not a directory")
    with pytest.raises(SystemExit, match="cannot create"):
        v.prepare_cache(0, "k1")


def test_an_unreachable_worker_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        v.subprocess,
        "run",
        lambda argv, **_: subprocess.CompletedProcess(argv, 255, "", "no route"),
    )
    with pytest.raises(SystemExit, match="rank 1"):
        v.prepare_cache(1, "k1")


_LAYERS = '["sha256:l1","sha256:l2"]'


class _Cluster:
    """A fake `subprocess.run` for both nodes: image, cache and stop probes.

    The head runs `bash -c`, the worker `ssh`. `worker_layers` None means
    the worker lacks the image. `labels` is what `docker inspect` returns
    for a container in `stop` (None: no such container); `exec_ok` and
    `throwaway_ok` decide whether each chown succeeds.
    """

    def __init__(
        self,
        image_id: str = "sha256:aaaa",
        files: int = 0,
        worker_layers: str | None = _LAYERS,
        labels: str | None = "k1|img:tag",
        exec_ok: bool = True,
        throwaway_ok: bool = True,
    ) -> None:
        self.image_id, self.files = image_id, files
        self.worker_layers, self.labels = worker_layers, labels
        self.exec_ok, self.throwaway_ok = exec_ok, throwaway_ok
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        script = argv[-1]
        head = argv[0] == "bash"

        def done(ok: bool, out: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0 if ok else 1, out, "boom")

        if "docker image inspect" in script and "--format" not in script:
            layers = _LAYERS if head else self.worker_layers
            if layers is None:
                return done(False, "[]")
            image = {"Id": "sha256:x", "RootFS": {"Layers": json.loads(layers)}}
            return done(True, json.dumps([image]))
        if "docker image inspect" in script:
            return done(bool(self.image_id), self.image_id)
        if "mkdir -p" in script:
            return done(True, f"{self.files}\n")
        if script.startswith("docker inspect"):
            return done(self.labels is not None, self.labels or "")
        if script.startswith("docker exec"):
            return done(self.exec_ok)
        if "--entrypoint chown" in script:
            return done(self.throwaway_ok)
        return done(True)

    def scripts(self, needle: str) -> list[str]:
        return [c[-1] for c in self.calls if needle in c[-1]]

    def runs(self) -> list[list[str]]:
        return [shlex.split(c[-1]) for c in self.calls if "docker run" in c[-1]]


_START = ["start", "--model-dir", "~/models/ckpt", "--image", "i", "--name", "n"]


def test_start_keeps_the_cache_by_default_on_both_nodes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Cluster(files=3)
    monkeypatch.setattr(v.subprocess, "run", fake)
    assert v.main([*_START, "--", "--served-model-name", "m"]) == 0
    probes = [c for c in fake.calls if "mkdir -p" in c[-1]]
    assert [c[0] for c in probes] == ["bash", "ssh"]  # head, then worker
    worker, head = fake.runs()
    key = v.cache_key(
        image_id="sha256:aaaa",
        model="ckpt",
        vllm_args=["--served-model-name", "m"],
        extra_env={},
    )
    for node in (worker, head):
        assert (
            _env(node)["VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR"] == "/flashinfer-autotune"
        )
        assert f"local-llm.autotune-cache.key={key}" in node
        assert "local-llm.autotune-cache.rank0=found" in node
        assert "local-llm.autotune-cache.rank1=found" in node


def test_start_with_the_cache_off_probes_nothing_and_mounts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Cluster()
    monkeypatch.setattr(v.subprocess, "run", fake)
    assert v.main([*_START, "--no-autotune-cache"]) == 0
    assert len(fake.calls) == 2
    for node in fake.runs():
        assert "local-llm.autotune-cache=off" in node
        assert not any("flashinfer-autotune" in a for a in node)


def test_start_refuses_when_the_head_has_no_such_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Cluster(image_id="")
    monkeypatch.setattr(v.subprocess, "run", fake)
    with pytest.raises(SystemExit, match="no-autotune-cache"):
        v.main(_START)
    assert fake.runs() == []


def test_start_refuses_a_bad_env_before_it_makes_a_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Cluster()
    monkeypatch.setattr(v.subprocess, "run", fake)
    with pytest.raises(ValueError, match="fabric setup"):
        v.main([*_START, "--env", "NCCL_IB_HCA=x"])
    assert fake.calls == []


@pytest.mark.parametrize("cache", [True, False])
def test_start_gives_both_ranks_the_same_extra_env(
    monkeypatch: pytest.MonkeyPatch, cache: bool
) -> None:
    fake = _Cluster()
    monkeypatch.setattr(v.subprocess, "run", fake)
    argv = [*_START, *([] if cache else ["--no-autotune-cache"])]
    argv += ["--env", "VLLM_USE_DEEP_GEMM=0", "--", "--max-num-seqs", "4"]
    assert v.main(argv) == 0

    ssh, head = [c for c in fake.calls if "docker run" in c[-1]]
    assert ssh[:3] == ["ssh", "-o", "BatchMode=yes"]
    assert head[:2] == ["bash", "-c"]
    worker_argv, head_argv = shlex.split(ssh[-1]), shlex.split(head[-1])
    for node in (worker_argv, head_argv):
        assert _env(node)["VLLM_USE_DEEP_GEMM"] == "0"
        assert node[node.index("serve") + 2 : node.index("serve") + 4] == [
            "--max-num-seqs",
            "4",
        ]
        assert ("VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR" in _env(node)) is cache
    assert _after(worker_argv, "--node-rank") == "1"
    assert _after(head_argv, "--node-rank") == "0"


def test_start_refuses_when_the_worker_has_other_image_layers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1514 review M1: the IDs differ across the two image stores, so the
    launcher compares the layers, and a mismatch would re-tune at every start."""
    fake = _Cluster(worker_layers='["sha256:l1","sha256:other"]')
    monkeypatch.setattr(v.subprocess, "run", fake)
    with pytest.raises(SystemExit, match="different layers"):
        v.main(_START)
    assert fake.runs() == [] and fake.scripts("mkdir -p") == []


def test_start_refuses_when_the_worker_lacks_the_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Cluster(worker_layers=None)
    monkeypatch.setattr(v.subprocess, "run", fake)
    with pytest.raises(SystemExit, match="worker has no image"):
        v.main(_START)
    assert fake.runs() == []


def test_matching_layers_pass_although_the_two_ids_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Cluster()
    monkeypatch.setattr(v.subprocess, "run", fake)
    assert v.main(_START) == 0
    inspects = [c for c in fake.calls if "docker image inspect" in c[-1]]
    assert [c[0] for c in inspects if "--format" not in c[-1]] == ["bash", "ssh"]
    # Only the head's ID is read, for the key.
    assert [c[0] for c in fake.calls if "{{.Id}}" in c[-1]] == ["bash"]


@pytest.mark.parametrize(
    "off",
    [
        ["--no-enable-flashinfer-autotune"],
        ["-O0"],
        ["-O", "0"],
    ],
)
def test_serve_args_that_turn_autotune_off_get_no_cache(
    monkeypatch: pytest.MonkeyPatch, off: list[str]
) -> None:
    """#1514 review L2: such a start tunes nothing, so `new` would be false."""
    fake = _Cluster()
    monkeypatch.setattr(v.subprocess, "run", fake)
    assert v.main([*_START, "--", "--max-num-seqs", "4", *off]) == 0
    assert fake.scripts("mkdir -p") == [] and fake.scripts("docker image inspect") == []
    for node in fake.runs():
        assert "local-llm.autotune-cache=autotune-disabled" in node
        assert not any("flashinfer-autotune:" in a for a in node)
        assert "VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR" not in _env(node)


def test_O1_leaves_autotune_on() -> None:
    assert not v.autotune_disabled(["-O1", "--max-num-seqs", "0"])
    assert not v.autotune_disabled(["-O", "3"])


def test_stop_chowns_a_live_container_then_removes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Cluster()
    monkeypatch.setattr(v.subprocess, "run", fake)
    assert v.main(["stop", "--name", "n"]) == 0
    scripts = [c[-1] for c in fake.calls]
    for rank, runner in ((0, "bash"), (1, "ssh")):
        mine = [c[-1] for c in fake.calls if c[0] == runner]
        execs = [s for s in mine if s.startswith("docker exec")]
        assert execs and "chown -R" in execs[0] and "/flashinfer-autotune" in execs[0]
        assert mine.index(execs[0]) < mine.index(f"docker rm -f n-rank{rank}")
    assert not any("--entrypoint chown" in s for s in scripts)


def test_stop_chowns_a_dead_containers_cache_through_a_throwaway(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#1514 review L1: a dead container cannot exec, and the files stayed root's."""
    fake = _Cluster(exec_ok=False)
    monkeypatch.setattr(v.subprocess, "run", fake)
    assert v.main(["stop", "--name", "n"]) == 0
    throwaway = fake.scripts("--entrypoint chown")
    assert len(throwaway) == 2
    line = throwaway[0]
    assert line.startswith("docker run --rm --entrypoint chown -v ")
    assert f'"$HOME/{v.AUTOTUNE_SUBDIR}/k1:/c" img:tag -R' in line
    assert "--user" not in line
    assert "chown in n-rank0 failed (boom)" in caplog.text
    # Each throwaway runs before that node's rm.
    for rank in (0, 1):
        order = [c[-1] for c in fake.calls]
        rm = order.index(f"docker rm -f n-rank{rank}")
        assert any("--entrypoint chown" in s for s in order[:rm])


def test_stop_logs_a_chown_that_fails_both_ways(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _Cluster(exec_ok=False, throwaway_ok=False)
    monkeypatch.setattr(v.subprocess, "run", fake)
    assert v.main(["stop", "--name", "n"]) == 0
    assert "files stay root-owned" in caplog.text
    assert len(fake.scripts("docker rm -f")) == 2


@pytest.mark.parametrize("labels", [None, "|img:tag", "<no value>|img:tag"])
def test_stop_leaves_a_container_without_a_cache_alone(
    monkeypatch: pytest.MonkeyPatch, labels: str | None
) -> None:
    fake = _Cluster(labels=labels)
    monkeypatch.setattr(v.subprocess, "run", fake)
    assert v.main(["stop", "--name", "n"]) == 0
    assert fake.scripts("chown") == []
    assert len(fake.scripts("docker rm -f")) == 2


def test_a_tilde_path_in_the_serve_args_stays_quoted() -> None:
    """#1514 review L6: only the two mounts are left for the shell to expand."""
    argv = v.docker_run(
        rank=1,
        name="n",
        image="i",
        model="ckpt",
        port=8888,
        vllm_args=["--chat-template", "~/x/t.jinja", "-v", "~/evil:/e"],
        autotune=_cache(),
    )
    line = v.shell_line(argv)
    assert "'~/x/t.jinja'" in line
    assert "'~/evil:/e'" in line
    assert " -v ~/models:/models:ro " in line


def test_start_refuses_an_unreachable_worker_as_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1516 review L1: ssh exit 255 is not "the worker has no image"."""
    fake = _Cluster()

    def run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if argv[0] == "ssh":
            return subprocess.CompletedProcess(argv, 255, "", "No route to host")
        return fake(argv, **kw)

    monkeypatch.setattr(v.subprocess, "run", run)
    with pytest.raises(SystemExit, match="unreachable"):
        v.main(_START)
    assert fake.runs() == []
