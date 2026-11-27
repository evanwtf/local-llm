"""The server facts decide which ledger a remote row joins. #562, #647

Nothing in a row can be repaired once it lands in the wrong ledger: the
hardware facts describe the head node whether one Spark served or two. So the
facts refuse a backend whose tier does not belong to the machine they name.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest
import server_facts

SINGLE = "Cortex-X925-128GB-GB10"
ARGV = "vllm serve /models/m --port 8888 --max-num-seqs 8"
CLUSTER = "Cortex-X925-128GB-GB10-x2"

TASKS = """\
base_commit = "abc1234"

[backend.pair]
tier = "gb10-spark-x2"
topology = "remote"
engine = "ds4"
model = "m"
base_url = "http://127.0.0.1:8888"

[backend.tf]
tier = "gb10-spark-x2"
topology = "remote"
engine = "tensorfold"
model = "m"
base_url = "http://127.0.0.1:8888/v1"

[backend.lcpp]
tier = "gb10-spark-x2"
topology = "remote"
engine = "llama.cpp"
model = "m"
base_url = "http://127.0.0.1:8020/v1"

[backend.atlas]
tier = "gb10-spark-x2"
topology = "remote"
engine = "atlas"
model = "m"
base_url = "http://127.0.0.1:8888"

[backend.newengine]
tier = "gb10-spark-x2"
topology = "remote"
engine = "newengine"
model = "m"
base_url = "http://127.0.0.1:8888"

[backend.single]
tier = "gb10-spark"
topology = "remote"
engine = "ds4"
model = "m"
base_url = "http://127.0.0.1:8888"
"""


@pytest.fixture
def tasks(tmp_path, monkeypatch) -> pathlib.Path:
    """A tasks file, and a head node that probes as one Spark."""
    import run

    monkeypatch.setattr(
        server_facts.hardware_id, "facts_for_this_machine", lambda: ({}, "linux")
    )
    monkeypatch.setattr(server_facts.hardware_id, "directory_name", lambda f, p: SINGLE)
    monkeypatch.setattr(server_facts.cluster_id, "verify_and_name", lambda p: CLUSTER)
    monkeypatch.setattr(server_facts.cluster_id, "peer_lan_name", lambda p: "w.lan")
    monkeypatch.setattr(server_facts.cluster_id, "peer_facts", lambda p: {})
    monkeypatch.setattr(server_facts, "peer_container_settings", lambda *a: {})
    monkeypatch.setattr(server_facts.preflight, "machine_facts", dict)
    monkeypatch.setattr(run, "capture_versions", lambda *a, **k: {"server_argv": ARGV})
    path = tmp_path / "tasks.toml"
    path.write_text(TASKS)
    return path


def test_a_cluster_backend_without_a_peer_refuses(tasks):
    """The trigger: a two-node backend, facts taken without --cluster-peer.
    The rows would land in the single-Spark ledger and read as one node."""
    with pytest.raises(SystemExit) as exc:
        server_facts.collect("pair", tasks)
    assert "--cluster-peer" in str(exc.value)
    assert CLUSTER in str(exc.value)


def test_a_single_node_backend_with_a_peer_refuses(tasks):
    """The other direction: a one-node run must not join the cluster ledger."""
    with pytest.raises(SystemExit) as exc:
        server_facts.collect("single", tasks, cluster_peer="peer")
    assert SINGLE in str(exc.value)


def test_each_backend_on_its_own_machine_passes(tasks):
    assert server_facts.collect("pair", tasks, cluster_peer="peer")["directory"] == (
        CLUSTER
    )
    assert server_facts.collect("single", tasks)["directory"] == SINGLE


def test_the_facts_record_the_server_instance(tasks, monkeypatch):
    """#948: the client compares this marker with the live one before a batch."""
    import remote

    monkeypatch.setattr(
        remote, "_fetch", lambda url, timeout, token=None: '{"requests_total": 7}'
    )
    got = server_facts.collect("tf", tasks, cluster_peer="peer")["instance"]
    assert got == {"engine": "tensorfold", "kind": "counter", "value": 7}


