"""scripts/videogen/generate.py: the request per arm, the checks, the run record (#1406, #1407).

No GPU, no network: the HTTP calls, the exporters, ffprobe and the InfluxDB
query are fakes passed in through `generate.Deps`.
"""

from __future__ import annotations

import copy
import datetime
import email
import email.policy
import hashlib
import http.client
import json
import pathlib
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from videogen import generate

UTC = datetime.UTC
T0 = datetime.datetime(2026, 11, 17, 15, 0, 0, tzinfo=UTC)
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\nnot-a-real-frame"
H3 = "MiniMaxAI/MiniMax-H3"
WAN_T2V = "Wan-AI/Wan2.2-T2V-A14B-Diffusers"
WAN_I2V = "Wan-AI/Wan2.2-I2V-A14B-Diffusers"

#: sha256 of Wan2.2's shared_config.sample_neg_prompt (UTF-8, 403 bytes) at
#: https://github.com/Wan-Video/Wan2.2/blob/1ea34ff48f87168174e12956e200b1d908b1c5ff/wan/configs/shared_config.py
WAN_NEG_SHA256 = "ce96e0324e4b54ce4b6e867f669ca520952e1a34cc116543516b1897f0d3c47e"

DOG_A_PROMPT = json.loads(
    (ROOT / "benchmarks" / "videogen" / "prompts" / "v1.json").read_text()
)["clips"][0]["prompt"]

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not on PATH")


def arms() -> dict[str, Any]:
    return generate.load_json(generate.ARMS_FILE)


# ------------------------------------------------------------- the file ----


def test_arms_file_has_four_arms_three_seeds_and_fallbacks() -> None:
    data = arms()
    generate.check_arms(data)
    assert data["seeds"] == [1, 2, 3]
    assert data["clips"] == ["dog-a", "dog-b", "dog-c"]
    assert sorted(data["arms"]) == ["h3-i2v", "h3-t2v", "wan-i2v", "wan-t2v"]
    sizes = {name: arm["fallback"] for name, arm in data["arms"].items()}
    assert sizes["h3-t2v"] == sizes["h3-i2v"] == {"width": 1024, "height": 576}
    assert sizes["wan-t2v"] == sizes["wan-i2v"] == {"width": 832, "height": 480}


def test_the_locked_values() -> None:
    """v1 was locked on 2026-11-17 after the timing probes (#1406). A change
    to these is a new version file, not an edit."""
    data = arms()
    assert data["status"] == "locked"
    size = {
        name: (arm["form"]["width"], arm["form"]["height"])
        for name, arm in data["arms"].items()
    }
    assert size == {
        "h3-t2v": (1280, 704),
        "h3-i2v": (1280, 704),
        "wan-t2v": (832, 480),
        "wan-i2v": (832, 480),
    }
    locked = data["locked"]
    assert [(p["arm"], p["size"]) for p in locked["probes"]] == [
        ("h3-t2v", "1280x704"),
        ("wan-t2v", "1280x720"),
        ("wan-t2v", "832x480"),
    ]
    assert [p["s_per_step"] for p in locked["probes"]] == [46.9, 205.3, 58.3]
    assert "different resolutions" in locked["comparison_note"]


def draft_copy(tmp_path: pathlib.Path) -> pathlib.Path:
    """The arms file with status "draft", for the tests of the draft refusal."""
    data = arms()
    data["status"] = "draft"
    path = tmp_path / "arms-draft.json"
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path


def test_every_size_is_a_multiple_of_32_for_h3_and_16_for_wan() -> None:
    for name, arm in arms()["arms"].items():
        step = 32 if name.startswith("h3") else 16
        for size in (arm["form"], arm["fallback"]):
            assert size["width"] % step == 0 and size["height"] % step == 0
    assert 1024 / 576 == 16 / 9


