"""scripts/videogen/generate.py: the request per arm, the run record, failures (#1406, #1407).

No GPU, no network: the HTTP post, the exporters and the InfluxDB query are
fakes passed in through `generate.Deps`.
"""

from __future__ import annotations

import datetime
import email
import email.policy
import hashlib
import json
import pathlib
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

DOG_A_PROMPT = json.loads(
    (ROOT / "benchmarks" / "videogen" / "prompts" / "v1.json").read_text()
)["clips"][0]["prompt"]


# ------------------------------------------------------------- the file ----


def test_arms_file_is_a_draft_with_four_arms_three_seeds_and_fallbacks() -> None:
    arms = generate.load_json(generate.ARMS_FILE)
    generate.check_arms(arms)
    assert arms["status"] == "draft"
    assert arms["seeds"] == [1, 2, 3]
    assert arms["clips"] == ["dog-a", "dog-b", "dog-c"]
    assert sorted(arms["arms"]) == ["h3-i2v", "h3-t2v", "wan-i2v", "wan-t2v"]
    sizes = {name: arm["fallback"] for name, arm in arms["arms"].items()}
    assert sizes["h3-t2v"] == sizes["h3-i2v"] == {"width": 960, "height": 576}
    assert sizes["wan-t2v"] == sizes["wan-i2v"] == {"width": 832, "height": 480}


def test_an_unknown_form_field_is_refused() -> None:
    arms = generate.load_json(generate.ARMS_FILE)
    arms["arms"]["wan-t2v"]["form"]["num_inference_step"] = 40  # a typo
    with pytest.raises(ValueError, match="num_inference_step"):
        generate.check_arms(arms)


# ------------------------------------------------------- the form fields ----


EXPECTED: dict[str, dict[str, str]] = {
    "h3-t2v": {
        "width": "1280",
        "height": "704",
        "aspect_ratio": "16:9",
        "fps": "24",
        "num_inference_steps": "50",
        "flow_shift": "12",
        "extra_params": '{"audio_flow_shift":3.0,"duration":5.0,"task":"t2va"}',
    },
    "h3-i2v": {
        "width": "1280",
        "height": "704",
        "fps": "24",
        "num_inference_steps": "50",
        "flow_shift": "12",
        "extra_params": '{"audio_flow_shift":3.0,"duration":5.0,"task":"fl2va"}',
    },
    "wan-t2v": {
        "width": "1280",
        "height": "720",
        "num_frames": "81",
        "fps": "16",
        "num_inference_steps": "40",
        "guidance_scale": "4.0",
        "guidance_scale_2": "3.0",
        "boundary_ratio": "0.875",
        "flow_shift": "5.0",
    },
    "wan-i2v": {
        "width": "1280",
        "height": "720",
        "num_frames": "81",
        "fps": "16",
        "num_inference_steps": "40",
        "guidance_scale": "3.5",
        "flow_shift": "5.0",
    },
}


@pytest.mark.parametrize("arm", sorted(EXPECTED))
def test_form_fields_per_arm(arm: str) -> None:
    cfg = generate.load_json(generate.ARMS_FILE)["arms"][arm]
    fields = generate.build_fields(cfg, "a prompt", 2)
    assert fields == {"prompt": "a prompt", **EXPECTED[arm], "seed": "2"}


def test_fallback_changes_only_the_size() -> None:
    cfg = generate.load_json(generate.ARMS_FILE)["arms"]["wan-t2v"]
    primary = generate.build_fields(cfg, "p", 1)
    fallback = generate.build_fields(cfg, "p", 1, fallback=True)
    assert fallback == {**primary, "width": "832", "height": "480"}


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
    def __init__(self, reply: generate.Reply | Exception, delay: float = 0.05):
        self.reply = reply
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
            return '{"version": "0.28.0"}'
        if url.endswith("/v1/models"):
            return '{"data": [{"id": "/models/MiniMax-H3/FL2VA"}]}'
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


