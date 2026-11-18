"""Generate one video-gen eval clip from a vLLM-Omni server and record it (#1406, #1407).

    generate.py --arm h3-t2v --clip dog-a --seed 1 --server http://HOST:8040
    generate.py --arm wan-i2v --clip dog-b --seed 2 --server http://HOST:8041 --probe

One request to `POST /v1/videos/sync` (multipart form). The prompt comes from
`benchmarks/videogen/prompts/v1.json`, and every other request field comes
from `benchmarks/videogen/arms-v1.json`, so the same arm, clip and seed make
the same request every time. The request names the model (`model` = the
arm's `served_model_name`), and the script reads `/v1/models` first and
refuses a server that serves another model. An I2V arm attaches the clip's
first frame from `~/bench-logs/videogen/frames/<clip>/first.png` (made by
`frames.py`), and the script refuses a frame whose sha256 is not the one
`refs-v1.json` records.

It writes `clip.mp4` and `run.json`. `run.json` holds the arm, the model and
revision, the server block from arms-v1.json (served name, argv, env,
vllm-omni version), every field sent, the seed, start and end
(America/New_York, with offset), wall seconds, outlet energy in Wh, mean and
peak GPU power, peak memory, what ffprobe found in the clip, the node role,
the output sha256 and the HTTP status. ffprobe must find the expected frame
count, size and fps (and, for H3, an audio stream), or the run fails.

On a failure it writes `run.json` with the error, leaves no `clip.mp4`, and
exits 1. It refuses (exit 2, nothing written) an output inside a git work
tree, a scored run from a draft arms file, an existing output without
`--overwrite`, a wrong first frame, a server that serves another model, and
a GPU that does not fall to the idle floor within 120 s of the request.

**Power and memory.** While the request runs, a thread reads the server
node's GPU power from its DCGM exporter (:9400) and its MemAvailable from its
node_exporter (:9100) once a second, with the readers in
`benchmarks/agent/remote.py`. Peak memory is MemTotal minus the lowest
MemAvailable: the whole node, which is what the repo's memory gates read. The
outlet energy comes from `outlet.py` after the request ends.

`--probe` runs one request into `~/bench-logs/videogen/probe/` and logs the
timing. A probe is not a scored clip, and it may run from a draft arms file.

**Image arms (#1424).** An arm in `image-arms-v1.json` or
`edit-arms-v1.json` makes one PNG (`image.png`) instead of a clip, through
`imagegen.py`; `--clip` then names an image prompt (`--prompt` is the same
flag). The checks, the GPU settle, the samplers and the outlet energy are
the same code (`prepare`, `measured_post`, `finish`).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import http.client
import json
import logging
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol
from zoneinfo import ZoneInfo

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "benchmarks" / "agent"))

import heartbeat
import remote
from videogen import outlet
from videogen.common import (
    DEFAULT_ROOT,
    EDIT_ARMS_FILE,
    IMAGE_ARMS_FILE,
    IMAGE_INPUTS_ROOT,
    FfmpegError,
    InsideRepo,
    Refused,
    file_url,
    prompt_name,
    refuse_inside_repo,
    run,
    sha256,
)

import logs

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
ARMS_FILE = REPO / "benchmarks" / "videogen" / "arms-v1.json"
PROMPTS_FILE = REPO / "benchmarks" / "videogen" / "prompts" / "v1.json"
REFS_FILE = REPO / "benchmarks" / "videogen" / "refs-v1.json"
#: Every arms file. An arm name is unique across them (a test checks), so
#: `--arm` alone picks the file.
ARMS_FILES = (ARMS_FILE, IMAGE_ARMS_FILE, EDIT_ARMS_FILE)
ENDPOINT = "/v1/videos/sync"

#: Two hours. A 50-step H3 clip at 1280x704 can take over an hour on one
#: Spark. The server has its own limit, VLLM_OMNI_VIDEO_SYNC_TIMEOUT (600 s
#: by default), so the server block in arms-v1.json sets it to 7200 too.
TIMEOUT_S = 7200.0
#: Seconds between GPU-power and memory samples.
SAMPLE_S = 1.0
#: At or below this GPU power a GB10 is idle (scripts/heartbeat.py). A
#: request that starts above it shares the GPU, and its time and energy
#: would measure two jobs.
IDLE_W = heartbeat.IDLE_FLOOR_W["gb10"]
#: How long to wait for the GPU to fall to the idle floor before refusing.
#: When one clip ends, batch.py starts the next in the same second, and the
#: GPU still reads its working draw (40.1 W on 2026-11-18 at 00:18:11, which
#: stopped the H3 arm after 2 of 9 clips). It falls to idle within seconds.
#: A GPU still above the floor after 2 minutes is running another job.
SETTLE_MAX_S = 120.0
#: Seconds between GPU-power reads while it settles.
SETTLE_POLL_S = 1.0
#: run.json keeps at most this many bytes of an error.
ERROR_BYTES = 500

#: The form fields this script may send. Each is a field of
#: `_parse_video_form` in vllm-omni 0.32.0
#: (vllm_omni/entrypoints/openai/video/generation/helpers.py:1002). An arms
#: file that names anything else is refused: FastAPI drops an unknown form
#: field without a word, so a typo would change nothing and look fine.
FORM_FIELDS = frozenset(
    {
        "prompt",
        "input_reference",
        "model",
        "seconds",
        "size",
        "width",
        "height",
        "num_frames",
        "fps",
        "aspect_ratio",
        "short_edge",
        "num_outputs_per_prompt",
        "quality",
        "num_inference_steps",
        "guidance_scale",
        "guidance_scale_2",
        "boundary_ratio",
        "flow_shift",
        "true_cfg_scale",
        "seed",
        "negative_prompt",
        "extra_params",
    }
)
#: Set per request, never in an arm's form.
PER_REQUEST = ("prompt", "seed", "extra_params", "input_reference", "model")
#: The extra_params keys H3 reads (encoder_processing.py:172 and :225 in
#: 0.32.0, and the pipeline's audio_flow_shift). The server ignores others.
EXTRA_PARAMS = frozenset({"task", "duration", "audio_flow_shift"})
#: Server flags no server block may carry. A boundary ratio set at startup
#: overrides the one in each request (pipeline_wan2_2.py:676), so the two Wan
#: arms would silently run at one value.
FORBIDDEN_SERVER_FLAGS = ("--boundary-ratio", "--boundary_ratio")

#: The response headers the sync endpoint sets, kept in run.json. Starlette
#: sends header names in lower case, so both sides are lower case.
SERVER_HEADERS = (
    "x-request-id",
    "x-model",
    "x-inference-time-s",
    "x-stage-durations",
    "x-peak-memory-mb",
)

#: Exit codes: refused before any request, and a request that failed.
REFUSED, FAILED = 2, 1


# ---------------------------------------------------------------- config ----


def load_json(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise TypeError(f"{path} does not hold a JSON object")
    return data


def forbidden_flags(argv: Sequence[str]) -> list[str]:
    """The items of `argv` that set a forbidden server flag."""
    return [
        item
        for item in argv
        for flag in FORBIDDEN_SERVER_FLAGS
        if item == flag or item.startswith(flag + "=")
    ]


def media_of(arms: Mapping[str, Any]) -> str:
    """`video` or `image`. The video arms file predates the key."""
    return str(arms.get("media") or "video")


def find_arms_file(
    arm: str, files: Sequence[pathlib.Path] = ARMS_FILES
) -> pathlib.Path:
    """The arms file that defines `arm`. Raise Refused if none does."""
    for path in files:
        if arm in load_json(path).get("arms", {}):
            return path
    known = sorted(a for path in files for a in load_json(path).get("arms", {}))
    raise Refused(f"no arm {arm!r}; the arms are {known}")


def all_servers(
    files: Sequence[pathlib.Path] = ARMS_FILES,
) -> dict[str, tuple[dict[str, Any], str]]:
    """Server name -> (its block, the file's media), over every arms file.

    One server may serve arms in two files (FLUX.2-klein makes images and
    edits). Its blocks must then be identical, or the command would depend
    on which file was read first: that raises ValueError.
    """
    found: dict[str, tuple[dict[str, Any], str]] = {}
    for path in files:
        arms = load_json(path)
        for name, block in arms.get("servers", {}).items():
            if name in found and found[name][0] != block:
                raise ValueError(f"server {name}: two arms files give different blocks")
            found[name] = (block, media_of(arms))
    return found


def check_server(name: str, block: Mapping[str, Any], media: str = "video") -> None:
    """Raise ValueError if a server block is incomplete or unsafe.

    Only a video server needs VLLM_OMNI_VIDEO_SYNC_TIMEOUT: the image
    endpoints have no server-side sync timeout.
    """
    for key in ("node", "port", "served_model_name", "vllm_omni", "env", "scope"):
        if key not in block:
            raise ValueError(f"server {name}: no {key}")
    argv = list(block["argv"])
    bad = forbidden_flags(argv)
    if bad:
        raise ValueError(f"server {name}: argv sets {bad}; a request sets it")
    try:
        named = argv[argv.index("--served-model-name") + 1]
    except (ValueError, IndexError):
        named = None
    if named != block["served_model_name"]:
        raise ValueError(f"server {name}: argv must pass --served-model-name")
    if media == "video" and block["env"].get("VLLM_OMNI_VIDEO_SYNC_TIMEOUT") != str(
        int(TIMEOUT_S)
    ):
        raise ValueError(f"server {name}: VLLM_OMNI_VIDEO_SYNC_TIMEOUT must be 7200")
    if block["node"] not in ("head", "worker"):
        raise ValueError(f"server {name}: node must be head or worker")


def check_arms(arms: Mapping[str, Any]) -> None:
    """Raise ValueError if the arms file names a field the server ignores,
    or a server block is unsafe."""
    for name, block in arms["servers"].items():
        check_server(name, block)
    for name, arm in arms["arms"].items():
        unknown = set(arm["form"]) - FORM_FIELDS
        if unknown:
            raise ValueError(f"arm {name}: unknown form field(s) {sorted(unknown)}")
        for reserved in PER_REQUEST:
            if reserved in arm["form"]:
                raise ValueError(f"arm {name}: {reserved} is set per request")
        extra = set(arm.get("extra_params") or {}) - EXTRA_PARAMS
        if extra:
            raise ValueError(f"arm {name}: unknown extra_params {sorted(extra)}")
        if arm["mode"] not in ("t2v", "i2v"):
            raise ValueError(f"arm {name}: mode must be t2v or i2v")
        if (arm["mode"] == "i2v") != ("input_reference" in arm):
            raise ValueError(f"arm {name}: an i2v arm, and only one, has an image")
        if arm["server"] not in arms["servers"]:
            raise ValueError(f"arm {name}: no server block {arm['server']!r}")
        for key in ("frames", "audio"):
            if key not in arm["expect"]:
                raise ValueError(f"arm {name}: expect has no {key}")


def prompt_for(prompts: Mapping[str, Any], clip: str) -> tuple[str, str]:
    """(prompt id, prompt text) for a clip name such as `dog-a`."""
    for entry in prompts["clips"]:
        if prompt_name(entry) == clip:
            return str(entry["id"]), str(entry["prompt"])
    raise Refused(f"no prompt for clip {clip!r} in {PROMPTS_FILE.name}")


def first_frame_sha(refs: Mapping[str, Any], clip: str) -> str:
    """The sha256 that refs-v1.json records for the clip's first.png."""
    for entry in refs["clips"]:
        if entry["id"] == clip:
            return str(entry["frames_sha256"]["first.png"])
    raise Refused(f"no reference frames for clip {clip!r} in {REFS_FILE.name}")


def build_fields(
    arm: Mapping[str, Any],
    prompt: str,
    seed: int,
    model: str,
    *,
    fallback: bool = False,
) -> dict[str, str]:
    """The form fields as sent, every value a string.

    `fallback` swaps in the arm's fallback size. `extra_params` goes as
    compact, key-sorted JSON so the same arm gives the same bytes.
    """
    form = dict(arm["form"])
    if fallback:
        form.update(arm["fallback"])
    fields = {"prompt": prompt, "model": model}
    fields.update({k: str(v) for k, v in form.items()})
    fields["seed"] = str(seed)
    if arm.get("extra_params"):
        fields["extra_params"] = json.dumps(
            arm["extra_params"], sort_keys=True, separators=(",", ":")
        )
    return fields


@dataclasses.dataclass(frozen=True)
class Image:
    """The first frame an I2V request attaches."""

    name: str
    data: bytes

    def record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "bytes": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
        }