def test_server_blocks_name_the_hf_models_and_the_032_venv() -> None:
    servers = arms()["servers"]
    assert {k: v["served_model_name"] for k, v in servers.items()} == {
        "h3": H3,
        "wan-t2v": WAN_T2V,
        "wan-i2v": WAN_I2V,
    }
    for block in servers.values():
        assert block["vllm_omni"] == "0.32.0"
        assert block["argv"][0] == "~/venvs/vllm-omni-0.32.0/bin/vllm"
        assert block["env"]["VLLM_OMNI_VIDEO_SYNC_TIMEOUT"] == "7200"
        assert block["scope"]["MemoryMax"] == "115G"
        assert generate.forbidden_flags(block["argv"]) == []
    assert servers["h3"]["port"] == 8040
    assert servers["wan-t2v"]["port"] == servers["wan-i2v"]["port"] == 8041


def test_wan_arms_send_upstream_negative_prompt_and_h3_sends_none() -> None:
    data = arms()["arms"]
    for name in ("wan-t2v", "wan-i2v"):
        neg = data[name]["form"]["negative_prompt"]
        assert hashlib.sha256(neg.encode()).hexdigest() == WAN_NEG_SHA256
    for name in ("h3-t2v", "h3-i2v"):
        assert "negative_prompt" not in data[name]["form"]


def test_wan_settings_follow_upstream() -> None:
    data = arms()["arms"]
    assert data["wan-t2v"]["form"]["flow_shift"] == 12.0
    assert data["wan-t2v"]["form"]["boundary_ratio"] == 0.875
    assert data["wan-i2v"]["form"]["flow_shift"] == 5.0
    assert data["wan-i2v"]["form"]["boundary_ratio"] == 0.9


@pytest.mark.parametrize(
    ("where", "key", "value", "match"),
    [
        ("form", "num_inference_step", 40, "num_inference_step"),
        ("form", "model", "x", "model is set per request"),
        ("extra_params", "durations", 5.0, "durations"),
    ],
)
def test_check_arms_refuses_an_unknown_or_per_request_field(
    where: str, key: str, value: Any, match: str
) -> None:
    data = arms()
    data["arms"]["h3-t2v"][where][key] = value
    with pytest.raises(ValueError, match=match):
        generate.check_arms(data)


@pytest.mark.parametrize(
    "flag", [["--boundary-ratio", "0.9"], ["--boundary-ratio=0.9"]]
)
def test_check_arms_refuses_a_server_boundary_ratio(flag: list[str]) -> None:
    data = arms()
    data["servers"]["wan-i2v"]["argv"] += flag
    with pytest.raises(ValueError, match="boundary"):
        generate.check_arms(data)


def test_check_arms_refuses_a_server_without_its_served_name() -> None:
    data = arms()
    argv = data["servers"]["h3"]["argv"]
    data["servers"]["h3"]["argv"] = argv[: argv.index("--served-model-name")]
    with pytest.raises(ValueError, match="served-model-name"):
        generate.check_arms(data)


# ------------------------------------------------------- the form fields ----


EXPECTED: dict[str, dict[str, str]] = {
    "h3-t2v": {
        "model": H3,
        "width": "1280",
        "height": "704",
        "aspect_ratio": "16:9",
        "fps": "24",
        "num_inference_steps": "50",
        "flow_shift": "12",
        "extra_params": '{"audio_flow_shift":3.0,"duration":5.0,"task":"t2va"}',
    },
    "h3-i2v": {
        "model": H3,
        "width": "1280",
        "height": "704",
        "fps": "24",
        "num_inference_steps": "50",
        "flow_shift": "12",
        "extra_params": '{"audio_flow_shift":3.0,"duration":5.0,"task":"fl2va"}',
    },
    "wan-t2v": {
        "model": WAN_T2V,
        "width": "832",
        "height": "480",
        "num_frames": "81",
        "fps": "16",
        "num_inference_steps": "40",
        "guidance_scale": "4.0",
        "guidance_scale_2": "3.0",
        "boundary_ratio": "0.875",
        "flow_shift": "12.0",
    },
    "wan-i2v": {
        "model": WAN_I2V,
        "width": "832",
        "height": "480",
        "num_frames": "81",
        "fps": "16",
        "num_inference_steps": "40",
        "guidance_scale": "3.5",
        "guidance_scale_2": "3.5",
        "boundary_ratio": "0.9",
        "flow_shift": "5.0",
    },
}