def deps_for(server: FakeServer, *, outlet_w: float = 120.0) -> generate.Deps:
    end = T0 + datetime.timedelta(seconds=360)
    times = clock(T0, end)
    points = [(T0 + datetime.timedelta(seconds=s), outlet_w) for s in range(-5, 371, 5)]
    return generate.Deps(
        post=server.post,
        get=server.get,
        gpu=lambda host: 60.0,
        mem=lambda host: {"MemTotal": 119.5, "MemAvailable": 22.0},
        flux=lambda query: flux_table(points),
        sleep=lambda s: None,
        now=lambda: next(times),
        node_host=lambda role: f"{role}-node",
        machine=lambda: "Cortex-X925-128GB-GB10",
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
    return generate.Reply(
        200,
        {"X-Inference-Time-S": "301.500", "X-Peak-Memory-MB": "98000.000"},
        MP4,
    )


# ------------------------------------------------------------- requests ----


def test_t2v_sends_no_image(tmp_path: pathlib.Path, first_frame: pathlib.Path) -> None:
    plan = generate.make_plan("h3-t2v", "dog-a", 1, frames_root=first_frame)
    server = FakeServer(ok_reply())
    out = tmp_path / "out"
    rc = generate.run_one(
        plan, "http://h:8040", out, allow_draft=True, deps=deps_for(server)
    )
    assert rc == 0
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
    plan = generate.make_plan("wan-i2v", "dog-a", 3, frames_root=first_frame)
    server = FakeServer(ok_reply())
    rc = generate.run_one(
        plan, "http://w:8041", tmp_path / "out", allow_draft=True, deps=deps_for(server)
    )
    assert rc == 0
    assert server.forms[0]["input_reference"] == ("first.png", PNG)
    run = json.loads((tmp_path / "out" / "run.json").read_text())
    assert (
        run["request"]["input_reference"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    )


def test_i2v_refuses_a_first_frame_that_is_not_the_recorded_one(
    first_frame: pathlib.Path,
) -> None:
    (first_frame / "dog-a" / "first.png").write_bytes(PNG + b"changed")
    with pytest.raises(generate.Refused, match="refs-v1.json records"):
        generate.make_plan("h3-i2v", "dog-a", 1, frames_root=first_frame)


def test_run_json_records_what_the_issues_ask_for(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    plan = generate.make_plan("h3-t2v", "dog-a", 2, frames_root=first_frame)
    out = tmp_path / "out"
    rc = generate.run_one(
        plan,
        "http://h:8040",
        out,
        allow_draft=True,
        deps=deps_for(FakeServer(ok_reply())),
    )
    assert rc == 0
    run = json.loads((out / "run.json").read_text())
    assert run["status"] == "ok" and run["error"] is None
    assert run["arm"] == "h3-t2v" and run["seed"] == 2 and run["clip"] == "dog-a"
    assert run["model"] == "MiniMaxAI/MiniMax-H3" and run["revision"] == "0919fb68"
    assert run["request"]["fields"]["seed"] == "2"
    assert run["start"] == "2026-11-17T10:00:00-0500"
    assert run["end"] == "2026-11-17T10:06:00-0500"
    assert run["wall_s"] > 0
    assert run["energy_wh"] == pytest.approx(12.0)  # 120 W for 6 min
    assert run["outlet"]["entity"] == "dgx_current_consumption"
    assert run["gpu_power"]["mean_w"] == 60.0 and run["gpu_power"]["samples"] >= 1
    assert run["memory"]["peak_used_gib"] == pytest.approx(97.5)
    assert run["server"] == {
        "vllm_version": "0.28.0",
        "served_model": "/models/MiniMax-H3/FL2VA",
    }
    assert run["server_headers"]["X-Inference-Time-S"] == "301.500"
    assert run["node"] == {"role": "head", "host": "head-node"}
    assert run["http_status"] == 200
    assert run["output"]["sha256"] == hashlib.sha256(MP4).hexdigest()
    assert (out / "clip.mp4").read_bytes() == MP4
    assert not list(out.glob("*.partial"))


def test_the_worker_arm_reads_the_worker_outlet(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    plan = generate.make_plan("wan-t2v", "dog-a", 1, frames_root=first_frame)
    generate.run_one(
        plan,
        "http://w:1",
        tmp_path / "o",
        allow_draft=True,
        deps=deps_for(FakeServer(ok_reply())),
    )
    run = json.loads((tmp_path / "o" / "run.json").read_text())
    assert run["outlet"]["entity"] == "dgx_2_current_consumption"


# ------------------------------------------------------------- failures ----


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (generate.Reply(500, {}, b'{"detail": "out of memory"}'), "HTTP 500"),
        (generate.Reply(200, {}, b"<html>not a video</html>"), "not an MP4"),
        (OSError("connection reset"), "connection reset"),
    ],
)
def test_a_failure_leaves_no_clip_and_a_run_json_with_the_error(
    tmp_path: pathlib.Path,
    first_frame: pathlib.Path,
    reply: generate.Reply | Exception,
    error: str,
) -> None:
    plan = generate.make_plan("h3-t2v", "dog-a", 1, frames_root=first_frame)
    out = tmp_path / "out"
    out.mkdir()
    (out / "clip.mp4").write_bytes(MP4)  # an older clip in the same place
    (out / "run.json").write_text("{}")
    rc = generate.run_one(
        plan,
        "http://h:8040",
        out,
        allow_draft=True,
        overwrite=True,
        deps=deps_for(FakeServer(reply)),
    )
    assert rc == generate.FAILED
    assert not (out / "clip.mp4").exists()
    assert not list(out.glob("*.partial"))
    run = json.loads((out / "run.json").read_text())
    assert run["status"] == "failed"
    assert error in run["error"]
    assert "output" not in run


# ------------------------------------------------------------- refusals ----


def test_an_output_inside_a_repo_is_refused(first_frame: pathlib.Path) -> None:
    plan = generate.make_plan("h3-t2v", "dog-a", 1, frames_root=first_frame)
    out = ROOT / "videogen-test-output-must-not-exist"
    server = FakeServer(ok_reply())
    rc = generate.run_one(
        plan, "http://h:8040", out, allow_draft=True, deps=deps_for(server)
    )
    assert rc == generate.REFUSED
    assert not out.exists()
    assert server.forms == []


def test_a_scored_run_from_a_draft_file_is_refused(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    plan = generate.make_plan("h3-t2v", "dog-a", 1, frames_root=first_frame)
    server = FakeServer(ok_reply())
    rc = generate.run_one(plan, "http://h:8040", tmp_path / "o", deps=deps_for(server))
    assert rc == generate.REFUSED
    assert server.forms == []
    assert not (tmp_path / "o").exists()


def test_a_probe_may_run_from_a_draft_file(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    plan = generate.make_plan("h3-t2v", "dog-a", 1, frames_root=first_frame)
    rc = generate.run_one(
        plan,
        "http://h:8040",
        tmp_path / "o",
        probe=True,
        deps=deps_for(FakeServer(ok_reply())),
    )
    assert rc == 0
    assert json.loads((tmp_path / "o" / "run.json").read_text())["probe"] is True


def test_an_existing_run_is_not_replaced_without_overwrite(
    tmp_path: pathlib.Path, first_frame: pathlib.Path
) -> None:
    plan = generate.make_plan("h3-t2v", "dog-a", 1, frames_root=first_frame)
    out = tmp_path / "o"
    out.mkdir()
    (out / "clip.mp4").write_bytes(b"scored")
    rc = generate.run_one(
        plan,
        "http://h:8040",
        out,
        allow_draft=True,
        deps=deps_for(FakeServer(ok_reply())),
    )
    assert rc == generate.REFUSED
    assert (out / "clip.mp4").read_bytes() == b"scored"


# --------------------------------------------------------------- energy ----


def at(seconds: float) -> datetime.datetime:
    return T0 + datetime.timedelta(seconds=seconds)


def test_constant_power_for_an_hour_is_its_watts_in_wh() -> None:
    points = [(at(s), 100.0) for s in range(0, 3601, 5)]
    assert generate.integrate_outlet(points, at(0), at(3600)) == pytest.approx(100.0)


def test_a_linear_ramp_integrates_to_its_mean() -> None:
    points = [(at(s), s / 3600 * 200.0) for s in range(0, 3601, 10)]
    assert generate.integrate_outlet(points, at(0), at(3600)) == pytest.approx(100.0)


def test_the_reading_in_force_at_the_start_covers_the_first_seconds() -> None:
    # 200 W from before the start until a 100 W reading at 1800 s. Without the
    # held edge, the first 1800 s would read as 100 W (the first inside value).
    points = [(at(-30), 200.0), (at(1800), 100.0), (at(3600), 100.0)]
    wh = generate.integrate_outlet(points, at(0), at(3600))
    assert wh == pytest.approx((1800 * 150 + 1800 * 100) / 3600)


def test_outlet_energy_waits_for_a_reading_after_the_end() -> None:
    late = [(at(s), 100.0) for s in range(0, 360, 5)]
    full = [*late, (at(365), 100.0)]
    answers = iter([flux_table(late), flux_table(full)])
    slept: list[float] = []
    got = generate.outlet_energy(
        "dgx_current_consumption",
        at(0),
        at(360),
        flux=lambda q: next(answers),
        sleep=slept.append,
        settle_s=7.0,
    )
    assert slept == [7.0, 7.0]
    assert got["complete"] is True
    assert got["energy_wh"] == pytest.approx(10.0)
    assert got["peak_w"] == 100.0


def test_outlet_energy_says_when_the_readings_never_caught_up() -> None:
    got = generate.outlet_energy(
        "e", at(0), at(360), flux=lambda q: "", sleep=lambda s: None, tries=2
    )
    assert got["complete"] is False
    assert got["energy_wh"] is None and got["readings"] == 0


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