def test_the_facts_refuse_a_server_that_does_not_answer_its_marker(tasks, monkeypatch):
    import remote

    def down(url, timeout, token=None):
        raise OSError("connection refused")

    monkeypatch.setattr(remote, "_fetch", down)
    with pytest.raises(SystemExit, match="start the server first"):
        server_facts.collect("tf", tasks, cluster_peer="peer")


def test_a_llamacpp_server_without_metrics_is_told_the_flag(tasks, monkeypatch):
    """#948: llama.cpp answers /metrics only when started with --metrics."""
    import remote

    def no_metrics(url, timeout, token=None):
        raise OSError("HTTP Error 501: Not Implemented")

    monkeypatch.setattr(remote, "_fetch", no_metrics)
    with pytest.raises(SystemExit, match="--metrics"):
        server_facts.collect("lcpp", tasks, cluster_peer="peer")


def test_the_facts_name_the_worker_for_the_client(tasks):
    """#680 finding 9: the client's memory gates read the worker by this name."""
    assert server_facts.collect("pair", tasks, cluster_peer="peer")["peer_host"] == (
        "w.lan"
    )
    assert server_facts.collect("single", tasks)["peer_host"] is None


def test_an_engine_without_a_marker_records_why(tasks):
    """#948: ds4 publishes no marker. The facts say so, and every row carries
    the reason, so a row never lacks the check silently."""
    data = server_facts.collect("pair", tasks, cluster_peer="peer")
    assert data["instance"]["engine"] == "ds4"
    assert data["instance"]["kind"] == "none"
    assert data["instance"]["value"] is None
    assert data["env"]["instance_unchecked"] == data["instance"]["reason"]


def test_an_unclassified_engine_refuses(tasks):
    with pytest.raises(SystemExit, match="NO_INSTANCE_MARKER"):
        server_facts.collect("newengine", tasks, cluster_peer="peer")


def test_cluster_facts_describe_every_node(tasks, monkeypatch):
    """#680 finding 3: memory_gib stays per node, so rows compare across
    batches; env.cluster says how many nodes and what the worker has."""
    monkeypatch.setattr(
        server_facts.cluster_id,
        "peer_facts",
        lambda p: {"cpu_count": 20, "memory_gib": 121.7},
    )
    monkeypatch.setattr(
        server_facts,
        "peer_container_settings",
        lambda peer, env, spec: {"server_cap_add": f"{peer}:{spec['model']}"},
    )
    got = server_facts.collect("pair", tasks, cluster_peer="peer")["env"]["cluster"]
    # #680 finding 2: the worker's container settings sit with its facts.
    assert got == {
        "nodes": 2,
        "peer": {
            "cpu_count": 20,
            "memory_gib": 121.7,
            "server_cap_add": "peer:m",
        },
    }
    assert "cluster" not in server_facts.collect("single", tasks)["env"]


# #213: a row records what the engine did, never what it was told, unless the
# facts carry the launch argv. A remote row's argv comes only from these facts.


@pytest.fixture
def no_argv(monkeypatch):
    import run

    monkeypatch.setattr(run, "capture_versions", lambda *a, **k: {})


def _quiet_marker(monkeypatch):
    import remote

    monkeypatch.setattr(
        remote, "_fetch", lambda url, timeout, token=None: '{"requests_total": 7}'
    )


def test_the_facts_carry_the_launch_argv(tasks, monkeypatch):
    _quiet_marker(monkeypatch)
    env = server_facts.collect("tf", tasks, cluster_peer="peer")["env"]
    assert env["server_argv"] == ARGV
    assert "server_argv_missing" not in env


def test_an_engine_with_an_argv_reader_refuses_facts_without_argv(
    tasks, monkeypatch, no_argv
):
    _quiet_marker(monkeypatch)
    with pytest.raises(SystemExit, match="server_argv") as exc:
        server_facts.collect("tf", tasks, cluster_peer="peer")
    assert "--no-argv-reason" in str(exc.value)


