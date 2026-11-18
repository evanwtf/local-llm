"""Generate one video-gen eval clip from a vLLM-Omni server and record it (#1406, #1407).

    generate.py --arm h3-t2v --clip dog-a --seed 1 --server http://HOST:8040
    generate.py --arm wan-i2v --clip dog-b --seed 2 --server http://HOST:PORT --probe

One request to `POST /v1/videos/sync` (multipart form). The prompt comes from
`benchmarks/videogen/prompts/v1.json`, and every other request field comes
from `benchmarks/videogen/arms-v1.json`, so the same arm, clip and seed make
the same request every time. An I2V arm attaches the clip's first frame from
`~/bench-logs/videogen/frames/<clip>/first.png` (made by `frames.py`), and the
script refuses a frame whose sha256 is not the one `refs-v1.json` records.

It writes `clip.mp4` and `run.json` into the output directory. `run.json`
holds the arm, the model and revision, every field sent, the seed, start and
end (America/New_York, with offset), wall seconds, outlet energy in Wh, mean
and peak GPU power, peak memory, the server version, the node, the output
sha256 and the HTTP status. On a failure it writes `run.json` with the error,
leaves no `clip.mp4`, and exits 1. It refuses (exit 2, nothing written) an
output inside a git work tree, a scored run from a draft arms file, an
existing output without `--overwrite`, or a wrong first frame.

**Power and memory.** While the request runs, a thread reads the server
node's GPU power from its DCGM exporter (:9400) and its MemAvailable from its
node_exporter (:9100) once a second, with the readers in
`benchmarks/agent/remote.py`. Peak memory is MemTotal minus the lowest
MemAvailable: the whole node, which is what the repo's memory gates read. The
outlet meter is a smart plug that Home Assistant records into InfluxDB at its
own pace (about every 5 s), so polling it once a second adds nothing. After
the request ends, the script reads the plug's raw readings for the request
window and integrates them by the trapezoid rule
(`clock_cap_evidence.raw_flux`, `flux_points`, `energy_wh`).

`--probe` runs one request into `~/bench-logs/videogen/probe/` and logs the
timing. A probe is not a scored clip, and it may run from a draft arms file.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
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

import clock_cap_evidence
import dgx_hosts
import dgx_metrics
import remote
from videogen.common import DEFAULT_ROOT, InsideRepo, refuse_inside_repo, sha256

import logs

logger = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
ARMS_FILE = REPO / "benchmarks" / "videogen" / "arms-v1.json"
PROMPTS_FILE = REPO / "benchmarks" / "videogen" / "prompts" / "v1.json"
REFS_FILE = REPO / "benchmarks" / "videogen" / "refs-v1.json"
ENDPOINT = "/v1/videos/sync"

#: Two hours. A 50-step H3 clip at 1280x704 can take over an hour on one
#: Spark. The server has its own limit, VLLM_OMNI_VIDEO_SYNC_TIMEOUT (600 s
#: by default), so start it with 7200 too (the H3 runbook does).
TIMEOUT_S = 7200.0
#: Seconds between GPU-power and memory samples.
SAMPLE_S = 1.0
#: How long to wait for the plug's readings to reach InfluxDB after the
#: request ends, per try, and how many tries.
OUTLET_SETTLE_S = 15.0
OUTLET_TRIES = 4
#: Plug readings this far before the start are read too, so the value held
#: at the start of the window is known.
OUTLET_PAD = datetime.timedelta(seconds=120)

#: Every form field `POST /v1/videos/sync` accepts in vllm-omni 0.28.0
#: (`_parse_video_form` in vllm_omni/entrypoints/openai/api_server.py). An
#: arms file that names anything else is refused: FastAPI drops an unknown
#: form field without a word, so a typo would change nothing and look fine.
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

#: The response headers the sync endpoint sets, kept in run.json.
SERVER_HEADERS = (
    "X-Request-Id",
    "X-Model",
    "X-Inference-Time-S",
    "X-Stage-Durations",
    "X-Peak-Memory-MB",
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


def check_arms(arms: Mapping[str, Any]) -> None:
    """Raise ValueError if the arms file names a field the server ignores."""
    for name, arm in arms["arms"].items():
        unknown = set(arm["form"]) - FORM_FIELDS
        if unknown:
            raise ValueError(f"arm {name}: unknown form field(s) {sorted(unknown)}")
        for reserved in ("prompt", "seed", "extra_params", "input_reference"):
            if reserved in arm["form"]:
                raise ValueError(f"arm {name}: {reserved} is set per request")
        if arm["mode"] not in ("t2v", "i2v"):
            raise ValueError(f"arm {name}: mode must be t2v or i2v")
        if (arm["mode"] == "i2v") != ("input_reference" in arm):
            raise ValueError(f"arm {name}: an i2v arm, and only one, has an image")


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
    arm: Mapping[str, Any], prompt: str, seed: int, *, fallback: bool = False
) -> dict[str, str]:
    """The form fields as sent, every value a string.

    `fallback` swaps in the arm's fallback size. `extra_params` goes as
    compact, key-sorted JSON so the same arm gives the same bytes.
    """
    form = dict(arm["form"])
    if fallback:
        form.update(arm["fallback"])
    fields = {"prompt": prompt}
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
    arms_file: pathlib.Path = ARMS_FILE

    def request(self) -> dict[str, Any]:
        return request_record(self.fields, self.image)


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
    prompt_id, prompt = prompt_for(load_json(PROMPTS_FILE), clip)
    image = load_image(arm, clip, frames_root, load_json(REFS_FILE))
    return Plan(
        arm_name=arm_name,
        arm=arm,
        arms_status=str(arms["status"]),
        clip=clip,
        prompt_id=prompt_id,
        seed=seed,
        fields=build_fields(arm, prompt, seed, fallback=fallback),
        image=image,
        fallback=fallback,
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


#: (url, body, content type, timeout) -> Reply. Raises OSError when no
#: answer arrives at all.
Post = Callable[[str, bytes, str, float], Reply]
#: (url, timeout) -> response text, or None when it cannot be read.
Get = Callable[[str, float], str | None]


def urllib_post(url: str, body: bytes, content_type: str, timeout: float) -> Reply:
    """POST with urllib. An HTTP error status is a Reply, not an exception."""
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": content_type}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Reply(resp.status, dict(resp.headers.items()), resp.read())
    except urllib.error.HTTPError as err:
        return Reply(err.code, dict(err.headers.items()), err.read())


def urllib_get(url: str, timeout: float) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            text: str = resp.read().decode("utf-8", "replace")
            return text
    except (urllib.error.URLError, OSError, ValueError):
        return None


def server_info(server: str, get: Get) -> dict[str, Any]:
    """The server's vLLM version (`/version`) and served model (`/v1/models`).

    vllm-omni serves no version of its own; `/version` is the vLLM it runs on.
    """
    info: dict[str, Any] = {"vllm_version": None, "served_model": None}
    text = get(server.rstrip("/") + "/version", 10.0)
    try:
        info["vllm_version"] = json.loads(text or "")["version"]
    except (ValueError, KeyError, TypeError):
        pass
    text = get(server.rstrip("/") + "/v1/models", 10.0)
    try:
        info["served_model"] = json.loads(text or "")["data"][0]["id"]
    except (ValueError, KeyError, TypeError, IndexError):
        pass
    return info


def is_mp4(body: bytes) -> bool:
    """An MP4 opens with a box whose type, at bytes 4..8, is `ftyp`."""
    return len(body) > 8 and body[4:8] == b"ftyp"


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


# ---------------------------------------------------------------- outlet ----


Flux = Callable[[str], str]


def gcx_flux(query: str) -> str:
    """One Flux query against the Home Assistant InfluxDB, through gcx."""
    return clock_cap_evidence._gcx(
        ["datasources", "query", dgx_metrics.INFLUX_DATASOURCE, query]
    )


def outlet_entity(role: str) -> str:
    """The Home Assistant entity of the node's smart plug."""
    return {
        "head": dgx_metrics.OUTLET_ENTITY,
        "worker": dgx_metrics.WORKER_OUTLET_ENTITY,
    }[role]


