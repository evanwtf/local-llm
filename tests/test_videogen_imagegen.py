"""scripts/videogen/imagegen.py and the image arms (#1424): the arms and prompt
files, the request per arm, the reply checks, the run record, and the image
path through generate.py, batch.py and serve.py.

No GPU, no network: the HTTP calls, the exporters and the InfluxDB query are
fakes passed in through `generate.Deps`, as in test_videogen_generate.py.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_videogen_generate import FakeClock, Gpu, clock, flux_table, parse_form
from videogen import batch, generate, imagegen, outlet, serve
from videogen.common import png_size, prompt_name

T0 = datetime.datetime(2026, 11, 18, 15, 0, 0, tzinfo=datetime.UTC)
IMAGE_ARMS = generate.IMAGE_ARMS_FILE
EDIT_ARMS = generate.EDIT_ARMS_FILE
PROMPTS_DIR = ROOT / "benchmarks" / "videogen" / "prompts"
HEX64 = re.compile(r"[0-9a-f]{64}")
#: Bytes that open like a JPEG. The tests never decode an input image.
JPEG = b"\xff\xd8\xff\xe0 not a real jpeg"


def png(width: int, height: int) -> bytes:
    """A real, tiny PNG of `width` x `height` (one grey row repeated)."""
    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def load(path: pathlib.Path) -> dict[str, Any]:
    return generate.load_json(path)


# ------------------------------------------------------------- the files ----


def test_both_image_arms_files_pass_their_checks() -> None:
    for path in (IMAGE_ARMS, EDIT_ARMS):
        data = load(path)
        imagegen.check_arms(data)
        assert data["media"] == "image"
        assert data["seeds"] == [1, 2, 3]
        # Locked after one timing probe per arm (2026-11-18); each arm has its probe.
        assert data["status"] == "locked"
        assert {p["arm"] for p in data["locked"]["probes"]} == set(data["arms"])


def test_the_four_t2i_arms_are_pinned_to_the_issue_revisions() -> None:
    arms = load(IMAGE_ARMS)["arms"]
    assert {k: (v["model"], v["revision"], v["pipeline"]) for k, v in arms.items()} == {
        "qwen-image": ("Qwen/Qwen-Image-2.1", "d26bb61231", "QwenImage21Pipeline"),
        "hidream-o1": (
            "HiDream-ai/HiDream-O1-Image-Dev",
            "c0bada0e15",
            "HiDreamO1ImagePipeline",
        ),
        "z-image-turbo": ("Tongyi-MAI/Z-Image-Turbo", "f332072aa7", "ZImagePipeline"),
        "flux2-klein-4b": (
            "black-forest-labs/FLUX.2-klein-4B",
            "e7b7dc27f9",
            "Flux2KleinPipeline",
        ),
    }
    assert load(IMAGE_ARMS)["clips"] == ["meme-text", "photo-dog", "cartoon", "crowded"]


def test_the_model_card_defaults() -> None:
    """Each value is the model card's (the arm's `sources` says where)."""
    arms = load(IMAGE_ARMS)["arms"]
    assert {k: v["form"] for k, v in arms.items()} == {
        "qwen-image": {
            "width": 1024,
            "height": 1024,
            "num_inference_steps": 40,
            "true_cfg_scale": 1.0,
        },
        # 2048x2048: vLLM-Omni 0.32.0 snaps HiDream-O1 to its fixed sizes,
        # the smallest 2048x2048 (pipeline_hidream_o1_image.py:42-75).
        "hidream-o1": {
            "width": 2048,
            "height": 2048,
            "num_inference_steps": 28,
            "guidance_scale": 0.0,
        },
        "z-image-turbo": {
            "width": 1024,
            "height": 1024,
            "num_inference_steps": 9,
            "guidance_scale": 0.0,
        },
        "flux2-klein-4b": {
            "width": 1024,
            "height": 1024,
            "num_inference_steps": 4,
            "guidance_scale": 1.0,
        },
    }
    for arm in arms.values():
        assert "README.md at" in arm["sources"]


def test_the_edit_arms_and_their_defaults() -> None:
    arms = load(EDIT_ARMS)["arms"]
    assert {k: (v["model"], v["revision"], v["pipeline"]) for k, v in arms.items()} == {
        "qwen-image-edit": (
            "Qwen/Qwen-Image-Edit-2511",
            "6f3ccc0b56",
            "QwenImageEditPlusPipeline",
        ),
        "flux2-klein-4b-edit": (
            "black-forest-labs/FLUX.2-klein-4B",
            "e7b7dc27f9",
            "Flux2KleinPipeline",
        ),
    }
    qwen = arms["qwen-image-edit"]["form"]
    assert qwen == {
        "width": 1024,
        "height": 576,
        "num_inference_steps": 40,
        "true_cfg_scale": 4.0,
        "guidance_scale": 1.0,
        "negative_prompt": " ",
    }
    flux = arms["flux2-klein-4b-edit"]["form"]
    assert flux == {
        "width": 1024,
        "height": 576,
        "num_inference_steps": 4,
        "guidance_scale": 1.0,
    }
    assert load(EDIT_ARMS)["clips"] == ["caption-swap", "restyle", "face-swap"]


def test_arm_names_are_unique_across_every_arms_file() -> None:
    names = [a for path in generate.ARMS_FILES for a in load(path)["arms"]]
    assert len(names) == len(set(names))
    assert generate.find_arms_file("z-image-turbo") == IMAGE_ARMS
    assert generate.find_arms_file("qwen-image-edit") == EDIT_ARMS
    assert generate.find_arms_file("h3-t2v") == generate.ARMS_FILE
    with pytest.raises(generate.Refused, match="no arm 'nope'"):
        generate.find_arms_file("nope")


def test_one_model_per_spark_on_the_video_ports() -> None:
    servers = generate.all_servers()
    placement = {
        name: (block["node"], block["port"])
        for name, (block, media) in servers.items()
        if media == "image"
    }
    assert placement == {
        "qwen-image": ("head", 8040),
        "hidream-o1": ("worker", 8041),
        "z-image-turbo": ("head", 8040),
        "flux2-klein-4b": ("worker", 8041),
        "qwen-image-edit": ("head", 8040),
    }
    for name, (block, media) in servers.items():
        if media != "image":
            continue
        assert block["argv"][0] == "~/venvs/vllm-omni-0.32.0/bin/vllm"
        assert block["argv"][2] == f"~/models/imagegen/{name}"
        assert block["vllm_omni"] == "0.32.0"
        assert "VLLM_OMNI_VIDEO_SYNC_TIMEOUT" not in block["env"]
        assert "--quantization" not in block["argv"]


def test_the_shared_flux_server_block_is_the_same_in_both_files() -> None:
    a = load(IMAGE_ARMS)["servers"]["flux2-klein-4b"]
    b = load(EDIT_ARMS)["servers"]["flux2-klein-4b"]
    assert a == b


def test_all_servers_refuses_two_different_blocks_under_one_name(
    tmp_path: pathlib.Path,
) -> None:
    data = load(EDIT_ARMS)
    data["servers"]["flux2-klein-4b"]["port"] = 9999
    other = tmp_path / "edit.json"
    other.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="flux2-klein-4b: two arms files"):
        generate.all_servers([IMAGE_ARMS, other])


def test_only_a_video_server_needs_the_video_sync_timeout() -> None:
    block = load(IMAGE_ARMS)["servers"]["z-image-turbo"]
    generate.check_server("z", block, media="image")
    with pytest.raises(ValueError, match="VLLM_OMNI_VIDEO_SYNC_TIMEOUT"):
        generate.check_server("z", block)  # video by default


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"form": {"width": 1024, "height": 1024, "num_steps": 4}}, "num_steps"),
        ({"form": {"width": 1024, "height": 1000}}, "multiple of 16"),
        ({"form": {"height": 1024}}, "width must be"),
        ({"mode": "t2v"}, "mode must be t2i or edit"),
        ({"server": "nope"}, "no server block"),
        # boundary_ratio is a video form field; the image request has none.
        ({"form": {"width": 16, "height": 16, "boundary_ratio": 0.9}}, "boundary"),
    ],
)
def test_check_arms_refuses_a_bad_arm(change: dict[str, Any], message: str) -> None:
    data = load(IMAGE_ARMS)
    data["arms"]["z-image-turbo"].update(change)
    with pytest.raises(ValueError, match=message):
        imagegen.check_arms(data)


def test_an_edit_arm_may_not_send_a_t2i_only_field() -> None:
    data = load(EDIT_ARMS)
    data["arms"]["qwen-image-edit"]["form"]["flow_shift"] = 3.0
    with pytest.raises(ValueError, match="flow_shift"):
        imagegen.check_arms(data)


# ----------------------------------------------------------- the prompts ----


def test_the_image_prompts_have_names_and_10_to_12_checks() -> None:
    for name, count in (("image-v1.json", 4), ("edit-v1.json", 3)):
        data = load(PROMPTS_DIR / name)
        assert data["media"] == "image"
        assert len(data["clips"]) == count
        assert len(data["defects"]) == 6
        shorts = [d.split(" (")[0] for d in data["defects"]]
        assert len(set(shorts)) == len(shorts)  # judge.defect_labels needs this
        for entry in data["clips"]:
            assert entry["id"] == f"{entry['name']}-v1"
            assert prompt_name(entry) == entry["name"]
            assert 10 <= len(entry["checklist"]) <= 12
            assert entry["prompt"]


def test_the_arms_files_list_exactly_their_prompts() -> None:
    for arms_path in (IMAGE_ARMS, EDIT_ARMS):
        arms = load(arms_path)
        prompts = load(imagegen.prompts_file_of(arms_path, arms))
        assert arms["clips"] == [prompt_name(e) for e in prompts["clips"]]


def test_the_meme_checklist_tests_the_exact_caption() -> None:
    entry = {e["name"]: e for e in load(PROMPTS_DIR / "image-v1.json")["clips"]}
    meme = entry["meme-text"]
    for line in ("WHEN YOU HEAR THE TREAT BAG", "FROM THREE ROOMS AWAY"):
        assert f'"{line}"' in meme["prompt"]
        assert any(f'exactly "{line}"' in item for item in meme["checklist"])
    assert any("Impact" in item for item in meme["checklist"])
    crowded = entry["crowded"]
    assert "exactly three red balls" in crowded["prompt"].lower()
    assert any("left of the chair" in item for item in crowded["checklist"])


def test_the_edit_prompts_pin_their_inputs_and_name_no_one() -> None:
    entries = {e["name"]: e for e in load(PROMPTS_DIR / "edit-v1.json")["clips"]}
    base = {
        "file": "meme-base.jpg",
        "sha256": "28c77475739ac7c04905e284dfaf4b066779bc171c4f2ee612871433b0ab0f6f",
        "width": 596,
        "height": 335,
    }
    assert entries["caption-swap"]["inputs"] == [base]
    assert entries["restyle"]["inputs"] == [base]
    face = entries["face-swap"]["inputs"]
    assert face[0] == base
    assert face[1]["file"] == "face-ref.jpg" and face[1]["width"] == 588
    for entry in entries.values():
        for spec in entry["inputs"]:
            assert HEX64.fullmatch(spec["sha256"])
    swap = entries["caption-swap"]
    for line in ("I did not eat the couch.", "The couch was already like that."):
        assert f'"{line}"' in swap["prompt"]
        assert any(f'exactly "{line}"' in item for item in swap["checklist"])
    # Only "the person", "the person in image 1/2": no pronoun, no name.
    text = json.dumps(entries).lower()
    for word in (" he ", " she ", " his ", " her ", " man ", " woman "):
        assert word not in text


def test_no_image_file_is_tracked_under_benchmarks_videogen() -> None:
    for path in (ROOT / "benchmarks" / "videogen").rglob("*"):
        assert path.suffix.lower() not in {".png", ".jpg", ".jpeg"}, path


# ------------------------------------------------------------- the plans ----


@pytest.fixture
def files(tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
    """Locked copies of both arms files and their prompts, and fake inputs
    whose sha256 the edit prompts pin."""
    root = tmp_path / "bench"
    (root / "prompts").mkdir(parents=True)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    out: dict[str, pathlib.Path] = {"inputs": inputs}
    data = {"meme-base.jpg": JPEG + b"1", "face-ref.jpg": JPEG + b"2"}
    for name, body in data.items():
        (inputs / name).write_bytes(body)
    for key, src in (("image", IMAGE_ARMS), ("edit", EDIT_ARMS)):
        arms = load(src)
        arms["status"] = "locked"
        dst = root / src.name
        dst.write_text(json.dumps(arms))
        prompts = load(src.parent / arms["prompts"])
        for entry in prompts["clips"]:
            for spec in entry.get("inputs") or []:
                spec["sha256"] = hashlib.sha256(data[spec["file"]]).hexdigest()
        (root / arms["prompts"]).write_text(json.dumps(prompts))
        out[key] = dst
    return out


def plan_of(
    files: dict[str, pathlib.Path], arm: str, prompt: str, seed: int = 1
) -> imagegen.ImagePlan:
    key = "edit" if arm.endswith("-edit") else "image"
    return imagegen.make_plan(
        arm, prompt, seed, arms_file=files[key], inputs_root=files["inputs"]
    )


def test_a_t2i_body_is_json_with_numbers_and_one_size_string(
    files: dict[str, pathlib.Path],
) -> None:
    plan = plan_of(files, "z-image-turbo", "photo-dog", 3)
    assert plan.endpoint == "/v1/images/generations"
    assert plan.body["size"] == "1024x1024"
    assert "width" not in plan.body and "height" not in plan.body
    assert plan.body["num_inference_steps"] == 9
    assert plan.body["guidance_scale"] == 0.0
    assert plan.body["seed"] == 3
    assert plan.body["n"] == 1
    assert plan.body["response_format"] == "b64_json"
    assert plan.body["output_format"] == "png"
    assert plan.body["model"] == "Tongyi-MAI/Z-Image-Turbo"
    assert plan.body["prompt"].startswith("A photorealistic photo")
    assert plan.inputs == ()
    body, ctype = imagegen.encode(plan)
    assert ctype == "application/json"
    assert json.loads(body) == plan.body


def test_an_edit_body_is_a_form_with_every_input_in_order(
    files: dict[str, pathlib.Path],
) -> None:
    plan = plan_of(files, "qwen-image-edit", "face-swap", 2)
    assert plan.endpoint == "/v1/images/edits"
    assert all(isinstance(v, str) for v in plan.body.values())
    assert plan.body["negative_prompt"] == " "
    assert plan.body["true_cfg_scale"] == "4.0"
    assert plan.body["size"] == "1024x576"
    body, ctype = imagegen.encode(plan)
    assert ctype.startswith("multipart/form-data; boundary=")
    # parse_form keeps one part per name, so count the image parts by hand.
    assert body.count(b'name="image"; filename="meme-base.jpg"') == 1
    assert body.count(b'name="image"; filename="face-ref.jpg"') == 1
    assert body.index(b"meme-base.jpg") < body.index(b"face-ref.jpg")
    assert b"Content-Type: image/jpeg" in body
    form = parse_form(body, ctype)
    assert form["prompt"][1].decode().startswith("Replace the face")
    assert form["seed"][1] == b"2"
    names = [i.record()["name"] for i in plan.inputs]
    assert names == ["meme-base.jpg", "face-ref.jpg"]


def test_an_edit_input_that_is_not_the_pinned_one_is_refused(
    files: dict[str, pathlib.Path],
) -> None:
    (files["inputs"] / "face-ref.jpg").write_bytes(JPEG + b"changed")
    with pytest.raises(generate.Refused, match="not the input the prompt"):
        plan_of(files, "qwen-image-edit", "face-swap")
    # The one-input prompts do not use it, so they still plan.
    plan_of(files, "qwen-image-edit", "restyle")


def test_a_missing_edit_input_is_refused(files: dict[str, pathlib.Path]) -> None:
    (files["inputs"] / "meme-base.jpg").unlink()
    with pytest.raises(generate.Refused, match="does not exist"):
        plan_of(files, "flux2-klein-4b-edit", "caption-swap")


def test_a_prompt_from_the_other_file_is_refused(
    files: dict[str, pathlib.Path],
) -> None:
    with pytest.raises(generate.Refused, match="no prompt 'face-swap'"):
        plan_of(files, "z-image-turbo", "face-swap")
    with pytest.raises(generate.Refused, match="no prompt 'meme-text'"):
        plan_of(files, "qwen-image-edit", "meme-text")


def test_plan_for_dispatches_on_the_arms_file() -> None:
    frames = pathlib.Path("/nonexistent")
    image = generate.plan_for("flux2-klein-4b", "cartoon", 1, frames_root=frames)
    assert isinstance(image, imagegen.ImagePlan)
    assert image.output_name == "image.png"
    with pytest.raises(generate.Refused, match="holds image arms"):
        generate.make_plan(
            "flux2-klein-4b", "cartoon", 1, frames_root=frames, arms_file=IMAGE_ARMS
        )


# ------------------------------------------------------------- the reply ----


def reply(
    body: bytes | dict[str, Any],
    status: int = 200,
    ctype: str = "application/json",
) -> generate.Reply:
    raw = json.dumps(body).encode() if isinstance(body, dict) else body
    return generate.Reply(status, {"content-type": ctype}, raw)


def ok_body(image: bytes) -> dict[str, Any]:
    return {
        "created": 1,
        "data": [
            {"b64_json": base64.b64encode(image).decode(), "revised_prompt": None}
        ],
        "output_format": "png",
        "size": "1024x1024",
        "metrics": {"stage_durations": None, "peak_memory_mb": 41000.5},
    }


def test_decode_reply_returns_the_png_and_the_metrics() -> None:
    image = png(4, 2)
    got = imagegen.decode_reply(reply(ok_body(image)))
    assert isinstance(got, imagegen.Decoded)
    assert got.png == image
    assert got.metrics == {"stage_durations": None, "peak_memory_mb": 41000.5}
    assert png_size(got.png) == (4, 2)


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (reply(b'{"detail": "boom"}', status=500), "HTTP 500"),
        (reply(b"<html>proxy</html>", ctype="text/html"), "text/html, not application"),
        (reply(b"not json"), "not JSON"),
        (reply({"data": []}), "holds 0 images"),
        (reply({"nothing": 1}), "holds no images"),
        (reply({"data": [{"url": "http://x/a.png"}]}), "no b64_json"),
        (reply({"data": [{"b64_json": "@@@"}]}), "not valid base64"),
        (
            reply({"data": [{"b64_json": base64.b64encode(JPEG).decode()}]}),
            "not a PNG",
        ),
    ],
)
def test_decode_reply_refuses_a_bad_reply(bad: generate.Reply, message: str) -> None:
    got = imagegen.decode_reply(bad)
    assert isinstance(got, str)
    assert message in got


def test_png_size_reads_ihdr_and_refuses_other_bytes() -> None:
    assert png_size(png(1024, 576)) == (1024, 576)
    assert png_size(JPEG) is None
    assert png_size(png(3, 3)[:20]) is None


# --------------------------------------------------------------- the run ----


class FakeServer:
    def __init__(
        self, answer: generate.Reply | Exception, serves: tuple[str, ...] = ()
    ) -> None:
        self.answer = answer
        self.serves = serves
        self.posts: list[tuple[str, bytes, str]] = []

    def post(
        self, url: str, body: bytes, content_type: str, timeout: float
    ) -> generate.Reply:
        assert timeout == imagegen.TIMEOUT_S
        self.posts.append((url, body, content_type))
        time.sleep(0.03)  # long enough for the samplers to read
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def get(self, url: str, timeout: float) -> str | None:
        if url.endswith("/version"):
            return '{"version": "0.32.0"}'
        if url.endswith("/v1/models"):
            return json.dumps({"data": [{"id": m} for m in self.serves]})
        return None


def deps_for(
    server: FakeServer, gpu: Gpu | None = None, seconds: int = 30
) -> generate.Deps:
    end = T0 + datetime.timedelta(seconds=seconds)
    times = clock(T0, end)
    points = [(T0 + datetime.timedelta(seconds=s), 180.0) for s in range(-5, 36, 5)]
    table = flux_table(points)
    fake_clock = FakeClock()
    return generate.Deps(
        post=server.post,
        get=server.get,
        gpu=gpu or Gpu(),
        mem=lambda host: {"MemTotal": 119.5, "MemAvailable": 80.0},
        flux=lambda query: table,
        sleep=fake_clock.sleep,
        clock=fake_clock.now,
        now=lambda: next(times),
        machine=lambda: "Cortex-X925-128GB-GB10",
        sample_s=0.005,
    )


def go(
    plan: imagegen.ImagePlan,
    out: pathlib.Path,
    answer: generate.Reply | Exception,
    **kw: Any,
) -> tuple[int, FakeServer]:
    server = FakeServer(answer, serves=(str(plan.server["served_model_name"]),))
    gpu = kw.pop("gpu", None)
    deps = deps_for(server, gpu, kw.pop("seconds", 30))
    rc = imagegen.run_one(plan, "http://h:8041", out, deps=deps, **kw)
    return rc, server


def read_run(out: pathlib.Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / "run.json").read_text())
    return data


def test_a_t2i_run_records_what_the_issue_asks_for(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    plan = plan_of(files, "flux2-klein-4b", "meme-text", 2)
    image = png(1024, 1024)
    out = tmp_path / "out"
    rc, server = go(plan, out, reply(ok_body(image)), gpu=Gpu(before=[30.0, 12.0]))
    assert rc == 0
    assert (out / "image.png").read_bytes() == image
    assert not list(out.glob("*.partial"))
    [(url, body, ctype)] = server.posts
    assert url == "http://h:8041/v1/images/generations"
    assert ctype == "application/json"
    assert json.loads(body)["seed"] == 2
    run = read_run(out)
    assert run["status"] == "ok" and run["error"] is None
    assert run["media"] == "image" and run["mode"] == "t2i"
    assert run["arm"] == "flux2-klein-4b" and run["clip"] == "meme-text"
    assert run["prompt_id"] == "meme-text-v1"
    assert run["model"] == "black-forest-labs/FLUX.2-klein-4B"
    assert run["revision"] == "e7b7dc27f9"
    assert run["seed"] == 2
    fields = run["request"]["fields"]
    assert fields["num_inference_steps"] == 4 and fields["guidance_scale"] == 1.0
    assert fields["size"] == "1024x1024"
    assert run["request"]["inputs"] == []
    assert run["expected"] == {"width": 1024, "height": 1024}
    assert run["found"] == {"width": 1024, "height": 1024, "format": "png"}
    assert run["wall_s"] > 0
    # No x-inference-time-s, and this reply has no stage durations either.
    assert run["server_s"] is None and run["server_s_from"] == []
    assert run["server_metrics"] == {"stage_durations": None, "peak_memory_mb": 41000.5}
    assert run["energy_wh"] == pytest.approx(1.5)  # 180 W for 30 s
    assert run["outlet"]["entity"] == outlet.entity("worker")
    # Under MIN_GPU_MEAN_S: the samples stay, the mean does not.
    power = run["gpu_power"]
    assert power["mean_w"] is None and "lag the load" in power["no_mean"]
    assert power["readings"] and len(power["readings"]) == power["samples"]
    assert all(w == 60.0 for _, w in power["readings"])
    assert power["peak_w"] == 60.0
    assert run["memory"]["peak_used_gib"] == pytest.approx(39.5)
    assert run["server"]["settle_s"] == 1.0
    assert run["server"]["settle_readings"] == [[0.0, 30.0], [1.0, 12.0]]
    assert run["server"]["gpu_w_before"] == 12.0
    assert run["node"] == {"role": "worker"}
    assert run["endpoint"] == "/v1/images/generations"
    assert run["arms_file"] == "image-arms-v1.json"
    assert (
        run["prompts_sha256"]
        == hashlib.sha256(plan.prompts_file.read_bytes()).hexdigest()
    )
    assert run["output"]["sha256"] == hashlib.sha256(image).hexdigest()
    assert run["server_config"]["name"] == "flux2-klein-4b"


def test_an_edit_run_records_each_input_path_and_sha(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    plan = plan_of(files, "qwen-image-edit", "face-swap")
    out = tmp_path / "out"
    rc, server = go(plan, out, reply(ok_body(png(1024, 576))))
    assert rc == 0
    [(url, _, ctype)] = server.posts
    assert url == "http://h:8041/v1/images/edits"
    assert ctype.startswith("multipart/form-data")
    run = read_run(out)
    assert run["mode"] == "edit" and run["endpoint"] == "/v1/images/edits"
    inputs = run["inputs"]
    assert [i["name"] for i in inputs] == ["meme-base.jpg", "face-ref.jpg"]
    for item in inputs:
        path = pathlib.Path(item["path"])
        assert path.parent == files["inputs"]
        assert item["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert item["bytes"] == len(path.read_bytes())
    # The request names each input by name and sha256 only: no path.
    assert run["request"]["inputs"] == [
        {"name": i["name"], "sha256": i["sha256"]} for i in inputs
    ]
    assert run["request"]["fields"]["true_cfg_scale"] == "4.0"


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (reply(b'{"detail": "boom"}', status=500), "HTTP 500"),
        (reply(b"<html></html>", ctype="text/html"), "not application/json"),
        (reply({"data": []}), "holds 0 images"),
        (reply({"data": [{"b64_json": base64.b64encode(JPEG).decode()}]}), "not a PNG"),
        (reply(ok_body(png(512, 512))), "is 512x512, not the 1024x1024"),
        (ConnectionResetError("reset"), "request failed: ConnectionResetError"),
    ],
)
def test_a_failed_image_leaves_no_png_and_a_run_json_with_the_error(
    files: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
    answer: generate.Reply | Exception,
    message: str,
) -> None:
    plan = plan_of(files, "z-image-turbo", "cartoon")
    out = tmp_path / "out"
    rc, _ = go(plan, out, answer)
    assert rc == generate.FAILED
    assert not (out / "image.png").exists()
    assert not list(out.glob("*.partial"))
    run = read_run(out)
    assert run["status"] == "failed"
    assert message in run["error"]
    assert "output" not in run


def test_a_scored_run_from_the_draft_file_is_refused(tmp_path: pathlib.Path) -> None:
    # The real file is locked now, so test the gate on a draft copy of it.
    draft = tmp_path / "image-arms-draft.json"
    data = load(IMAGE_ARMS)
    data["status"] = "draft"
    data.pop("locked", None)
    draft.write_text(json.dumps(data))
    # The arms file names its prompts relative to its own folder.
    shutil.copytree(IMAGE_ARMS.parent / "prompts", tmp_path / "prompts")
    plan = imagegen.make_plan("z-image-turbo", "cartoon", 1, arms_file=draft)
    out = tmp_path / "out"
    rc, server = go(plan, out, reply(ok_body(png(1024, 1024))))
    assert rc == generate.REFUSED
    assert server.posts == [] and not out.exists()
    rc, server = go(plan, out, reply(ok_body(png(1024, 1024))), probe=True)
    assert rc == 0 and read_run(out)["probe"] is True


def test_a_server_that_serves_another_model_is_refused(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    plan = plan_of(files, "z-image-turbo", "cartoon")
    answer = reply(ok_body(png(1024, 1024)))
    server = FakeServer(answer, serves=("Qwen/Qwen-Image-2.1",))
    rc = imagegen.run_one(plan, "http://h:8040", tmp_path / "o", deps=deps_for(server))
    assert rc == generate.REFUSED
    assert server.posts == []


def test_an_existing_image_is_not_replaced_without_overwrite(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    plan = plan_of(files, "z-image-turbo", "cartoon")
    out = tmp_path / "out"
    out.mkdir()
    (out / "image.png").write_bytes(b"old")
    rc, server = go(plan, out, reply(ok_body(png(1024, 1024))))
    assert rc == generate.REFUSED and server.posts == []
    assert (out / "image.png").read_bytes() == b"old"


def test_run_plan_sends_an_image_plan_to_imagegen(
    files: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def image_run(plan: object, *a: object, **k: object) -> int:
        seen.append("image")
        return 0

    def video_run(plan: object, *a: object, **k: object) -> int:
        seen.append("video")
        return 0

    monkeypatch.setattr(imagegen, "run_one", image_run)
    monkeypatch.setattr(generate, "run_one", video_run)
    plan = plan_of(files, "z-image-turbo", "cartoon")
    assert generate.run_plan(plan, "http://h", tmp_path) == 0
    assert seen == ["image"]
    with pytest.raises(TypeError):
        generate.run_plan(object(), "http://h", tmp_path)  # type: ignore[arg-type]


# ------------------------------------------------------------- the batch ----


def test_a_batch_of_an_image_arm_runs_every_prompt_and_seed_and_no_frames(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    made: list[tuple[str, int]] = []
    framed: list[pathlib.Path] = []

    def generator(plan: imagegen.ImagePlan, out: pathlib.Path) -> int:
        made.append((plan.clip, plan.seed))
        out.mkdir(parents=True, exist_ok=True)
        image = png(8, 8) + plan.clip.encode()
        (out / "image.png").write_bytes(image)
        run = {
            "status": "ok",
            "probe": False,
            "arms_status": "locked",
            "request": plan.request(),
            "server_config": plan.server_config(),
            "energy_wh": 1.0,
            "output": {"sha256": hashlib.sha256(image).hexdigest()},
        }
        (out / "run.json").write_text(json.dumps(run))
        return 0

    def frames(clip: pathlib.Path, out: pathlib.Path, overwrite: bool) -> int:
        framed.append(clip)
        return 0

    def run() -> int:
        return batch.run_batch(
            "qwen-image-edit",
            server="http://h:8040",
            out_root=tmp_path / "vg" / "out",
            frames_out_root=tmp_path / "vg" / "frames-out",
            frames_root=tmp_path / "frames",
            arms_file=files["edit"],
            inputs_root=files["inputs"],
            generator=generator,
            frames_runner=frames,
            gallery_builder=None,
        )

    assert run() == 0
    assert made == [
        (p, s) for p in ("caption-swap", "restyle", "face-swap") for s in (1, 2, 3)
    ]
    assert framed == []
    made.clear()
    assert run() == 0  # every run is good now, so nothing is made again
    assert made == []
    # A stale image is made again.
    stale = tmp_path / "vg" / "out" / "qwen-image-edit" / "restyle-s2" / "image.png"
    stale.write_bytes(b"x")
    assert run() == 0
    assert made == [("restyle", 2)]


def test_batch_finds_the_image_arms_file_by_itself(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fake_run_one(
        plan: imagegen.ImagePlan, server: str, out: pathlib.Path, **kw: Any
    ) -> int:
        seen.append(plan.arm_name)
        return generate.FAILED

    monkeypatch.setattr(imagegen, "run_one", fake_run_one)
    rc = batch.run_batch(
        "z-image-turbo",
        server="http://h:8040",
        out_root=tmp_path / "out",
        frames_out_root=tmp_path / "fo",
        frames_root=tmp_path / "frames",
        allow_draft=True,
        gallery_builder=None,
    )
    assert rc == generate.FAILED and seen == ["z-image-turbo"]


# ------------------------------------------------------------- the server ----


def test_serve_logs_an_image_server_from_its_file(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    assert serve.serve("hidream-o1", start=False) == 0
    line = caplog.text
    assert "--served-model-name HiDream-ai/HiDream-O1-Image-Dev" in line
    assert "models/imagegen/hidream-o1" in line
    assert "VLLM_OMNI_VIDEO_SYNC_TIMEOUT" not in line
    argv, env = serve.command(
        load(IMAGE_ARMS)["servers"]["qwen-image"], pathlib.Path("/home/x")
    )
    assert argv[9:12] == [
        "/home/x/venvs/vllm-omni-0.32.0/bin/vllm",
        "serve",
        "/home/x/models/imagegen/qwen-image",
    ]
    assert "VLLM_OMNI_VIDEO_SYNC_TIMEOUT" not in env


def test_serve_still_finds_the_video_servers() -> None:
    assert serve.serve("h3", start=False) == 0
    assert serve.serve("no-such-server", start=False) == 2


# ------------------------------------------------- #1424 review: server_s ----


def test_server_seconds_sums_the_stage_ms_keys_only() -> None:
    # The FLUX.2-klein-4B probe reply of 2026-11-18.
    metrics = {"stage_durations": {"stage_0_gen_ms": 2977.2, "queue_wait_ms": 0.56}}
    assert imagegen.server_seconds(metrics) == (2.977, ["stage_0_gen_ms"])
    two = {"stage_durations": {"stage_1_gen_ms": 500.0, "stage_0_gen_ms": 1500.0}}
    assert imagegen.server_seconds(two) == (2.0, ["stage_0_gen_ms", "stage_1_gen_ms"])


@pytest.mark.parametrize(
    "metrics",
    [
        None,
        {},
        {"stage_durations": None},
        {"stage_durations": {"queue_wait_ms": 3.0}},
        {"stage_durations": {"stage_0_gen_ms": "fast"}},
        {"stage_durations": {"stage_0_gen_ms": True}},
        {"stage_durations": [1, 2]},
    ],
)
def test_server_seconds_is_none_without_a_stage_time(metrics: Any) -> None:
    assert imagegen.server_seconds(metrics) == (None, [])


def test_a_run_records_server_s_from_the_stage_durations(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    plan = plan_of(files, "z-image-turbo", "cartoon")
    body = ok_body(png(1024, 1024))
    body["metrics"]["stage_durations"] = {"stage_0_gen_ms": 2977.2, "queue_wait_ms": 1}
    rc, _ = go(plan, tmp_path / "out", reply(body))
    assert rc == 0
    run = read_run(tmp_path / "out")
    assert run["server_s"] == 2.977
    assert run["server_s_from"] == ["stage_0_gen_ms"]


# -------------------------------------------------- #1424 review: energy ----


def reading(complete: bool = True, readings: int = 3, wh: Any = 1.5) -> dict[str, Any]:
    return {"complete": complete, "readings": readings, "energy_wh": wh}


@pytest.mark.parametrize(
    ("got", "seconds", "want"),
    [
        (reading(), 30.0, 1.5),
        (reading(readings=7), 120.0, 1.5),
        (reading(readings=2), 30.0, None),  # too few readings
        (reading(), 29.9, None),  # too short
        (reading(complete=False), 60.0, None),
        (reading(wh=None), 60.0, None),
    ],
)
def test_request_energy_needs_a_complete_long_reading(
    got: dict[str, Any], seconds: float, want: float | None
) -> None:
    assert generate.request_energy(got, seconds) == want


def test_a_short_run_keeps_its_outlet_reading_but_has_no_energy(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    plan = plan_of(files, "z-image-turbo", "cartoon")
    rc, _ = go(plan, tmp_path / "out", reply(ok_body(png(1024, 1024))), seconds=4)
    assert rc == 0
    run = read_run(tmp_path / "out")
    assert run["energy_wh"] is None
    assert run["outlet"]["complete"] is True
    assert run["outlet"]["readings"] == 1  # one plug reading in 4 s
    assert run["outlet"]["energy_wh"] == pytest.approx(0.2)


def test_the_gpu_mean_stays_for_a_run_of_min_gpu_mean_s(
    files: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(imagegen, "MIN_GPU_MEAN_S", 0.0)
    plan = plan_of(files, "z-image-turbo", "cartoon")
    rc, _ = go(plan, tmp_path / "out", reply(ok_body(png(1024, 1024))))
    assert rc == 0
    power = read_run(tmp_path / "out")["gpu_power"]
    assert power["mean_w"] == 60.0 and "no_mean" not in power


def test_inside_a_block_a_run_neither_settles_nor_reads_the_outlet(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    plan = plan_of(files, "z-image-turbo", "cartoon")
    server = FakeServer(
        reply(ok_body(png(1024, 1024))),
        serves=(str(plan.server["served_model_name"]),),
    )
    gpu = Gpu(before=[])
    deps = deps_for(server, gpu)
    flux_calls: list[str] = []
    deps = generate.Deps(**{**vars(deps), "flux": flux_calls.append})
    out = tmp_path / "out"
    rc = imagegen.run_one(
        plan, "http://h:8041", out, deps=deps, settle=False, read_outlet=False
    )
    assert rc == 0
    run = read_run(out)
    assert run["outlet"] == {"deferred": "batch.py reads it after the block"}
    assert run["energy_wh"] is None
    assert not flux_calls
    assert run["server"]["settle_s"] is None
    assert "settled once" in run["server"]["settle_skipped"]


# --------------------------------------------------- #1424 review: inputs ----


def test_the_request_is_the_same_under_another_inputs_root(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    for path in files["inputs"].iterdir():
        (other / path.name).write_bytes(path.read_bytes())
    a = plan_of(files, "qwen-image-edit", "face-swap")
    b = imagegen.make_plan(
        "qwen-image-edit", "face-swap", 1, arms_file=files["edit"], inputs_root=other
    )
    assert a.request() == b.request()
    assert all(set(i) == {"name", "sha256"} for i in a.request()["inputs"])


# --------------------------------------------- #1424 review: warm-up, block ----


def test_the_warm_up_sends_seed_0_and_never_raises(
    files: dict[str, pathlib.Path],
) -> None:
    plan = plan_of(files, "qwen-image-edit", "face-swap", 2)
    server = FakeServer(reply(ok_body(png(1024, 576))))
    got = imagegen.warm_up(plan, "http://h:8041", deps_for(server))
    assert got["error"] is None and got["http_status"] == 200 and got["wall_s"] > 0
    [(url, body, ctype)] = server.posts
    assert url.endswith("/v1/images/edits")
    assert parse_form(body, ctype)["seed"] == (None, b"0")
    failing = FakeServer(OSError("refused"))
    got = imagegen.warm_up(plan, "http://h:8041", deps_for(failing))
    assert got["error"] and "refused" in got["error"]


def block_run(
    arm_dir: pathlib.Path, n: int, seconds: int = 3, status: str = "ok"
) -> pathlib.Path:
    run_dir = arm_dir / f"p-s{n}"
    run_dir.mkdir(parents=True)
    start = T0 + datetime.timedelta(seconds=seconds * n)
    record = {
        "status": status,
        "media": "image",
        "start": generate.iso(start),
        "end": generate.iso(start + datetime.timedelta(seconds=seconds)),
        "outlet": {"deferred": "batch.py reads it after the block"},
        "energy_wh": None,
    }
    (run_dir / "run.json").write_text(json.dumps(record))
    return run_dir


def block_deps(watts: float = 180.0, step: int = 5, upto: int = 60) -> generate.Deps:
    points = [
        (T0 + datetime.timedelta(seconds=s), watts) for s in range(-5, upto, step)
    ]
    table = flux_table(points)
    return generate.Deps(flux=lambda query: table, sleep=lambda s: None)


def test_a_block_of_twelve_images_gives_wh_per_image(tmp_path: pathlib.Path) -> None:
    arm_dir = tmp_path / "out" / "z-image-turbo"
    block = imagegen.Block("http://h:8040", deps=block_deps(), role="head")
    for n in range(12):
        block.add(block_run(arm_dir, n))
    got = block.close(arm_dir, "z-image-turbo")
    assert got is not None
    saved = json.loads((arm_dir / "block.json").read_text())
    assert saved == got
    assert saved["requests"] == saved["images"] == 12
    assert saved["duration_s"] == 36.0
    assert saved["energy_wh"] == pytest.approx(1.8)  # 180 W for 36 s
    assert saved["wh_per_image"] == pytest.approx(0.15)
    assert saved["outlet"]["readings"] == 8 and saved["outlet"]["complete"]
    # Each run now has its own reading, but no energy: 3 s is too short.
    for n in range(12):
        run = json.loads((arm_dir / f"p-s{n}" / "run.json").read_text())
        assert "deferred" not in run["outlet"]
        assert run["outlet"]["entity"] == outlet.entity("head")
        assert run["energy_wh"] is None


def test_a_block_with_a_failed_request_has_no_wh_per_image(
    tmp_path: pathlib.Path,
) -> None:
    arm_dir = tmp_path / "out" / "z-image-turbo"
    block = imagegen.Block("http://h:8040", deps=block_deps(), role="head")
    for n in range(11):
        block.add(block_run(arm_dir, n))
    block.add(block_run(arm_dir, 11, status="failed"))
    got = block.close(arm_dir, "z-image-turbo")
    assert got is not None
    assert got["images"] == 11 and got["requests"] == 12
    assert got["energy_wh"] == pytest.approx(1.8)
    assert got["wh_per_image"] is None


def test_a_short_or_gappy_block_has_no_energy(tmp_path: pathlib.Path) -> None:
    arm_dir = tmp_path / "out" / "z-image-turbo"
    short = imagegen.Block("http://h:8040", deps=block_deps(), role="head")
    for n in range(3):  # 9 s
        short.add(block_run(arm_dir, n))
    got = short.close(arm_dir, "z-image-turbo")
    assert got is not None and got["energy_wh"] is None and got["wh_per_image"] is None
    gappy = imagegen.Block("http://h:8040", deps=block_deps(step=40), role="head")
    for n in range(3, 15):
        gappy.add(block_run(arm_dir, n))
    got = gappy.close(arm_dir, "z-image-turbo")
    assert got is not None and got["outlet"]["complete"] is False
    assert got["energy_wh"] is None and got["wh_per_image"] is None


def test_a_new_block_keeps_the_earlier_block_file(tmp_path: pathlib.Path) -> None:
    arm_dir = tmp_path / "out" / "z-image-turbo"
    first = imagegen.Block("http://h:8040", deps=block_deps(), role="head")
    for n in range(12):
        first.add(block_run(arm_dir, n))
    old = first.close(arm_dir, "z-image-turbo")
    assert old is not None
    second = imagegen.Block("http://h:8040", deps=block_deps(), role="head")
    second.add(block_run(arm_dir, 12))
    second.close(arm_dir, "z-image-turbo")
    [kept] = list(arm_dir.glob("block-*.json"))
    assert json.loads(kept.read_text()) == old
    assert json.loads((arm_dir / "block.json").read_text())["requests"] == 1


def test_block_start_warms_up_and_settles_once(
    files: dict[str, pathlib.Path],
) -> None:
    plan = plan_of(files, "z-image-turbo", "cartoon")
    server = FakeServer(reply(ok_body(png(1024, 1024))))
    block = imagegen.Block("http://h:8040", deps=deps_for(server))
    block.start(plan)
    block.start(plan)
    assert len(server.posts) == 1
    assert json.loads(server.posts[0][1])["seed"] == imagegen.WARM_UP_SEED
    assert block.role == "head"
    assert block.warm is not None and block.warm["error"] is None
    assert block.settled is not None and block.settled["gpu_w_before"] == 12.0


class RecordingBlock(imagegen.Block):
    """A block that sends nothing: it records what batch.py asks of it."""

    def __init__(self, server: str, node: str | None, host: str | None) -> None:
        super().__init__(server, node, host)
        self.events: list[str] = []

    def start(self, plan: imagegen.ImagePlan) -> None:
        self.events.append(f"start {plan.clip}-s{plan.seed}")

    def add(self, run_dir: pathlib.Path) -> None:
        super().add(run_dir)
        self.events.append(f"add {run_dir.name}")

    def close(self, arm_dir: pathlib.Path, arm: str) -> dict[str, Any] | None:
        self.events.append(f"close {arm}")
        (arm_dir / imagegen.BLOCK_FILE).write_text("{}")
        return {}


def image_batch(
    files: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
    blocks: list[RecordingBlock],
    **kw: Any,
) -> int:
    def maker(server: str, node: str | None, host: str | None) -> RecordingBlock:
        blocks.append(RecordingBlock(server, node, host))
        return blocks[-1]

    return batch.run_batch(
        "z-image-turbo",
        server="http://h:8040",
        out_root=tmp_path / "out",
        frames_out_root=tmp_path / "frames-out",
        frames_root=tmp_path / "frames",
        arms_file=files["image"],
        inputs_root=files["inputs"],
        block_maker=maker,
        **kw,
    )


def write_image_run(plan: imagegen.ImagePlan, out: pathlib.Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    image = png(8, 8)
    (out / "image.png").write_bytes(image)
    record = {
        "status": "ok",
        "media": "image",
        "probe": False,
        "arms_status": "locked",
        "request": plan.request(),
        "server_config": plan.server_config(),
        "energy_wh": None,
        "outlet": {"deferred": "batch.py reads it after the block"},
        "output": {"sha256": hashlib.sha256(image).hexdigest()},
    }
    (out / "run.json").write_text(json.dumps(record))
    return 0


def test_batch_runs_an_image_arm_as_one_block(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    blocks: list[RecordingBlock] = []
    rebuilt: list[pathlib.Path] = []
    rc = image_batch(
        files,
        tmp_path,
        blocks,
        generator=write_image_run,
        gallery_builder=rebuilt.append,
    )
    assert rc == 0
    [block] = blocks
    order = [
        f"{c}-s{s}"
        for c in ("meme-text", "photo-dog", "cartoon", "crowded")
        for s in (1, 2, 3)
    ]
    # start before each request (the block warms up only on the first);
    # no gallery rebuild inside the block, one after it.
    assert block.events == [
        e for name in order for e in (f"start {name}", f"add {name}")
    ] + ["close z-image-turbo"]
    assert rebuilt == [tmp_path]


def test_batch_passes_no_settle_and_no_outlet_read_inside_a_block(
    files: dict[str, pathlib.Path],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, Any]] = []

    def fake_run_plan(plan: Any, server: str, out: pathlib.Path, **kw: Any) -> int:
        seen.append(kw)
        return write_image_run(plan, out)

    monkeypatch.setattr(generate, "run_plan", fake_run_plan)
    assert image_batch(files, tmp_path, [], gallery_builder=None) == 0
    assert len(seen) == 12
    assert all(kw["settle"] is False and kw["read_outlet"] is False for kw in seen)


def test_an_image_run_with_a_deferred_outlet_is_made_again(
    files: dict[str, pathlib.Path], tmp_path: pathlib.Path
) -> None:
    plan = plan_of(files, "z-image-turbo", "cartoon")
    out = tmp_path / "run"
    write_image_run(plan, out)
    assert batch.state(out, plan)[0] == batch.TODO
    record = json.loads((out / "run.json").read_text())
    record["outlet"] = {"complete": True, "readings": 1, "energy_wh": 0.1}
    (out / "run.json").write_text(json.dumps(record))
    # Good with no energy of its own: the block has the energy.
    assert batch.state(out, plan) == (batch.GOOD, "done")


# -------------------------------------------------- #1424 review: Refused ----


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "videogen" / "generate.py"), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_cli_exits_refused_for_an_edit_input_that_is_missing(
    tmp_path: pathlib.Path,
) -> None:
    """imagegen raises the one Refused in common.py, so generate.py run as a
    script catches it (exit 2), not a traceback (exit 1)."""
    got = run_cli(
        "--arm",
        "qwen-image-edit",
        "--prompt",
        "face-swap",
        "--seed",
        "1",
        "--server",
        "http://127.0.0.1:9",
        "--inputs-root",
        str(tmp_path),
        "--out",
        str(tmp_path / "out"),
    )
    assert got.returncode == generate.REFUSED, got.stdout + got.stderr
    assert "Traceback" not in got.stderr
    assert "does not exist" in got.stdout + got.stderr


def test_the_cli_passes_the_draft_gate_for_a_locked_image_arm(
    tmp_path: pathlib.Path,
) -> None:
    # The draft refusal itself is tested on a draft copy above; through the CLI the
    # real, locked file must get past the gate and fail only on the absent server.
    got = run_cli(
        "--arm",
        "z-image-turbo",
        "--prompt",
        "cartoon",
        "--seed",
        "1",
        "--server",
        "http://127.0.0.1:9",
        "--out",
        str(tmp_path / "out"),
    )
    assert "Traceback" not in got.stderr
    assert "not 'locked'" not in got.stdout + got.stderr
    assert "is the server up?" in got.stdout + got.stderr
    assert not (tmp_path / "out" / "image.png").exists()