def request_record(fields: Mapping[str, str], image: Image | None) -> dict[str, Any]:
    """What run.json keeps of the request. batch.py compares it to decide
    whether an earlier run made the same request."""
    out: dict[str, Any] = {"fields": dict(fields)}
    out["input_reference"] = image.record() if image else None
    return out


def load_image(
    arm: Mapping[str, Any],
    clip: str,
    frames_root: pathlib.Path,
    refs: Mapping[str, Any],
) -> Image | None:
    """The first frame for an I2V arm, checked against refs-v1.json; or None."""
    if arm["mode"] != "i2v":
        return None
    path = frames_root / clip / str(arm["input_reference"])
    if not path.is_file():
        raise Refused(f"{path} does not exist; make it with frames.py first")
    data = path.read_bytes()
    want = first_frame_sha(refs, clip)
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        raise Refused(
            f"{path} has sha256 {got}, but refs-v1.json records {want}. "
            "Rerun frames.py on the reference clip."
        )
    return Image(path.name, data)


def server_record(arm: Mapping[str, Any], server: Mapping[str, Any]) -> dict[str, Any]:
    """An arm's server block as run.json records it; batch.py compares it."""
    return {
        "name": arm["server"],
        "served_model_name": server["served_model_name"],
        "vllm_omni": server["vllm_omni"],
        "argv": list(server["argv"]),
        "env": dict(server["env"]),
        "scope": dict(server["scope"]),
    }