def test_a_stated_reason_lets_facts_without_argv_through_and_is_recorded(
    tasks, monkeypatch, no_argv
):
    _quiet_marker(monkeypatch)
    env = server_facts.collect(
        "tf", tasks, cluster_peer="peer", no_argv_reason="server started by hand"
    )["env"]
    assert "server_argv" not in env
    assert env["server_argv_missing"] == "server started by hand"


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_blank_reason_is_no_reason(tasks, monkeypatch, no_argv, reason):
    _quiet_marker(monkeypatch)
    with pytest.raises(SystemExit, match="server_argv"):
        server_facts.collect("tf", tasks, cluster_peer="peer", no_argv_reason=reason)


def test_an_engine_without_an_argv_reader_records_why(tasks, monkeypatch, no_argv):
    import remote

    monkeypatch.setattr(
        remote, "_fetch", lambda url, timeout, token=None: "atlas_requests_total 3\n"
    )
    env = server_facts.collect("atlas", tasks, cluster_peer="peer")["env"]
    assert env["server_argv_missing"] == (
        "no launch-argv reader for engine 'atlas' (#213)"
    )


def test_the_engines_with_an_argv_reader():
    """run.capture_versions reads a launch argv for each of these (#213)."""
    assert server_facts.ARGV_ENGINES == {
        "vllm",
        "tensorfold",
        "sglang",
        "llama.cpp",
        "tandemllm",
        "ds4",
    }


