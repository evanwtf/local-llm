"""Make one text-to-image or image-edit run on a vLLM-Omni server and record it (#1424).

    generate.py --arm z-image-turbo --prompt meme-text --seed 1 --server http://HOST:8040
    generate.py --arm qwen-image-edit --prompt face-swap --seed 1 --server http://HOST:8040

`generate.py` and `batch.py` call this module for an arm in
`benchmarks/videogen/image-arms-v1.json` (text-to-image) or
`benchmarks/videogen/edit-arms-v1.json` (image edits). It has no CLI of its
own: the checks before the request, the GPU settle, the power and memory
samplers and the outlet energy are `generate.py`'s code, so a clip and an
image are measured the same way.

The API, read from the installed vLLM-Omni 0.32.0 (`vllm_omni/...`):

- Text-to-image is `POST /v1/images/generations` with a JSON body
  (entrypoints/openai/api_server.py:2090; the fields are
  protocol/images.py:54-204). The size is one `WIDTHxHEIGHT` string.
- An edit is `POST /v1/images/edits`, a multipart form with one `image`
  part per input image, in order (api_server.py:2318-2403). Up to 4 inputs
  for both edit pipelines (diffusion/model_metadata.py:30-31).
- Both answer JSON: `data[0].b64_json` is the image, base64 PNG, and
  `metrics` holds `stage_durations` and `peak_memory_mb`
  (api_server.py:2049-2087). Neither sends `x-inference-time-s` (only
  /v1/videos/sync does, api_server.py:2855-2859), so an image run has no
  server time: `server_s` is null, and `server_metrics` keeps what the
  server sent.

An image run writes `image.png` and `run.json`. `run.json` holds what a clip's
run.json holds (the arm, model, revision, server block, request, seed, start
and end, wall seconds, energy, GPU power, peak memory, node role, output
sha256), plus `media: image`, the PNG's size, and each input image's path and
sha256. The PNG must have the size the arm asked for, or the run fails.

A failure (an HTTP error, a reply that is not JSON, no image, an image that
is not a PNG, a wrong size) writes `run.json` with the error, leaves no
`image.png`, and returns FAILED. A refusal (an input image that is missing or
not the one the prompt file pins, a draft arms file for a scored run, and
the rest of `generate.prepare`) writes nothing.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import json
import logging
import os
import pathlib
import sys
import uuid
from collections.abc import Mapping
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from videogen import generate
from videogen.common import (
    IMAGE_INPUTS_ROOT,
    png_size,
    prompt_name,
    sha256,
)

logger = logging.getLogger(__name__)

GENERATIONS = "/v1/images/generations"
EDITS = "/v1/images/edits"
#: The endpoint of each image mode.
ENDPOINTS = {"t2i": GENERATIONS, "edit": EDITS}
OUTPUT = "image.png"
#: One hour. A 40-step 1024x1024 image takes minutes; the image endpoints have
#: no server-side timeout like VLLM_OMNI_VIDEO_SYNC_TIMEOUT.
TIMEOUT_S = 3600.0
#: The most input images an edit may send: the cap of both edit pipelines in
#: 0.32.0 (FLUX2_KLEIN_MAX_INPUT_IMAGES and
#: QWEN_IMAGE_EDIT_PLUS_MAX_INPUT_IMAGES, diffusion/model_metadata.py:30-31).
MAX_INPUTS = 4
#: The arm form keys a text-to-image request may carry, besides the size.
#: Each is a field of ImageGenerationRequest (protocol/images.py:119-158).
#: The request model allows extra keys (:62) and then ignores them, so a typo
#: would change nothing and look fine: an arms file that names another key
#: is refused.
T2I_FIELDS = frozenset(
    {"negative_prompt", "num_inference_steps", "guidance_scale", "true_cfg_scale"}
)
#: The same for an edit: Form fields of edit_images (api_server.py:2349-2355).
#: FastAPI drops an unknown form field without a word.
EDIT_FIELDS = frozenset(
    {
        "negative_prompt",
        "num_inference_steps",
        "guidance_scale",
        "guidance_scale_2",
        "strength",
        "true_cfg_scale",
    }
)
ALLOWED = {"t2i": T2I_FIELDS, "edit": EDIT_FIELDS}
#: Input image types, by file suffix.
INPUT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


# ---------------------------------------------------------------- config ----


def check_arms(arms: Mapping[str, Any]) -> None:
    """Raise ValueError if an image arms file is unsafe or names a field the
    server ignores."""
    for name, block in arms["servers"].items():
        generate.check_server(name, block, media="image")
    for name, arm in arms["arms"].items():
        mode = arm.get("mode")
        if mode not in ENDPOINTS:
            raise ValueError(f"arm {name}: mode must be t2i or edit")
        form = dict(arm["form"])
        for key in ("width", "height"):
            value = form.pop(key, None)
            if not isinstance(value, int) or value <= 0 or value % 16:
                raise ValueError(f"arm {name}: {key} must be a multiple of 16")
        unknown = set(form) - ALLOWED[mode]
        if unknown:
            raise ValueError(f"arm {name}: unknown form field(s) {sorted(unknown)}")
        if arm["server"] not in arms["servers"]:
            raise ValueError(f"arm {name}: no server block {arm['server']!r}")


def prompts_file_of(arms_file: pathlib.Path, arms: Mapping[str, Any]) -> pathlib.Path:
    """The prompts file an arms file names, relative to the arms file."""
    return arms_file.parent / str(arms["prompts"])


def prompt_entry(prompts: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    for entry in prompts["clips"]:
        if prompt_name(entry) == name:
            return dict(entry)
    raise generate.Refused(f"no prompt {name!r} in the prompts file")


@dataclasses.dataclass(frozen=True)
class InputImage:
    """One input image of an edit, read and checked."""

    path: pathlib.Path
    data: bytes
    content_type: str

    def record(self) -> dict[str, Any]:
        return {
            "name": self.path.name,
            "path": str(self.path),
            "bytes": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
        }


def load_inputs(
    entry: Mapping[str, Any], inputs_root: pathlib.Path
) -> tuple[InputImage, ...]:
    """The prompt's input images, each checked against its pinned sha256."""
    specs = entry.get("inputs") or []
    if not 1 <= len(specs) <= MAX_INPUTS:
        raise generate.Refused(
            f"{prompt_name(entry)}: an edit needs 1 to {MAX_INPUTS} input images"
        )
    images = []
    for spec in specs:
        name = str(spec["file"])
        if pathlib.PurePath(name).name != name or name.startswith("."):
            raise generate.Refused(f"{name!r} is not a plain file name")
        content_type = INPUT_TYPES.get(pathlib.PurePath(name).suffix.lower())
        if content_type is None:
            raise generate.Refused(f"{name}: not a PNG or JPEG name")
        path = inputs_root / name
        if not path.is_file():
            raise generate.Refused(f"{path} does not exist")
        data = path.read_bytes()
        got = hashlib.sha256(data).hexdigest()
        if got != spec["sha256"]:
            raise generate.Refused(
                f"{path} has sha256 {got}, but the prompts file pins "
                f"{spec['sha256']}: it is not the input the prompt was written for"
            )
        images.append(InputImage(path, data, content_type))
    return tuple(images)