@dataclasses.dataclass(frozen=True)
class Plan:
    """Everything one request needs, settled before it is sent."""

    arm_name: str
    arm: Mapping[str, Any]
    arms_status: str
    clip: str
    prompt_id: str
    seed: int
    fields: dict[str, str]
    image: Image | None
    fallback: bool
    server: Mapping[str, Any]
    arms_file: pathlib.Path = ARMS_FILE

    @property
    def output_name(self) -> str:
        return "clip.mp4"

    def request(self) -> dict[str, Any]:
        return request_record(self.fields, self.image)

    def server_config(self) -> dict[str, Any]:
        """The server block as run.json records it; batch.py compares it."""
        return server_record(self.arm, self.server)

    def expect(self) -> dict[str, Any]:
        """What ffprobe must find in the clip."""
        return {
            "frames": int(self.arm["expect"]["frames"]),
            "width": int(self.fields["width"]),
            "height": int(self.fields["height"]),
            "fps": float(self.fields["fps"]),
            "audio": bool(self.arm["expect"]["audio"]),
        }


class AnyPlan(Protocol):
    """What the shared run code needs of a video Plan or an image plan."""

    @property
    def arm_name(self) -> str: ...

    @property
    def arm(self) -> Mapping[str, Any]: ...

    @property
    def arms_status(self) -> str: ...

    @property
    def arms_file(self) -> pathlib.Path: ...

    @property
    def clip(self) -> str: ...

    @property
    def seed(self) -> int: ...

    @property
    def server(self) -> Mapping[str, Any]: ...

    @property
    def output_name(self) -> str: ...

    def request(self) -> dict[str, Any]: ...

    def server_config(self) -> dict[str, Any]: ...