@pytest.mark.parametrize("arm", sorted(EXPECTED))
def test_form_fields_per_arm(arm: str) -> None:
    data = arms()
    cfg = data["arms"][arm]
    model = data["servers"][cfg["server"]]["served_model_name"]
    fields = generate.build_fields(cfg, "a prompt", 2, model)
    fields.pop("negative_prompt", None)
    assert fields == {"prompt": "a prompt", **EXPECTED[arm], "seed": "2"}


def test_fallback_changes_only_the_size() -> None:
    cfg = arms()["arms"]["h3-t2v"]
    primary = generate.build_fields(cfg, "p", 1, H3)
    fallback = generate.build_fields(cfg, "p", 1, H3, fallback=True)
    assert fallback == {**primary, "width": "1024", "height": "576"}


# ------------------------------------------------------------ the fakes ----


def parse_form(body: bytes, content_type: str) -> dict[str, tuple[str | None, bytes]]:
    """name -> (filename, bytes), parsed the way a MIME reader would."""
    msg = email.message_from_bytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + body,
        policy=email.policy.HTTP,
    )
    out: dict[str, tuple[str | None, bytes]] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        out[str(name)] = (part.get_filename(), payload)
    return out


class FakeServer:
    def __init__(
        self,
        reply: generate.Reply | Exception,
        serves: tuple[str, ...] = (H3, WAN_T2V, WAN_I2V),
        delay: float = 0.05,
    ) -> None:
        self.reply = reply
        self.serves = serves
        self.delay = delay
        self.forms: list[dict[str, tuple[str | None, bytes]]] = []
        self.urls: list[str] = []

    def post(
        self, url: str, body: bytes, content_type: str, timeout: float
    ) -> generate.Reply:
        assert timeout == generate.TIMEOUT_S
        self.urls.append(url)
        self.forms.append(parse_form(body, content_type))
        time.sleep(self.delay)  # long enough for the samplers to read
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def get(self, url: str, timeout: float) -> str | None:
        if url.endswith("/version"):
            return '{"version": "0.32.0"}'
        if url.endswith("/v1/models"):
            return json.dumps({"data": [{"id": m} for m in self.serves]})
        return None


def flux_table(points: list[tuple[datetime.datetime, float]]) -> str:
    return json.dumps(
        {
            "columns": ["Time", "Value"],
            "rows": [[t.strftime("%Y-%m-%dT%H:%M:%SZ"), v] for t, v in points],
        }
    )


def clock(*times: datetime.datetime) -> Iterator[datetime.datetime]:
    yield from times


class Gpu:
    """Idle before the request, then working."""

    def __init__(self, before: float = 12.0, during: float = 60.0) -> None:
        self.values = [before]
        self.during = during

    def __call__(self, host: str) -> float:
        return self.values.pop(0) if self.values else self.during


def probe_as_expected(plan: generate.Plan) -> Any:
    def probe(path: pathlib.Path) -> dict[str, Any]:
        assert path.name == "clip.mp4.partial"
        assert path.read_bytes() == MP4
        return {**plan.expect(), "video_codec": "h264"}

    return probe