@dataclasses.dataclass(frozen=True)
class ImagePlan:
    """Everything one image request needs, settled before it is sent."""

    arm_name: str
    arm: Mapping[str, Any]
    arms_status: str
    arms_file: pathlib.Path
    prompts_file: pathlib.Path
    clip: str  # the prompt name; `clip` so batch.py and judge.py share code
    prompt_id: str
    seed: int
    body: dict[str, Any]
    inputs: tuple[InputImage, ...]
    server: Mapping[str, Any]

    @property
    def output_name(self) -> str:
        return OUTPUT

    @property
    def mode(self) -> str:
        return str(self.arm["mode"])

    @property
    def endpoint(self) -> str:
        return ENDPOINTS[self.mode]

    def request(self) -> dict[str, Any]:
        """What run.json keeps of the request; batch.py compares it."""
        return {
            "fields": dict(self.body),
            "inputs": [image.record() for image in self.inputs],
        }

    def server_config(self) -> dict[str, Any]:
        return generate.server_record(self.arm, self.server)

    def expect(self) -> dict[str, int]:
        """The size the PNG must have."""
        return {
            "width": int(self.arm["form"]["width"]),
            "height": int(self.arm["form"]["height"]),
        }


def build_body(
    arm: Mapping[str, Any], prompt: str, seed: int, model: str
) -> dict[str, Any]:
    """The request fields as sent.

    Text-to-image sends JSON, so its numbers stay numbers. An edit sends a
    form, so every value is a string. The size is one `WxH` string in both.
    """
    form = dict(arm["form"])
    size = f"{form.pop('width')}x{form.pop('height')}"
    body: dict[str, Any] = {
        "prompt": prompt,
        "model": model,
        "n": 1,
        "size": size,
        "response_format": "b64_json",
        "output_format": "png",
        "seed": seed,
        **form,
    }
    if arm["mode"] == "edit":
        return {k: str(v) for k, v in body.items()}
    return body