def test_the_inkling2_backend_passes_the_currency_gate_at_its_pin(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1435: a remote backend with no recipe_dir or image refuses; the
    inkling2-exl3 checkout is its recipe, pinned to the approved revision."""
    import tomllib

    import currency

    tasks = pathlib.Path(server_facts.__file__).resolve().parents[1] / (
        "benchmarks/agent/tasks.toml"
    )
    name = "inkling2sexl3255evanguodual2xrc"
    spec = tomllib.loads(tasks.read_text())["backend"][name]
    assert spec["recipe_pin"] == "32419261"
    assert spec["recipe_pin_reason"]
    (tmp_path / ".git").mkdir()
    spec = {**spec, "recipe_dir": str(tmp_path)}
    answers = {
        "fetch --quiet origin": "",
        "rev-parse --short HEAD": "32419261",
        "rev-parse --abbrev-ref origin/HEAD": "origin/spark",
        "rev-parse --short origin/spark": "32419261",
        "rev-list --count HEAD..origin/spark": "0",
        "tag --merged": "",
    }

    def run(argv: list[str], timeout: int = 30) -> str | None:
        joined = " ".join(argv)
        return next((v for k, v in answers.items() if k in joined), None)

    monkeypatch.setattr(currency, "STAMP", tmp_path / "stamp.json")
    assert server_facts.check_current(name, spec, run) is None
    answers["rev-parse --short HEAD"] = "1234567"
    assert server_facts.check_current(name, spec, run) is not None


def test_the_image_check_reason_reaches_capture_versions(tasks, monkeypatch):
    """#1516 review M1: server_facts.py takes --no-image-check-reason like
    --no-argv-reason, and hands it to the check in capture_versions."""
    import run

    seen: dict = {}

    def capture(*a, **k):
        seen.update(k)
        return {"server_argv": ARGV}

    monkeypatch.setattr(run, "capture_versions", capture)
    server_facts.collect(
        "pair", tasks, cluster_peer="peer", no_image_check_reason="rebuilding"
    )
    assert seen["no_image_check_reason"] == "rebuilding"
    server_facts.collect("pair", tasks, cluster_peer="peer")
    assert seen["no_image_check_reason"] is None


# #680 finding 2, worker half: a treatment can live in the worker's container
# env or CapAdd. The peer's container is read over ssh, picked by the image the
# head serves, and filtered by the head's own function.


VLLM_TAG = "local/vllm-openai:inkling2-c32251b5d"
TF_TAG = "local/tensorfold-dsv41:zig"
KINDLING_TAG = "kindling/glm53:local"
_VLLM_SERVE = ["vllm", "serve", "/models/m", "--served-model-name", "m"]
_VLLM_RANK = ["--nnodes", "2", "--node-rank"]


def _info(
    cid: str,
    image: str,
    cmd: list[str] | None = None,
    env: list[str] | None = None,
    *,
    entrypoint: list[str] | None = None,
    labels: dict[str, str] | None = None,
    caps: list[str] | None = None,
    image_id: str = "",
) -> dict:
    return {
        "Id": cid + "0" * 52,
        "Image": image_id or f"sha256:{image}",
        "Config": {
            "Image": image,
            "Entrypoint": entrypoint,
            "Cmd": cmd,
            "Env": env or ["VLLM_A=1"],
            "Labels": labels,
        },
        "HostConfig": {"CapAdd": caps},
    }


def _vllm_worker(cid: str = "w1", rank: str = "1", **kw) -> dict:
    return _info(cid, VLLM_TAG, [*_VLLM_SERVE, *_VLLM_RANK, rank, "--headless"], **kw)


_EXPORTER = _info("ne", "prom/node-exporter:latest", ["/bin/node_exporter"])
_SPEC = {"model": "m", "engine": "vllm"}
_HEAD = {"vllm_image": VLLM_TAG, "server_argv": " ".join([*_VLLM_SERVE, "0"])}


def _listing(*infos: dict) -> str:
    return "\n".join(json.dumps(i) for i in infos) + "\n"


def _fake_ssh(monkeypatch, answer):
    seen: list[tuple[str, str]] = []

    def ssh(peer: str, command: str) -> str:
        seen.append((peer, command))
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(server_facts.cluster_id, "ssh", ssh)
    return seen


def _peer(monkeypatch, *infos, env=None, spec=None):
    _fake_ssh(monkeypatch, _listing(*infos))
    return server_facts.peer_container_settings(
        "spark-b", _HEAD if env is None else env, spec or _SPEC
    )


_WORKER_ENV = [
    "PATH=/usr/bin",
    "VLLM_HOST_IP=10.0.0.2",
    "NCCL_SOCKET_IFNAME=enp1s0f1np1",
    "HF_TOKEN=hf_secret",
    "VLLM_API_KEY=local-key",
    "NCCL_IB_PASSWORD=pw",
    "UCX_CREDENTIAL_FILE=/c",
    "VLLM_SECRET_SAUCE=s",
    "VLLM_NVFP4_GEMM_BACKEND=marlin",
]


def test_peer_settings_filter_the_workers_env_like_the_heads(monkeypatch):
    """Secrets are dropped by name and IPv4 addresses masked, as on the head,
    and the chosen container's image is recorded for audit."""
    got = _peer(
        monkeypatch,
        _EXPORTER,
        _vllm_worker(env=_WORKER_ENV, caps=["SYS_NICE", "IPC_LOCK"]),
    )
    assert got == {
        "server_env": {
            "NCCL_SOCKET_IFNAME": "enp1s0f1np1",
            "VLLM_HOST_IP": "<ipv4>",
            "VLLM_NVFP4_GEMM_BACKEND": "marlin",
        },
        "server_cap_add": "IPC_LOCK,SYS_NICE",
        "server_env_image": VLLM_TAG,
    }
    text = json.dumps(got)
    for word in ("TOKEN", "KEY", "SECRET", "PASS", "CREDENTIAL", "10.0.0.2"):
        assert word not in text


def test_peer_settings_use_the_heads_filter_function(monkeypatch):
    """Reuse, not a copy: a change to the head's filter reaches the peer."""
    import run

    monkeypatch.setattr(run, "engine_settings_from", lambda info: {"server_env": {}})
    assert _peer(monkeypatch, _vllm_worker())["server_env"] == {}


def test_the_container_serving_image_read_is_reused(monkeypatch):
    """One rule with run.serving_image: its rank-1 container wins, even over
    another container that also looks like a rank-1 worker."""
    env = {
        **_HEAD,
        "serving_image": {"rank1": {"id": "sha256:x"}, "rank1_container": "w2"},
    }
    got = _peer(
        monkeypatch,
        _vllm_worker("w1", env=["VLLM_A=other"]),
        _vllm_worker("w2", env=["VLLM_A=chosen"]),
        env=env,
    )
    assert got["server_env"] == {"VLLM_A": "chosen"}


def test_a_reused_container_that_has_gone_records_what_was_seen(monkeypatch):
    env = {**_HEAD, "serving_image": {"rank1_container": "w9"}}
    got = _peer(monkeypatch, _vllm_worker("w1"), _EXPORTER, env=env)
    assert got == {
        "server_env_missing": "the rank-1 container that serving_image read "
        "(w9) is not running on the peer (2 containers listed)"
    }


def test_the_worker_image_id_from_serving_image_matches_without_a_tag(monkeypatch):
    env = {"serving_image": {"rank1": {"id": "sha256:wid"}}}
    got = _peer(monkeypatch, _vllm_worker(image_id="sha256:wid"), env=env)
    assert got["server_env_image"] == VLLM_TAG


def test_the_tensorfold_zig_follow_worker_is_found(monkeypatch):
    """The Zig worker runs `tf-dsv41 follow /model /assets`, no entrypoint
    (deepseek-v41-tensorfold-spark scripts/run-zig.sh). The word "tensorfold"
    is not in its command."""
    worker = _info(
        "z1",
        TF_TAG,
        ["tf-dsv41", "follow", "/model", "/assets"],
        ["CUDA_VISIBLE_DEVICES=0"],
        labels={"org.tensorfold.rank": "1", "org.tensorfold.revision": "abc"},
    )
    got = _peer(
        monkeypatch,
        _EXPORTER,
        worker,
        env={"tensorfold_image": TF_TAG},
        spec={"model": "DeepSeek-V4.1-Flash-TF", "engine": "tensorfold"},
    )
    assert got["server_env_image"] == TF_TAG
    assert got["server_env"] == {"CUDA_VISIBLE_DEVICES": "0"}


def test_the_zig_rank0_and_rank1_on_one_peer_pick_the_follower(monkeypatch):
    rank0 = _info(
        "z0",
        TF_TAG,
        ["tensorfold-dsv41", "serve", "/model", "--name", "D"],
        ["CUDA_A=rank0"],
        labels={"org.tensorfold.rank": "0"},
    )
    rank1 = _info(
        "z1", TF_TAG, ["tf-dsv41", "follow", "/model", "/assets"], ["CUDA_A=rank1"]
    )
    got = _peer(
        monkeypatch, rank0, rank1, env={"tensorfold_image": TF_TAG}, spec={"model": "D"}
    )
    assert got["server_env"] == {"CUDA_A": "rank1"}


def test_the_kindling_entrypoint_container_is_found(monkeypatch):
    """glm-5.3-flash-gx10: ENTRYPOINT ["/entrypoint.sh"] and no CMD, so the
    command names neither the engine, the model nor a rank."""
    worker = _info(
        "k1", KINDLING_TAG, None, ["VLLM_A=kindling"], entrypoint=["/entrypoint.sh"]
    )
    got = _peer(
        monkeypatch,
        _EXPORTER,
        worker,
        env={"vllm_image": KINDLING_TAG},
        spec={"model": "glm53"},
    )
    assert got == {
        "server_env": {"VLLM_A": "kindling"},
        "server_env_image": KINDLING_TAG,
    }


def test_a_bench_container_beside_the_rank1_worker_does_not_win(monkeypatch):
    bench = _info("b1", VLLM_TAG, ["vllm", "bench", "serve", "--model", "m"])
    got = _peer(monkeypatch, bench, _vllm_worker(env=["VLLM_A=worker"]))
    assert got["server_env"] == {"VLLM_A": "worker"}


def test_a_lone_bench_container_is_not_the_server(monkeypatch):
    bench = _info("b1", VLLM_TAG, ["vllm", "bench", "serve", "--model", "m"])
    got = _peer(monkeypatch, bench)
    assert got == {
        "server_env_missing": "1 containers on the peer run the head's image, "
        "and each is a client, rank 0, or serves another model (1 containers listed)"
    }


def test_rank0_and_rank1_of_one_model_on_one_peer_pick_rank1(monkeypatch):
    got = _peer(
        monkeypatch,
        _vllm_worker("r0", "0", env=["VLLM_A=r0"]),
        _vllm_worker("r1", "1", env=["VLLM_A=r1"]),
    )
    assert got["server_env"] == {"VLLM_A": "r1"}


def test_a_lone_container_for_another_model_is_refused(monkeypatch):
    """The single-hit case gets the same checks: same image, other model."""
    other = _info(
        "o1",
        VLLM_TAG,
        ["vllm", "serve", "/models/o", "--served-model-name", "other"],
    )
    got = _peer(monkeypatch, other)
    assert (
        "each is a client, rank 0, or serves another model"
        in (got["server_env_missing"])
    )


def test_a_lone_container_on_another_image_is_refused(monkeypatch):
    other = _info("o1", "vllm/vllm-openai:v0.32.0", [*_VLLM_SERVE, *_VLLM_RANK, "1"])
    got = _peer(monkeypatch, other, _EXPORTER)
    assert got == {
        "server_env_missing": "no running container on the peer runs the head's "
        "image (2 containers listed)"
    }


def test_hyperflare_records_that_the_head_has_no_serving_image(monkeypatch):
    """Hyperflare serves outside docker on both nodes. The head recorded no
    serving image either, so there is nothing to match: the reason says so."""
    got = _peer(
        monkeypatch,
        _EXPORTER,
        env={"server_argv": "hyperflare serve x"},
        spec={"model": "glm-5.3-flash-hyperflare", "engine": "hyperflare"},
    )
    assert got == {
        "server_env_missing": "the head's serving image is not recorded, so no "
        "peer container can be matched to it (1 containers listed)"
    }


def test_two_rank1_workers_for_one_model_are_unknown_not_a_guess(monkeypatch):
    got = _peer(monkeypatch, _vllm_worker("a"), _vllm_worker("b"))
    assert got == {
        "server_env_missing": "2 containers on the peer run the head's image and "
        "2 carry a rank-1 marker: cannot tell which serves (2 containers listed)"
    }


def test_a_container_without_recorded_prefixes_records_why(monkeypatch):
    got = _peer(monkeypatch, _vllm_worker(env=["PATH=/x"]))
    assert "server_env" not in got
    assert got["server_env_image"] == VLLM_TAG
    assert got["server_env_missing"] == (
        "the peer's serving container has no variables with a recorded prefix"
    )


# M3: an ssh failure puts a fixed phrase in the row and the full text in the
# log. Driven through the real cluster_id.ssh with a fake subprocess.run.

_LEAKY_STDERR = (
    "ssh: connect to host spark-b.lan (192.0.2.7, fe80::1ce:2ff:fe33:4455) "
    "port 22 as evan@spark-b.lan: /home/evan/.ssh/config line 4: bad"
)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            subprocess.TimeoutExpired(["ssh", "spark-b.lan", "docker ps"], 30),
            "cannot read the peer's containers: no answer in 30 s",
        ),
        (
            subprocess.CompletedProcess([], 255, "", _LEAKY_STDERR),
            "cannot read the peer's containers: ssh failed, exit 255",
        ),
        (
            subprocess.CompletedProcess([], 3, "", _LEAKY_STDERR),
            "cannot read the peer's containers: docker ps failed on the peer, exit 3",
        ),
        (
            subprocess.CompletedProcess([], 1, "", _LEAKY_STDERR),
            (
                "cannot read the peer's containers: the container listing failed "
                "on the peer, exit 1"
            ),
        ),
    ],
)
def test_an_ssh_failure_records_a_fixed_phrase(monkeypatch, caplog, result, expected):
    def fake_run(*a, **k):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(server_facts.cluster_id.subprocess, "run", fake_run)
    with caplog.at_level("WARNING", logger="server_facts"):
        got = server_facts.peer_container_settings("spark-b.lan", _HEAD, _SPEC)
    assert got == {"server_env_missing": expected}
    for leak in ("spark-b", "evan", "fe80", "192.0.2.7", "/home", ".ssh"):
        assert leak not in got["server_env_missing"]
    # The operator still gets the whole text.
    assert "spark-b.lan" in caplog.text