def plan_for(
    arm_name: str,
    clip: str,
    seed: int,
    *,
    frames_root: pathlib.Path,
    fallback: bool = False,
    arms_file: pathlib.Path | None = None,
    inputs_root: pathlib.Path = IMAGE_INPUTS_ROOT,
) -> AnyPlan:
    """The plan for any arm: a video `Plan`, or an `imagegen.ImagePlan`.

    `arms_file` None finds the file that defines the arm. `fallback` and
    `frames_root` apply only to a video arm; `inputs_root` only to an edit
    arm (#1424).
    """
    if arms_file is None:
        arms_file = find_arms_file(arm_name)
    if media_of(load_json(arms_file)) == "image":
        from videogen import imagegen  # imagegen imports this module

        return imagegen.make_plan(
            arm_name, clip, seed, arms_file=arms_file, inputs_root=inputs_root
        )
    return make_plan(
        arm_name,
        clip,
        seed,
        frames_root=frames_root,
        fallback=fallback,
        arms_file=arms_file,
    )


def make_plan(
    arm_name: str,
    clip: str,
    seed: int,
    *,
    frames_root: pathlib.Path,
    fallback: bool = False,
    arms_file: pathlib.Path = ARMS_FILE,
) -> Plan:
    """Read the arms, prompts and refs files and build a video request."""
    arms = load_json(arms_file)
    if media_of(arms) != "video":
        raise Refused(f"{arms_file.name} holds image arms; use plan_for")
    check_arms(arms)
    if arm_name not in arms["arms"]:
        raise Refused(f"no arm {arm_name!r}; the arms are {sorted(arms['arms'])}")
    if clip not in arms["clips"]:
        raise Refused(f"no clip {clip!r}; the clips are {arms['clips']}")
    arm = arms["arms"][arm_name]
    server = arms["servers"][arm["server"]]
    prompt_id, prompt = prompt_for(load_json(PROMPTS_FILE), clip)
    image = load_image(arm, clip, frames_root, load_json(REFS_FILE))
    return Plan(
        arm_name=arm_name,
        arm=arm,
        arms_status=str(arms["status"]),
        clip=clip,
        prompt_id=prompt_id,
        seed=seed,
        fields=build_fields(
            arm, prompt, seed, str(server["served_model_name"]), fallback=fallback
        ),
        image=image,
        fallback=fallback,
        server=server,
        arms_file=arms_file,
    )


# ------------------------------------------------------------------ http ----


#: One file part of a form: (field name, file name, content type, bytes).
FilePart = tuple[str, str, str, bytes]


def encode_parts(
    fields: Mapping[str, str], files: Sequence[FilePart], boundary: str
) -> bytes:
    """A multipart/form-data body: the fields, then the files in order."""
    out: list[bytes] = []
    for name, value in fields.items():
        out.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            f"\r\n\r\n".encode()
        )
        out.append(value.encode() + b"\r\n")
    for name, filename, content_type, data in files:
        out.append(
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n".encode()
        )
        out.append(data + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out)


def encode_multipart(
    fields: Mapping[str, str], image: Image | None, boundary: str
) -> bytes:
    """A multipart/form-data body: the fields, then the image if any."""
    files: list[FilePart] = []
    if image is not None:
        files.append(("input_reference", image.name, "image/png", image.data))
    return encode_parts(fields, files, boundary)


@dataclasses.dataclass(frozen=True)
class Reply:
    status: int
    headers: dict[str, str]
    body: bytes


#: (url, body, content type, timeout) -> Reply. Raises OSError or
#: http.client.HTTPException when no whole answer arrives.
Post = Callable[[str, bytes, str, float], Reply]
#: (url, timeout) -> response text, or None when it cannot be read.
Get = Callable[[str, float], str | None]


def lower_headers(items: Any) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in items}


def urllib_post(url: str, body: bytes, content_type: str, timeout: float) -> Reply:
    """POST with urllib. An HTTP error status is a Reply, not an exception."""
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Reply(resp.status, lower_headers(resp.headers.items()), resp.read())
    except urllib.error.HTTPError as err:
        return Reply(err.code, lower_headers(err.headers.items()), err.read())


def urllib_get(url: str, timeout: float) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            text: str = resp.read().decode("utf-8", "replace")
            return text
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        return None


def served_models(server: str, get: Get) -> list[str] | None:
    """The model ids `/v1/models` lists, or None if it cannot be read."""
    text = get(server.rstrip("/") + "/v1/models", 10.0)
    try:
        return [str(m["id"]) for m in json.loads(text or "")["data"]]
    except (ValueError, KeyError, TypeError):
        return None


def vllm_version(server: str, get: Get) -> str | None:
    """`/version`: the vLLM under vllm-omni (vllm-omni serves no version)."""
    try:
        return str(
            json.loads(get(server.rstrip("/") + "/version", 10.0) or "")["version"]
        )
    except (ValueError, KeyError, TypeError):
        return None


def is_mp4(body: bytes) -> bool:
    """An MP4 opens with a box whose type, at bytes 4..8, is `ftyp`."""
    return len(body) > 8 and body[4:8] == b"ftyp"


def clip_error(text: str) -> str:
    """At most ERROR_BYTES of UTF-8, cut on a character boundary."""
    return text.encode()[:ERROR_BYTES].decode("utf-8", "ignore")


# --------------------------------------------------------------- ffprobe ----