def make_plan(
    arm_name: str,
    clip: str,
    seed: int,
    *,
    arms_file: pathlib.Path,
    inputs_root: pathlib.Path = IMAGE_INPUTS_ROOT,
) -> ImagePlan:
    """Read the arms and prompts files (and an edit's inputs); build the request."""
    arms = generate.load_json(arms_file)
    check_arms(arms)
    if arm_name not in arms["arms"]:
        raise generate.Refused(
            f"no arm {arm_name!r}; the arms are {sorted(arms['arms'])}"
        )
    if clip not in arms["clips"]:
        raise generate.Refused(f"no prompt {clip!r}; the prompts are {arms['clips']}")
    arm = arms["arms"][arm_name]
    server = arms["servers"][arm["server"]]
    prompts_file = prompts_file_of(arms_file, arms)
    entry = prompt_entry(generate.load_json(prompts_file), clip)
    if arm["mode"] == "edit":
        inputs = load_inputs(entry, inputs_root.expanduser())
    elif entry.get("inputs"):
        raise generate.Refused(f"{clip} has input images; a t2i arm sends none")
    else:
        inputs = ()
    return ImagePlan(
        arm_name=arm_name,
        arm=arm,
        arms_status=str(arms["status"]),
        arms_file=arms_file,
        prompts_file=prompts_file,
        clip=clip,
        prompt_id=str(entry["id"]),
        seed=seed,
        body=build_body(
            arm, str(entry["prompt"]), seed, str(server["served_model_name"])
        ),
        inputs=inputs,
        server=server,
    )


# ------------------------------------------------------------------ http ----


def encode(plan: ImagePlan) -> tuple[bytes, str]:
    """(request body, content type): JSON for t2i, a form for an edit."""
    if plan.mode == "t2i":
        return json.dumps(plan.body).encode(), "application/json"
    boundary = "imagegen-" + uuid.uuid4().hex
    files = [
        ("image", image.path.name, image.content_type, image.data)
        for image in plan.inputs
    ]
    return (
        generate.encode_parts(plan.body, files, boundary),
        f"multipart/form-data; boundary={boundary}",
    )


@dataclasses.dataclass(frozen=True)
class Decoded:
    png: bytes
    metrics: dict[str, Any] | None
    size: str | None


def decode_reply(reply: generate.Reply) -> Decoded | str:
    """The PNG and the server's metrics from a reply, or why there are none."""
    if reply.status != 200:
        text = reply.body[:2000].decode("utf-8", "replace")
        return f"HTTP {reply.status}: {text}"
    ctype = reply.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        return f"the reply is {ctype or 'untyped'}, not application/json"
    try:
        data = json.loads(reply.body)
    except ValueError:
        return f"the reply is not JSON ({len(reply.body)} bytes)"
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) != 1:
        count = len(items) if isinstance(items, list) else "no"
        return f"the reply holds {count} images, not 1"
    b64 = items[0].get("b64_json") if isinstance(items[0], dict) else None
    if not isinstance(b64, str) or not b64:
        return "the reply has no b64_json image"
    try:
        png = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return "the reply's b64_json is not valid base64"
    if png_size(png) is None:
        return f"the image is not a PNG ({len(png)} bytes, starts {png[:8]!r})"
    metrics = data.get("metrics")
    size = data.get("size")
    return Decoded(
        png=png,
        metrics=metrics if isinstance(metrics, dict) else None,
        size=size if isinstance(size, str) else None,
    )