def integrate_outlet(
    points: Sequence[tuple[datetime.datetime, float]],
    start: datetime.datetime,
    end: datetime.datetime,
) -> float | None:
    """Wh from start to end. The reading in force at the start (the plug
    records on its own clock, not ours) is added at the start edge."""
    pts = list(points)
    edge = clock_cap_evidence.held_value(pts, start)
    if edge is not None:
        pts.append((start, edge))
    pts.sort(key=lambda p: p[0])
    return clock_cap_evidence.energy_wh(pts, start, end)


def outlet_energy(
    entity: str,
    start: datetime.datetime,
    end: datetime.datetime,
    *,
    flux: Flux,
    sleep: Callable[[float], None] = time.sleep,
    settle_s: float = OUTLET_SETTLE_S,
    tries: int = OUTLET_TRIES,
) -> dict[str, Any]:
    """Read the plug's raw readings for the window and integrate them.

    Waits for a reading at or after `end`, so the last seconds are not cut
    off by InfluxDB lag. After `tries` waits it uses what it has and says so.
    """
    query = clock_cap_evidence.raw_flux(
        entity,
        "W",
        clock_cap_evidence.utc_text(start - OUTLET_PAD),
        clock_cap_evidence.utc_text(end + datetime.timedelta(seconds=60)),
    )
    points: list[tuple[datetime.datetime, float]] = []
    complete = False
    for attempt in range(tries):
        sleep(settle_s)
        points = clock_cap_evidence.flux_points(flux(query))
        if any(t >= end for t, _ in points):
            complete = True
            break
        logger.info("outlet: no reading after the end yet (try %d)", attempt + 1)
    inside = [v for t, v in points if start <= t <= end]
    return {
        "entity": entity,
        "energy_wh": _round(integrate_outlet(points, start, end), 3),
        "readings": len(inside),
        "mean_w": _round(sum(inside) / len(inside), 1) if inside else None,
        "peak_w": _round(max(inside), 1) if inside else None,
        "complete": complete,
    }


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