def parse_ffprobe(text: str) -> dict[str, Any]:
    """Frames, size, fps and audio from `ffprobe -count_frames -of json`."""
    streams = json.loads(text).get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise FfmpegError("the clip has no video stream")
    num, _, den = str(video.get("avg_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {
        "frames": int(video.get("nb_read_frames", 0)),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps": round(fps, 3),
        "audio": any(s.get("codec_type") == "audio" for s in streams),
        "video_codec": video.get("codec_name"),
    }


def ffprobe_clip(path: pathlib.Path) -> dict[str, Any]:
    """Decode the clip once with ffprobe and count its frames."""
    return parse_ffprobe(
        run(
            [
                *("ffprobe", "-v", "error", "-count_frames"),
                *(
                    "-show_entries",
                    (
                        "stream=codec_type,codec_name,width,height,"
                        "avg_frame_rate,nb_read_frames"
                    ),
                ),
                *("-of", "json", file_url(path)),
            ]
        )
    )


def video_mismatches(found: Mapping[str, Any], expect: Mapping[str, Any]) -> list[str]:
    """Each way the clip differs from what the arm asked for."""
    bad = [
        f"{key} {found.get(key)} (expected {expect[key]})"
        for key in ("frames", "width", "height")
        if found.get(key) != expect[key]
    ]
    if abs(float(found.get("fps") or 0) - expect["fps"]) > 1e-3:
        bad.append(f"fps {found.get('fps')} (expected {expect['fps']:g})")
    if expect["audio"] and not found.get("audio"):
        bad.append("no audio stream (expected one)")
    return bad


# ---------------------------------------------------------------- sample ----


class Sampler:
    """Calls `read` every `interval` seconds on a thread, from start to stop.

    Each sample is (monotonic seconds, value). A None value is kept out.
    """

    def __init__(self, read: Callable[[], Any], interval: float = SAMPLE_S) -> None:
        self.read = read
        self.interval = interval
        self.samples: list[tuple[float, Any]] = []
        self.misses = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while True:
            try:
                value = self.read()
            except Exception:  # a sampler must never kill the request
                logger.debug("sample failed", exc_info=True)
                value = None
            if value is None:
                self.misses += 1
            else:
                self.samples.append((time.monotonic(), value))
            if self._stop.wait(self.interval):
                return

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)


def gpu_stats(watts: Sequence[float]) -> dict[str, Any]:
    """Mean and peak of the GPU power samples, and how many there were."""
    if not watts:
        return {"mean_w": None, "peak_w": None, "samples": 0}
    return {
        "mean_w": round(sum(watts) / len(watts), 2),
        "peak_w": round(max(watts), 2),
        "samples": len(watts),
    }


def mem_stats(readings: Sequence[Mapping[str, float]]) -> dict[str, Any]:
    """Peak memory in use on the node: MemTotal minus the lowest MemAvailable."""
    avail = [r["MemAvailable"] for r in readings if "MemAvailable" in r]
    totals = [r["MemTotal"] for r in readings if "MemTotal" in r]
    if not avail or not totals:
        return {"peak_used_gib": None, "min_available_gib": None, "total_gib": None}
    return {
        "peak_used_gib": round(max(totals) - min(avail), 2),
        "min_available_gib": round(min(avail), 2),
        "total_gib": round(max(totals), 2),
    }


def read_meminfo(host: str, timeout: float = 5.0) -> dict[str, float] | None:
    """MemTotal and MemAvailable in GiB from the node's node_exporter.

    Not `remote.server_meminfo`: that logs a warning on every miss, which at
    one read a second would be thousands of lines. The parse is remote's.
    """
    text = urllib_get(remote.node_exporter_url(host), timeout)
    if text is None:
        return None
    got = remote.parse_node_meminfo(text)
    return got or None


# ------------------------------------------------------------------- run ----


def ny_now() -> datetime.datetime:
    return datetime.datetime.now(NY)


def iso(t: datetime.datetime) -> str:
    """ISO 8601 with offset, America/New_York: the repo's one timestamp shape."""
    return t.astimezone(NY).strftime("%Y-%m-%dT%H:%M:%S%z")


def client_machine() -> str | None:
    """The machine running this script, named by hardware_id.py."""
    try:
        import hardware_id

        return hardware_id.directory_name(*hardware_id.facts_for_this_machine())
    except (Exception, SystemExit):
        logger.warning("hardware_id could not name this machine", exc_info=True)
        return None


@dataclasses.dataclass
class Deps:
    """Everything that reaches outside the process. Tests replace them."""

    post: Post = urllib_post
    get: Get = urllib_get
    gpu: Callable[[str], float | None] = remote.gpu_watts
    mem: Callable[[str], dict[str, float] | None] = read_meminfo
    flux: outlet.Flux = outlet.gcx_flux
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], datetime.datetime] = ny_now
    machine: Callable[[], str | None] = client_machine
    ffprobe: Callable[[pathlib.Path], dict[str, Any]] = ffprobe_clip
    sample_s: float = SAMPLE_S


def write_json(path: pathlib.Path, data: Mapping[str, Any]) -> None:
    """Write JSON through a temp file and a rename, so no reader sees half."""
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def write_partial(path: pathlib.Path, body: bytes) -> pathlib.Path:
    """Write the clip to `<path>.partial` and return that path."""
    tmp = path.with_name(path.name + ".partial")
    with tmp.open("wb") as fh:
        fh.write(body)
        fh.flush()
        os.fsync(fh.fileno())
    return tmp


def default_out(
    arm: str, clip: str, seed: int, probe: bool, stamp: str
) -> pathlib.Path:
    if probe:
        return DEFAULT_ROOT / "probe" / arm / f"{clip}-s{seed}-{stamp}"
    return DEFAULT_ROOT / "out" / arm / f"{clip}-s{seed}"