def keep_image(
    plan: ImagePlan, path: pathlib.Path, png: bytes, record: dict[str, Any]
) -> str | None:
    """Write the PNG to .partial, check its size, then rename it.

    Returns an error, or None once image.png holds a checked image. The
    .partial file never survives.
    """
    tmp: pathlib.Path | None = None
    try:
        tmp = generate.write_partial(path, png)
        found = png_size(tmp.read_bytes())
        record["expected"] = plan.expect()
        record["found"] = (
            {"width": found[0], "height": found[1], "format": "png"} if found else None
        )
        if found != (plan.expect()["width"], plan.expect()["height"]):
            got = f"{found[0]}x{found[1]}" if found else "no PNG size"
            want = "{width}x{height}".format(**plan.expect())
            return f"the image is {got}, not the {want} the arm asked for"
        os.replace(tmp, path)
        return None
    except OSError as err:
        return f"could not write the image: {err}"
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------- run ----


def run_one(
    plan: ImagePlan,
    server: str,
    out: pathlib.Path,
    *,
    probe: bool = False,
    node: str | None = None,
    metrics_host: str | None = None,
    allow_draft: bool = False,
    overwrite: bool = False,
    deps: generate.Deps | None = None,
) -> int:
    """Make one image. Returns 0, FAILED (run.json says why) or REFUSED."""
    deps = deps or generate.Deps()
    try:
        prepared = generate.prepare(
            plan,
            server,
            out,
            probe=probe,
            node=node,
            metrics_host=metrics_host,
            allow_draft=allow_draft,
            overwrite=overwrite,
            deps=deps,
        )
    except generate.Refused as err:
        logger.error("%s", err)
        return generate.REFUSED
    image_path, run_path = out / OUTPUT, out / "run.json"
    record = generate.base_record(
        plan,
        prepared,
        probe=probe,
        prompts_file=plan.prompts_file,
        endpoint=plan.endpoint,
        deps=deps,
    )
    record["media"] = "image"
    record["prompt_id"] = plan.prompt_id
    body, content_type = encode(plan)
    measured = generate.measured_post(
        server.rstrip("/") + plan.endpoint,
        body,
        content_type,
        TIMEOUT_S,
        prepared.host,
        deps,
    )
    record.update(measured.record)
    # The image endpoints send no inference time (see the module docstring).
    record["server_s"] = None
    record["server_metrics"] = None
    error = measured.error
    reply = measured.reply
    if error is None and reply is not None:
        decoded = decode_reply(reply)
        if isinstance(decoded, str):
            error = decoded
        else:
            record["server_metrics"] = decoded.metrics
            record["server_size"] = decoded.size
            error = keep_image(plan, image_path, decoded.png, record)
    if error is None:
        record["output"] = {
            "file": image_path.name,
            "bytes": image_path.stat().st_size,
            "sha256": sha256(image_path),
        }
    generate.finish(record, measured, error, prepared.role, run_path, deps)
    if error:
        image_path.unlink(missing_ok=True)
        logger.error(
            "%s %s seed %d failed: %s", plan.arm_name, plan.clip, plan.seed, error
        )
        return generate.FAILED
    logger.info(
        "%s%s %s seed %d: %.1f s wall, %s Wh, GPU mean %s W, peak memory %s GiB -> %s",
        "probe " if probe else "",
        plan.arm_name,
        plan.clip,
        plan.seed,
        measured.wall_s,
        record["energy_wh"],
        record["gpu_power"]["mean_w"],
        record["memory"]["peak_used_gib"],
        image_path,
    )
    return 0