# ------------------------------------------------------------------- run ----


def ny_now() -> datetime.datetime:
    return datetime.datetime.now(NY)


def iso(t: datetime.datetime) -> str:
    """ISO 8601 with offset, America/New_York: the repo's one timestamp shape."""
    return t.astimezone(NY).strftime("%Y-%m-%dT%H:%M:%S%z")


def node_host(role: str) -> str | None:
    """The node's host name from local config (dgx_hosts), never an address."""
    try:
        return dgx_hosts.lookup(role)
    except dgx_hosts.HostUnknown as err:
        logger.warning("%s", err)
        return None


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
    flux: Flux = gcx_flux
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime.datetime] = ny_now
    node_host: Callable[[str], str | None] = node_host
    machine: Callable[[], str | None] = client_machine
    sample_s: float = SAMPLE_S


def write_json(path: pathlib.Path, data: Mapping[str, Any]) -> None:
    """Write JSON through a temp file and a rename, so no reader sees half."""
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def write_clip(path: pathlib.Path, body: bytes) -> None:
    """Write the clip through `.partial` and a rename: never a half file."""
    tmp = path.with_name(path.name + ".partial")
    try:
        with tmp.open("wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def default_out(
    arm: str, clip: str, seed: int, probe: bool, stamp: str
) -> pathlib.Path:
    if probe:
        return DEFAULT_ROOT / "probe" / arm / f"{clip}-s{seed}-{stamp}"
    return DEFAULT_ROOT / "out" / arm / f"{clip}-s{seed}"


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
    out.mkdir(parents=True, exist_ok=True)
    # Clear the old pair first, so a failure below cannot leave an old clip
    # beside a new run.json that describes a different request.
    clip_path.unlink(missing_ok=True)
    run_path.unlink(missing_ok=True)

    role = node or str(plan.arm["node"])
    host = metrics_host or urllib.parse.urlsplit(server).hostname or ""
    record: dict[str, Any] = {
        "schema": 1,
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
        "endpoint": ENDPOINT,
        "node": {"role": role, "host": deps.node_host(role)},
        "client_machine": deps.machine(),
        "server": server_info(server, deps.get),
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
    except (OSError, ValueError) as err:
        error = f"request failed: {err}"
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
        try:
            write_clip(clip_path, reply.body)
        except OSError as err:
            error = f"could not write the clip: {err}"
    if error is None:
        record["output"] = {
            "file": clip_path.name,
            "bytes": clip_path.stat().st_size,
            "sha256": sha256(clip_path),
        }
    record["outlet"] = outlet_energy(
        outlet_entity(role), start, end, flux=deps.flux, sleep=deps.sleep
    )
    record["energy_wh"] = record["outlet"]["energy_wh"]
    record["status"] = "failed" if error else "ok"
    record["error"] = error
    write_json(run_path, record)

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
        wall,
        record["server_headers"].get("X-Inference-Time-S", "n/a"),
        record["energy_wh"],
        record["gpu_power"]["mean_w"],
        record["memory"]["peak_used_gib"],
        clip_path,
    )
    return 0


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