def preflight(
    plan: AnyPlan,
    server: str,
    host: str,
    role: str,
    deps: Deps,
    *,
    settle: bool = True,
) -> dict[str, Any]:
    """Check the server and the GPU before the request. Raise Refused.

    Returns what run.json records of them. `settle` False skips the wait
    for the idle floor: inside an image block (batch.py) the requests run
    back to back, and the batch settled once before the block.
    """
    want = str(plan.server["served_model_name"])
    ids = served_models(server, deps.get)
    if ids is None:
        raise Refused(f"cannot read {server}/v1/models; is the server up?")
    if want not in ids:
        raise Refused(
            f"{server} serves {ids}, not {want}. Start it with "
            f"scripts/videogen/serve.py --model {plan.arm['server']}."
        )
    settled = (
        wait_for_idle(host, role, deps)
        if settle
        else {
            "gpu_w_before": None,
            "settle_s": None,
            "settle_readings": [],
            "settle_skipped": "inside a block: batch.py settled once before it",
        }
    )
    return {
        "served_model": want,
        "vllm_version": vllm_version(server, deps.get),
        **settled,
    }


def wait_for_idle(host: str, role: str, deps: Deps) -> dict[str, Any]:
    """Poll the GPU power once a second until it is at or below the idle
    floor, for at most SETTLE_MAX_S. Raise Refused if it never gets there.

    Returns `gpu_w_before` (the reading that passed, or None if the power
    cannot be read), `settle_s` (how long it waited) and `settle_readings`
    ([seconds since the first read, watts] for every read).
    """
    readings: list[list[float | None]] = []
    t0 = deps.clock()
    while True:
        watts = deps.gpu(host)
        elapsed = round(deps.clock() - t0, 1)
        readings.append([elapsed, watts])
        if watts is None:
            logger.warning(
                "could not read the GPU power on %s before the request", host
            )
            break
        if watts <= IDLE_W:
            if elapsed > 0:
                logger.info("the GPU settled to %.1f W in %.1f s", watts, elapsed)
            break
        if elapsed >= SETTLE_MAX_S:
            raise Refused(
                f"the GPU on the {role} still draws {watts:.1f} W after "
                f"{elapsed:g} s, above the {IDLE_W:g} W idle floor: something "
                "else is running on it, and this clip's time and energy would "
                "include it"
            )
        deps.sleep(SETTLE_POLL_S)
    return {"gpu_w_before": watts, "settle_s": elapsed, "settle_readings": readings}


@dataclasses.dataclass(frozen=True)
class Prepared:
    """What the checks before a request settled: where to read, and what."""

    role: str
    host: str
    server_seen: dict[str, Any]


def prepare(
    plan: AnyPlan,
    server: str,
    out: pathlib.Path,
    *,
    probe: bool,
    node: str | None,
    metrics_host: str | None,
    allow_draft: bool,
    overwrite: bool,
    deps: Deps,
    settle: bool = True,
) -> Prepared:
    """Every check before a request, for a clip or an image. Raise Refused.

    Then make `out` and clear its old output and run.json, so a failure
    later cannot leave an old output beside a new run.json that describes a
    different request.
    """
    try:
        refuse_inside_repo(out)
    except InsideRepo as err:
        raise Refused(str(err)) from err
    if plan.arms_status != "locked" and not probe and not allow_draft:
        raise Refused(
            f"the arms file is {plan.arms_status!r}, not 'locked': a scored run "
            "needs fixed settings. Run --probe, lock the file, or pass --allow-draft."
        )
    output, run_path = out / plan.output_name, out / "run.json"
    if (output.exists() or run_path.exists()) and not overwrite:
        raise Refused(f"{out} already holds a run; pass --overwrite to replace it")
    role = node or str(plan.server["node"])
    host = metrics_host or urllib.parse.urlsplit(server).hostname or ""
    server_seen = preflight(plan, server, host, role, deps, settle=settle)
    out.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    run_path.unlink(missing_ok=True)
    return Prepared(role=role, host=host, server_seen=server_seen)


def base_record(
    plan: AnyPlan,
    prepared: Prepared,
    *,
    probe: bool,
    prompts_file: pathlib.Path,
    endpoint: str,
    deps: Deps,
) -> dict[str, Any]:
    """The run.json fields that a clip and an image share, before the request."""
    return {
        "schema": 2,
        "probe": probe,
        "arm": plan.arm_name,
        "mode": plan.arm["mode"],
        "clip": plan.clip,
        "arms_file": plan.arms_file.name,
        "arms_status": plan.arms_status,
        "arms_sha256": sha256(plan.arms_file),
        "prompts_sha256": sha256(prompts_file),
        "model": plan.arm["model"],
        "revision": plan.arm["revision"],
        "seed": plan.seed,
        "request": plan.request(),
        "server_config": plan.server_config(),
        "server": prepared.server_seen,
        "endpoint": endpoint,
        "node": {"role": prepared.role},
        "client_machine": deps.machine(),
    }


@dataclasses.dataclass(frozen=True)
class Measured:
    """One timed request: the reply or the error, and what was read meanwhile."""

    reply: Reply | None
    error: str | None
    start: datetime.datetime
    end: datetime.datetime
    wall_s: float
    record: dict[str, Any]
    #: Each GPU power reading as (seconds since the request started, watts).
    gpu_readings: tuple[tuple[float, float], ...] = ()


