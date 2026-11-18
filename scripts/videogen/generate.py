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
from typing import Any
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
    FfmpegError,
    InsideRepo,
    file_url,
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


class Refused(Exception):
    """A check before the request failed. Nothing is written."""


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


def check_server(name: str, block: Mapping[str, Any]) -> None:
    """Raise ValueError if a server block is incomplete or unsafe."""
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
    if block["env"].get("VLLM_OMNI_VIDEO_SYNC_TIMEOUT") != str(int(TIMEOUT_S)):
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
        if pathlib.PurePath(entry["ref"]).stem == clip:
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

    def request(self) -> dict[str, Any]:
        return request_record(self.fields, self.image)

    def server_config(self) -> dict[str, Any]:
        """The server block as run.json records it; batch.py compares it."""
        return {
            "name": self.arm["server"],
            "served_model_name": self.server["served_model_name"],
            "vllm_omni": self.server["vllm_omni"],
            "argv": list(self.server["argv"]),
            "env": dict(self.server["env"]),
            "scope": dict(self.server["scope"]),
        }

    def expect(self) -> dict[str, Any]:
        """What ffprobe must find in the clip."""
        return {
            "frames": int(self.arm["expect"]["frames"]),
            "width": int(self.fields["width"]),
            "height": int(self.fields["height"]),
            "fps": float(self.fields["fps"]),
            "audio": bool(self.arm["expect"]["audio"]),
        }


def make_plan(
    arm_name: str,
    clip: str,
    seed: int,
    *,
    frames_root: pathlib.Path,
    fallback: bool = False,
    arms_file: pathlib.Path = ARMS_FILE,
) -> Plan:
    """Read the arms, prompts and refs files and build the request."""
    arms = load_json(arms_file)
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


def encode_multipart(
    fields: Mapping[str, str], image: Image | None, boundary: str
) -> bytes:
    """A multipart/form-data body: the fields, then the image if any."""
    out: list[bytes] = []
    for name, value in fields.items():
        out.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            f"\r\n\r\n".encode()
        )
        out.append(value.encode() + b"\r\n")
    if image is not None:
        out.append(
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="input_reference"; filename="{image.name}"\r\n'
            "Content-Type: image/png\r\n\r\n".encode()
        )
        out.append(image.data + b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out)


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
    plan: Plan, server: str, host: str, role: str, deps: Deps
) -> dict[str, Any]:
    """Check the server and the GPU before the request. Raise Refused.

    Returns what run.json records of them.
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
    settled = wait_for_idle(host, role, deps)
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
        refuse_inside_repo(out)
    except InsideRepo as err:
        logger.error("%s", err)
        return REFUSED
    if plan.arms_status != "locked" and not probe and not allow_draft:
        logger.error(
            "the arms file is %r, not 'locked': a scored run needs fixed settings. "
            "Run --probe, lock the file, or pass --allow-draft.",
            plan.arms_status,
        )
        return REFUSED
    clip_path, run_path = out / "clip.mp4", out / "run.json"
    if (clip_path.exists() or run_path.exists()) and not overwrite:
        logger.error("%s already holds a run; pass --overwrite to replace it", out)
        return REFUSED
    role = node or str(plan.server["node"])
    host = metrics_host or urllib.parse.urlsplit(server).hostname or ""
    try:
        server_seen = preflight(plan, server, host, role, deps)
    except Refused as err:
        logger.error("%s", err)
        return REFUSED

    out.mkdir(parents=True, exist_ok=True)
    # Clear the old pair first, so a failure below cannot leave an old clip
    # beside a new run.json that describes a different request.
    clip_path.unlink(missing_ok=True)
    run_path.unlink(missing_ok=True)

    record: dict[str, Any] = {
        "schema": 2,
        "probe": probe,
        "arm": plan.arm_name,
        "mode": plan.arm["mode"],
        "clip": plan.clip,
        "prompt_id": plan.prompt_id,
        "arms_file": plan.arms_file.name,
        "arms_status": plan.arms_status,
        "arms_sha256": sha256(plan.arms_file),
        "prompts_sha256": sha256(PROMPTS_FILE),
        "model": plan.arm["model"],
        "revision": plan.arm["revision"],
        "size": "fallback" if plan.fallback else "primary",
        "seed": plan.seed,
        "request": plan.request(),
        "server_config": plan.server_config(),
        "server": server_seen,
        "endpoint": ENDPOINT,
        "node": {"role": role},
        "client_machine": deps.machine(),
    }

    gpu = Sampler(lambda: deps.gpu(host), deps.sample_s)
    mem = Sampler(lambda: deps.mem(host), deps.sample_s)
    boundary = "videogen-" + uuid.uuid4().hex
    body = encode_multipart(plan.fields, plan.image, boundary)
    start = deps.now()
    t0 = time.monotonic()
    gpu.start()
    mem.start()
    reply: Reply | None = None
    error: str | None = None
    try:
        reply = deps.post(
            server.rstrip("/") + ENDPOINT,
            body,
            f"multipart/form-data; boundary={boundary}",
            TIMEOUT_S,
        )
    except (OSError, ValueError, http.client.HTTPException) as err:
        error = f"request failed: {type(err).__name__}: {err}"
    finally:
        wall = time.monotonic() - t0
        end = deps.now()
        gpu.stop()
        mem.stop()

    record.update(
        {
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
    )
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
    record["outlet"] = outlet.energy(
        outlet.entity(role), start, end, flux=deps.flux, sleep=deps.sleep
    )
    # A partial reading stays under `outlet`; only a complete one is a number.
    complete = record["outlet"]["complete"]
    record["energy_wh"] = record["outlet"]["energy_wh"] if complete else None
    record["status"] = "failed" if error else "ok"
    record["error"] = clip_error(error) if error else None
    write_json(run_path, record)

    if error:
        clip_path.unlink(missing_ok=True)
        logger.error(
            "%s %s seed %d failed: %s", plan.arm_name, plan.clip, plan.seed, error
        )
        return FAILED
    if not complete:
        logger.warning(
            "the outlet reading is incomplete (max gap %s s, %s); energy_wh is "
            "None and batch.py will not count this run",
            record["outlet"]["max_gap_s"],
            record["outlet"]["error"] or "the readings do not cover the window",
        )
    logger.info(
        "%s%s %s seed %d: %.1f s wall (server %s s), %s Wh, GPU mean %s W, "
        "peak memory %s GiB -> %s",
        "probe " if probe else "",
        plan.arm_name,
        plan.clip,
        plan.seed,
        wall,
        record["server_headers"].get("x-inference-time-s", "n/a"),
        record["energy_wh"],
        record["gpu_power"]["mean_w"],
        record["memory"]["peak_used_gib"],
        clip_path,
    )
    return 0


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
    p.add_argument("--arm", required=True, help="an arm in arms-v1.json")
    p.add_argument("--clip", required=True, choices=["dog-a", "dog-b", "dog-c"])
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
        plan = make_plan(
            args.arm,
            args.clip,
            args.seed,
            frames_root=args.frames_root.expanduser(),
            fallback=args.fallback,
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
    return run_one(
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