def deps_for(
    server: FakeServer,
    plan: generate.Plan,
    *,
    points: list[tuple[datetime.datetime, float]] | None = None,
    gpu: Gpu | None = None,
    found: dict[str, Any] | None = None,
) -> generate.Deps:
    end = T0 + datetime.timedelta(seconds=360)
    times = clock(T0, end)
    if points is None:
        points = [
            (T0 + datetime.timedelta(seconds=s), 120.0) for s in range(-5, 371, 5)
        ]
    table = flux_table(points)
    return generate.Deps(
        post=server.post,
        get=server.get,
        gpu=gpu or Gpu(),
        mem=lambda host: {"MemTotal": 119.5, "MemAvailable": 22.0},
        flux=lambda query: table,
        sleep=lambda s: None,
        now=lambda: next(times),
        machine=lambda: "Cortex-X925-128GB-GB10",
        ffprobe=(lambda p: found) if found is not None else probe_as_expected(plan),
        sample_s=0.005,
    )


@pytest.fixture
def first_frame(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    """A frames root with dog-a/first.png, and a refs file that records it."""
    root = tmp_path / "frames"
    (root / "dog-a").mkdir(parents=True)
    (root / "dog-a" / "first.png").write_bytes(PNG)
    refs = tmp_path / "refs.json"
    refs.write_text(
        json.dumps(
            {
                "clips": [
                    {
                        "id": "dog-a",
                        "frames_sha256": {"first.png": hashlib.sha256(PNG).hexdigest()},
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(generate, "REFS_FILE", refs)
    return root


def ok_reply() -> generate.Reply:
    # Starlette sends header names in lower case.
    return generate.Reply(
        200,
        {"x-inference-time-s": "301.500", "x-peak-memory-mb": "98000.000"},
        MP4,
    )


def make(arm: str, frames: pathlib.Path, seed: int = 1) -> generate.Plan:
    return generate.make_plan(arm, "dog-a", seed, frames_root=frames)


def go(
    plan: generate.Plan,
    out: pathlib.Path,
    server: FakeServer,
    **kw: Any,
) -> int:
    deps_kw = {k: kw.pop(k) for k in ("points", "gpu", "found") if k in kw}
    return generate.run_one(
        plan, "http://h:8040", out, deps=deps_for(server, plan, **deps_kw), **kw
    )


def read_run(out: pathlib.Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / "run.json").read_text())
    return data


# ------------------------------------------------------------- requests ----


def test_t2v_sends_no_image_and_names_the_model(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    server = FakeServer(ok_reply())
    assert go(make("h3-t2v", first_frame), tmp_path / "out", server) == 0
    form = server.forms[0]
    assert "input_reference" not in form
    assert form["prompt"] == (None, DOG_A_PROMPT.encode())
    assert {k: v[1].decode() for k, v in form.items() if k != "prompt"} == {
        **EXPECTED["h3-t2v"],
        "seed": "1",
    }
    assert server.urls == ["http://h:8040/v1/videos/sync"]


def test_i2v_attaches_the_first_frame(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    server = FakeServer(ok_reply())
    assert go(make("wan-i2v", first_frame, 3), tmp_path / "out", server) == 0
    assert server.forms[0]["input_reference"] == ("first.png", PNG)
    neg = server.forms[0]["negative_prompt"][1]
    assert hashlib.sha256(neg).hexdigest() == WAN_NEG_SHA256
    run = read_run(tmp_path / "out")
    assert (
        run["request"]["input_reference"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    )


def test_i2v_refuses_a_first_frame_that_is_not_the_recorded_one(
    first_frame: pathlib.Path,
) -> None:
    (first_frame / "dog-a" / "first.png").write_bytes(PNG + b"changed")
    with pytest.raises(generate.Refused, match="refs-v1.json records"):
        make("h3-i2v", first_frame)


def test_run_json_records_what_the_issues_ask_for(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    plan = make("h3-t2v", first_frame, 2)
    out = tmp_path / "out"
    assert go(plan, out, FakeServer(ok_reply())) == 0
    run = read_run(out)
    assert run["status"] == "ok" and run["error"] is None
    assert run["arm"] == "h3-t2v" and run["seed"] == 2 and run["clip"] == "dog-a"
    assert run["model"] == H3 and run["revision"] == "0919fb68"
    assert run["request"]["fields"]["seed"] == "2"
    assert run["request"]["fields"]["model"] == H3
    assert run["start"] == "2026-11-17T10:00:00-0500"
    assert run["end"] == "2026-11-17T10:06:00-0500"
    assert run["wall_s"] > 0
    assert run["energy_wh"] == pytest.approx(12.0)  # 120 W for 6 min
    assert run["outlet"]["entity"] == "dgx_current_consumption"
    assert run["outlet"]["complete"] is True and run["outlet"]["max_gap_s"] == 5.0
    assert run["gpu_power"]["mean_w"] == 60.0 and run["gpu_power"]["samples"] >= 1
    assert run["memory"]["peak_used_gib"] == pytest.approx(97.5)
    assert run["server"] == {
        "served_model": H3,
        "vllm_version": "0.32.0",
        "gpu_w_before": 12.0,
    }
    block = arms()["servers"]["h3"]
    assert run["server_config"] == {
        "name": "h3",
        "served_model_name": H3,
        "vllm_omni": "0.32.0",
        "argv": block["argv"],
        "env": block["env"],
        "scope": block["scope"],
    }
    assert run["server_headers"] == {
        "x-inference-time-s": "301.500",
        "x-peak-memory-mb": "98000.000",
    }
    assert run["node"] == {"role": "head"}
    assert run["ffprobe"]["frames"] == 124 and run["ffprobe"]["audio"] is True
    assert run["expected"] == {
        "frames": 124,
        "width": 1280,
        "height": 704,
        "fps": 24.0,
        "audio": True,
    }
    assert run["http_status"] == 200
    assert run["output"]["sha256"] == hashlib.sha256(MP4).hexdigest()
    assert (out / "clip.mp4").read_bytes() == MP4
    assert not list(out.glob("*.partial"))


def test_the_worker_arm_reads_the_worker_outlet(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    assert go(make("wan-t2v", first_frame), tmp_path / "o", FakeServer(ok_reply())) == 0
    run = read_run(tmp_path / "o")
    assert run["outlet"]["entity"] == "dgx_2_current_consumption"
    assert run["node"] == {"role": "worker"}


def test_a_gap_in_the_outlet_readings_leaves_energy_none(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    points = [
        (T0 + datetime.timedelta(seconds=s), 120.0)
        for s in [*range(-5, 100, 5), *range(160, 371, 5)]
    ]
    out = tmp_path / "o"
    assert (
        go(make("h3-t2v", first_frame), out, FakeServer(ok_reply()), points=points) == 0
    )
    run = read_run(out)
    assert run["energy_wh"] is None
    assert run["outlet"]["complete"] is False
    assert run["outlet"]["max_gap_s"] == 65.0
    assert run["outlet"]["energy_wh"] == pytest.approx(12.0)  # partial value kept


# ------------------------------------------------------------- failures ----


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (generate.Reply(500, {}, b'{"detail": "out of memory"}'), "HTTP 500"),
        (generate.Reply(200, {}, b"<html>not a video</html>"), "not an MP4"),
        (OSError("connection reset"), "connection reset"),
        (http.client.IncompleteRead(b"\x00\x00"), "IncompleteRead"),
    ],
)
def test_a_failure_leaves_no_clip_and_a_run_json_with_the_error(
    tmp_path: pathlib.Path,
    first_frame: pathlib.Path,
    reply: generate.Reply | Exception,
    error: str,
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "clip.mp4").write_bytes(MP4)  # an older clip in the same place
    (out / "run.json").write_text("{}")
    rc = go(make("h3-t2v", first_frame), out, FakeServer(reply), overwrite=True)
    assert rc == generate.FAILED
    assert not (out / "clip.mp4").exists()
    assert not list(out.glob("*.partial"))
    run = read_run(out)
    assert run["status"] == "failed"
    assert error in run["error"]
    assert "output" not in run


def test_a_long_error_is_cut_to_500_bytes(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    reply = generate.Reply(500, {}, ("é" * 2000).encode())
    go(make("h3-t2v", first_frame), tmp_path / "o", FakeServer(reply))
    error = read_run(tmp_path / "o")["error"]
    assert error.startswith("HTTP 500: é")
    assert 498 <= len(error.encode()) <= 500


@pytest.mark.parametrize(
    ("arm", "change", "match"),
    [
        ("h3-t2v", {"frames": 120}, "frames 120 (expected 124)"),
        ("h3-t2v", {"width": 1024}, "width 1024 (expected 1280)"),
        ("h3-t2v", {"fps": 25.0}, "fps 25.0 (expected 24)"),
        ("h3-t2v", {"audio": False}, "no audio stream"),
        ("wan-t2v", {"frames": 80}, "frames 80 (expected 81)"),
    ],
)
def test_a_clip_unlike_the_arm_is_a_failure(
    tmp_path: pathlib.Path,
    first_frame: pathlib.Path,
    arm: str,
    change: dict[str, Any],
    match: str,
) -> None:
    plan = make(arm, first_frame)
    found = {**plan.expect(), **change}
    out = tmp_path / "o"
    assert go(plan, out, FakeServer(ok_reply()), found=found) == generate.FAILED
    assert not (out / "clip.mp4").exists()
    assert not list(out.glob("*.partial"))
    run = read_run(out)
    assert match in run["error"]
    assert run["ffprobe"] == found


def test_wan_needs_no_audio(tmp_path: pathlib.Path, first_frame: pathlib.Path) -> None:
    plan = make("wan-t2v", first_frame)
    found = {**plan.expect(), "audio": False}
    assert go(plan, tmp_path / "o", FakeServer(ok_reply()), found=found) == 0


# ------------------------------------------------------------- refusals ----


def test_a_server_that_serves_another_model_is_refused(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    server = FakeServer(ok_reply(), serves=("/home/x/models/MiniMax-H3/FL2VA",))
    rc = go(make("h3-t2v", first_frame), tmp_path / "o", server)
    assert rc == generate.REFUSED
    assert server.forms == []
    assert not (tmp_path / "o").exists()


def test_an_unreadable_model_list_is_refused(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    server = FakeServer(ok_reply())
    server.get = lambda url, timeout: None  # type: ignore[method-assign]
    assert go(make("h3-t2v", first_frame), tmp_path / "o", server) == generate.REFUSED
    assert server.forms == []


def test_a_busy_gpu_is_refused(
    tmp_path: pathlib.Path,
    first_frame: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = FakeServer(ok_reply())
    rc = go(make("h3-t2v", first_frame), tmp_path / "o", server, gpu=Gpu(before=45.0))
    assert rc == generate.REFUSED
    assert server.forms == []
    assert "45.0 W before the request, above the 20 W idle floor" in caplog.text


def test_the_idle_floor_is_the_heartbeat_gb10_floor() -> None:
    assert generate.IDLE_W == 20.0


def test_an_output_inside_a_repo_is_refused(first_frame: pathlib.Path) -> None:
    out = ROOT / "videogen-test-output-must-not-exist"
    server = FakeServer(ok_reply())
    assert go(make("h3-t2v", first_frame), out, server) == generate.REFUSED
    assert not out.exists()
    assert server.forms == []


def test_a_scored_run_from_a_draft_file_is_refused(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    server = FakeServer(ok_reply())
    plan = generate.make_plan(
        "h3-t2v", "dog-a", 1, frames_root=first_frame, arms_file=draft_copy(tmp_path)
    )
    assert plan.arms_status == "draft"
    rc = go(plan, tmp_path / "o", server)
    assert rc == generate.REFUSED
    assert server.forms == []
    assert not (tmp_path / "o").exists()


def test_allow_draft_lets_a_draft_file_run(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    plan = generate.make_plan(
        "h3-t2v", "dog-a", 1, frames_root=first_frame, arms_file=draft_copy(tmp_path)
    )
    rc = go(plan, tmp_path / "o", FakeServer(ok_reply()), allow_draft=True)
    assert rc == 0
    assert read_run(tmp_path / "o")["arms_status"] == "draft"


def test_a_scored_run_from_the_locked_file_needs_no_flag(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    rc = go(make("h3-t2v", first_frame), tmp_path / "o", FakeServer(ok_reply()))
    assert rc == 0
    assert read_run(tmp_path / "o")["arms_status"] == "locked"


def test_a_probe_may_run_from_a_draft_file(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    server = FakeServer(ok_reply())
    plan = generate.make_plan(
        "h3-t2v", "dog-a", 1, frames_root=first_frame, arms_file=draft_copy(tmp_path)
    )
    rc = go(plan, tmp_path / "o", server, probe=True)
    assert rc == 0
    assert read_run(tmp_path / "o")["probe"] is True


def test_an_existing_run_is_not_replaced_without_overwrite(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    out = tmp_path / "o"
    out.mkdir()
    (out / "clip.mp4").write_bytes(b"scored")
    rc = go(make("h3-t2v", first_frame), out, FakeServer(ok_reply()))
    assert rc == generate.REFUSED
    assert (out / "clip.mp4").read_bytes() == b"scored"


def test_plan_reads_server_config_from_the_file(first_frame: pathlib.Path) -> None:
    data = copy.deepcopy(arms())
    plan = make("wan-i2v", first_frame)
    assert plan.server_config()["argv"] == data["servers"]["wan-i2v"]["argv"]
    assert plan.fields["model"] == WAN_I2V


# --------------------------------------------------------------- ffprobe ----


FFPROBE_JSON = json.dumps(
    {
        "streams": [
            {
                "codec_name": "h264",
                "codec_type": "video",
                "width": 1280,
                "height": 704,
                "avg_frame_rate": "24/1",
                "nb_read_frames": "124",
            },
            {"codec_name": "aac", "codec_type": "audio", "avg_frame_rate": "0/0"},
        ]
    }
)


def test_parse_ffprobe() -> None:
    assert generate.parse_ffprobe(FFPROBE_JSON) == {
        "frames": 124,
        "width": 1280,
        "height": 704,
        "fps": 24.0,
        "audio": True,
        "video_codec": "h264",
    }


@needs_ffmpeg
def test_ffprobe_counts_a_real_clip(tmp_path: pathlib.Path) -> None:
    clip = tmp_path / "c.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-v", "error", "-f", "lavfi"),
            *("-i", "testsrc=size=320x176:rate=16"),
            *("-f", "lavfi", "-i", "anullsrc=r=32000:cl=stereo"),
            *("-frames:v", "21", "-shortest", "-pix_fmt", "yuv420p", str(clip)),
        ],
        check=True,
    )
    found = generate.ffprobe_clip(clip)
    assert (found["frames"], found["width"], found["height"]) == (21, 320, 176)
    assert found["fps"] == 16.0 and found["audio"] is True


# ---------------------------------------------------------------- stats ----


def test_gpu_and_memory_stats() -> None:
    assert generate.gpu_stats([40.0, 60.0, 80.0]) == {
        "mean_w": 60.0,
        "peak_w": 80.0,
        "samples": 3,
    }
    assert generate.gpu_stats([])["mean_w"] is None
    mem: list[dict[str, Any]] = [
        {"MemTotal": 119.5, "MemAvailable": 100.0},
        {"MemTotal": 119.5, "MemAvailable": 21.5},
    ]
    assert generate.mem_stats(mem)["peak_used_gib"] == pytest.approx(98.0)