def measured_post(
    url: str,
    body: bytes,
    content_type: str,
    timeout: float,
    host: str,
    deps: Deps,
) -> Measured:
    """POST once while two threads read the GPU power and the node memory.

    The record holds start and end (America/New_York), wall seconds, the HTTP
    status, the server headers, the GPU power, the peak memory and the
    sample misses: the run.json fields of every request, clip or image.
    """
    gpu = Sampler(lambda: deps.gpu(host), deps.sample_s)
    mem = Sampler(lambda: deps.mem(host), deps.sample_s)
    start = deps.now()
    t0 = time.monotonic()
    gpu.start()
    mem.start()
    reply: Reply | None = None
    error: str | None = None
    try:
        reply = deps.post(url, body, content_type, timeout)
    except (OSError, ValueError, http.client.HTTPException) as err:
        error = f"request failed: {type(err).__name__}: {err}"
    finally:
        wall = time.monotonic() - t0
        end = deps.now()
        gpu.stop()
        mem.stop()
    record = {
        "start": iso(start),
        "end": iso(end),
        "wall_s": round(wall, 3),
        "http_status": reply.status if reply else None,
        "server_headers": {
            h: reply.headers[h]
            for h in SERVER_HEADERS
            if reply is not None and h in reply.headers
        },
        "gpu_power": gpu_stats([w for _, w in gpu.samples]),
        "memory": mem_stats([m for _, m in mem.samples]),
        "sample_misses": {"gpu": gpu.misses, "memory": mem.misses},
    }
    readings = tuple((round(t - t0, 3), float(w)) for t, w in gpu.samples)
    return Measured(reply, error, start, end, wall, record, readings)


#: A per-request energy needs at least this many outlet readings inside the
#: window, and a window at least this long. The plug reports about every
#: 5 s, so a 3 s image has one reading or none: its "complete" Wh is one
#: held value times the window, not a measurement (#1424 review). Image arms
#: get a block energy instead (batch.py, `out/<arm>/block.json`).
MIN_READINGS = 3
MIN_WINDOW_S = 30.0


def request_energy(reading: Mapping[str, Any], seconds: float) -> float | None:
    """The Wh of one window, or None unless it is complete, holds at least
    MIN_READINGS readings and lasts at least MIN_WINDOW_S."""
    if not reading.get("complete") or seconds < MIN_WINDOW_S:
        return None
    if int(reading.get("readings") or 0) < MIN_READINGS:
        return None
    value = reading.get("energy_wh")
    return float(value) if isinstance(value, (int, float)) else None


def apply_outlet(
    record: dict[str, Any],
    reading: dict[str, Any],
    start: datetime.datetime,
    end: datetime.datetime,
) -> None:
    """Put an outlet reading in a run record: `outlet` always (with its
    `readings` count), `energy_wh` only when `request_energy` allows it."""
    record["outlet"] = reading
    record["energy_wh"] = request_energy(reading, (end - start).total_seconds())


def finish(
    record: dict[str, Any],
    measured: Measured,
    error: str | None,
    role: str,
    run_path: pathlib.Path,
    deps: Deps,
    *,
    read_outlet: bool = True,
) -> bool:
    """Add the outlet energy and the status, and write run.json.

    Returns whether `energy_wh` is set. A partial or too short reading stays
    under `outlet`; only a full one is the top-level `energy_wh`.
    `read_outlet` False defers the reading: inside an image block, a read
    here would wait about 15 s for InfluxDB, and the next request must start
    at once. batch.py reads it after the block (`backfill_outlet`).
    """
    if read_outlet:
        reading = outlet.energy(
            outlet.entity(role),
            measured.start,
            measured.end,
            flux=deps.flux,
            sleep=deps.sleep,
        )
        apply_outlet(record, reading, measured.start, measured.end)
    else:
        record["outlet"] = {"deferred": "batch.py reads it after the block"}
        record["energy_wh"] = None
    record["status"] = "failed" if error else "ok"
    record["error"] = clip_error(error) if error else None
    write_json(run_path, record)
    has_energy = record["energy_wh"] is not None
    if read_outlet and not error and not has_energy:
        logger.warning(
            "the outlet reading is incomplete or too short (%s readings, max gap "
            "%s s, %s); energy_wh is None",
            record["outlet"]["readings"],
            record["outlet"]["max_gap_s"],
            record["outlet"]["error"] or "see outlet in run.json",
        )
    return has_energy


def run_one(
    plan: Plan,
    server: str,
    out: pathlib.Path,
    *,
    probe: bool = False,
    node: str | None = None,
    metrics_host: str | None = None,
    allow_draft: bool = False,
    overwrite: bool = False,
    deps: Deps | None = None,
) -> int:
    """Make one clip. Returns 0, FAILED (run.json says why) or REFUSED."""
    deps = deps or Deps()
    try:
        prepared = prepare(
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
    except Refused as err:
        logger.error("%s", err)
        return REFUSED
    clip_path, run_path = out / plan.output_name, out / "run.json"
    record = base_record(
        plan,
        prepared,
        probe=probe,
        prompts_file=PROMPTS_FILE,
        endpoint=ENDPOINT,
        deps=deps,
    )
    record["prompt_id"] = plan.prompt_id
    record["size"] = "fallback" if plan.fallback else "primary"

    boundary = "videogen-" + uuid.uuid4().hex
    measured = measured_post(
        server.rstrip("/") + ENDPOINT,
        encode_multipart(plan.fields, plan.image, boundary),
        f"multipart/form-data; boundary={boundary}",
        TIMEOUT_S,
        prepared.host,
        deps,
    )
    record.update(measured.record)
    reply, error = measured.reply, measured.error
    if error is None and reply is not None:
        if reply.status != 200:
            text = reply.body[:2000].decode("utf-8", "replace")
            error = f"HTTP {reply.status}: {text}"
        elif not is_mp4(reply.body):
            error = f"the reply is not an MP4 ({len(reply.body)} bytes)"

    if error is None and reply is not None:
        error = keep_clip(plan, clip_path, reply.body, record, deps)
    if error is None:
        record["output"] = {
            "file": clip_path.name,
            "bytes": clip_path.stat().st_size,
            "sha256": sha256(clip_path),
        }
    finish(record, measured, error, prepared.role, run_path, deps)

    if error:
        clip_path.unlink(missing_ok=True)
        logger.error(
            "%s %s seed %d failed: %s", plan.arm_name, plan.clip, plan.seed, error
        )
        return FAILED
    logger.info(
        "%s%s %s seed %d: %.1f s wall (server %s s), %s Wh, GPU mean %s W, "
        "peak memory %s GiB -> %s",
        "probe " if probe else "",
        plan.arm_name,
        plan.clip,
        plan.seed,
        measured.wall_s,
        record["server_headers"].get("x-inference-time-s", "n/a"),
        record["energy_wh"],
        record["gpu_power"]["mean_w"],
        record["memory"]["peak_used_gib"],
        clip_path,
    )
    return 0


def run_plan(plan: AnyPlan, server: str, out: pathlib.Path, **kw: Any) -> int:
    """`run_one` for a video plan, `imagegen.run_one` for an image plan.

    `kw` goes through unchanged: probe, node, metrics_host, allow_draft,
    overwrite and deps; for an image plan also settle and read_outlet
    (batch.py's block).
    """
    from videogen import imagegen  # imagegen imports this module

    if isinstance(plan, imagegen.ImagePlan):
        return imagegen.run_one(plan, server, out, **kw)
    if isinstance(plan, Plan):
        return run_one(plan, server, out, **kw)
    raise TypeError(f"not a plan: {type(plan).__name__}")


def keep_clip(
    plan: Plan,
    clip_path: pathlib.Path,
    body: bytes,
    record: dict[str, Any],
    deps: Deps,
) -> str | None:
    """Write the clip to .partial, check it with ffprobe, then rename it.

    Returns an error, or None once clip.mp4 holds a checked clip. The
    .partial file never survives.
    """
    tmp: pathlib.Path | None = None
    try:
        tmp = write_partial(clip_path, body)
        found = deps.ffprobe(tmp)
        record["ffprobe"] = found
        record["expected"] = plan.expect()
        bad = video_mismatches(found, plan.expect())
        if bad:
            return "the clip is not what the arm asked for: " + "; ".join(bad)
        os.replace(tmp, clip_path)
        return None
    except (OSError, FfmpegError, ValueError, TypeError) as err:
        return f"could not write or probe the clip: {err}"
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------- cli ----


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--arm",
        required=True,
        help="an arm in arms-v1.json, image-arms-v1.json or edit-arms-v1.json",
    )
    p.add_argument(
        "--clip",
        "--prompt",
        dest="clip",
        required=True,
        help="a clip (dog-a) or an image prompt name (meme-text); the arms "
        "file's 'clips' lists them",
    )
    p.add_argument("--seed", required=True, type=int)
    p.add_argument("--server", required=True, help="http://HOST:PORT of vLLM-Omni")
    p.add_argument(
        "--out",
        type=pathlib.Path,
        help=f"output dir (default {DEFAULT_ROOT}/out/<arm>/<clip>-s<seed>/)",
    )
    p.add_argument("--probe", action="store_true", help="a timing probe, not scored")
    p.add_argument("--fallback", action="store_true", help="use the fallback size")
    p.add_argument(
        "--frames-root",
        type=pathlib.Path,
        default=DEFAULT_ROOT / "frames",
        help="where frames.py wrote <clip>/first.png",
    )
    p.add_argument(
        "--inputs-root",
        type=pathlib.Path,
        default=IMAGE_INPUTS_ROOT,
        help="where an edit arm's input images are",
    )
    p.add_argument("--node", choices=["head", "worker"], help="override the arm's node")
    p.add_argument(
        "--metrics-host",
        help="host of the node's exporters (default: the --server host)",
    )
    p.add_argument(
        "--allow-draft",
        action="store_true",
        help="allow a scored run from a draft arms file",
    )
    p.add_argument("--overwrite", action="store_true", help="replace an earlier run")
    return p


def main(argv: list[str] | None = None) -> int:
    logs.configure()
    args = build_parser().parse_args(argv)
    try:
        plan = plan_for(
            args.arm,
            args.clip,
            args.seed,
            frames_root=args.frames_root.expanduser(),
            fallback=args.fallback,
            inputs_root=args.inputs_root.expanduser(),
        )
    except (Refused, ValueError, TypeError, KeyError) as err:
        logger.error("%s", err)
        return REFUSED
    stamp = ny_now().strftime("%Y%m%dT%H%M%S")
    out = (
        args.out.expanduser()
        if args.out
        else default_out(args.arm, args.clip, args.seed, args.probe, stamp)
    )
    return run_plan(
        plan,
        args.server,
        out.resolve(),
        probe=args.probe,
        node=args.node,
        metrics_host=args.metrics_host,
        allow_draft=args.allow_draft,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    sys.exit(main())
