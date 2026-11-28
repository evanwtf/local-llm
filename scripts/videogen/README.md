# scripts/videogen/

Tools that make the reference material for the video-generation judge
([#1402](https://github.com/evanwtf/local-llm/issues/1402),
[#1404](https://github.com/evanwtf/local-llm/issues/1404)). Each model gets a
prompt written from a real reference clip. The judge scores the model's output
against that clip, so a rerun must give the same clip and the same frames.

**Clips and frames never go in this repository.** The repository is public. The
source videos are the operator's own, and a phone video can carry its GPS
position in a tag. Keep every clip, frame and `first.png` in
`~/bench-logs/videogen/`. Only code, prompts, checklists, names and hashes go
in git. Four guards hold this rule:

- Both scripts refuse an output path inside any git work tree.
- `.gitignore` ignores the video extensions (`mp4`, `mov`, `m4v`, `mkv`,
  `webm`, `avi`, `heic`, `heif`, `avif`, `3gp`, `mts`, `m2ts`, `hevc`, `y4m`)
  in any letter case, and images under `benchmarks/videogen/`.
- The `refuse-video-files` pre-commit hook refuses a commit that stages one of
  those files, even with `git add -f`.
- `tests/test_no_media_files.py` reads the first bytes of every tracked file
  and refuses video and image content, whatever the file name. The pre-push
  hook and CI both run it.

The frozen prompts and checklists are in
[`benchmarks/videogen/prompts/v1.json`](../../benchmarks/videogen/prompts/v1.json).
Do not edit v1. A change is a new version file.
[`benchmarks/videogen/refs-v1.json`](../../benchmarks/videogen/refs-v1.json)
records how each v1 reference clip was cut, and the sha256 of the clip and of
every frame `frames.py` makes from it.

## The scripts

| script | what it does |
|---|---|
| `cut.py` | Cuts a window from a source video, crops it if you ask, and re-encodes it to 1920x1080 at 30 fps (libx264, CRF 14, 8 threads). It removes audio, every tag and every chapter. `--strip-only` does the same with no re-encode. It writes to a `.partial` file first and reads the tags back with ffprobe. Only a clean file gets the output name. It also writes `<out>.json`: the source name, the window, the crop, the ffmpeg version, the argv, the CPU architecture and the output sha256. |
| `frames.py` | From a clip, writes one frame per second (`s01.jpg`, ...; 1280 px wide), `first.png` (the first frame, the image-to-video input), `strip.jpg` (the busiest 2 s at 4 fps, 640 px wide, tiled 4x2, with the clip time on each tile) and `meta.json` (the parameters, the versions and the sha256 of every output). The busiest 2 s is the window with the largest sum of per-frame luma change (ffmpeg `signalstats` YDIF). |
| `generate.py` | Makes one clip: one `POST /v1/videos/sync` to a vLLM-Omni server, with the prompt from `prompts/v1.json` and every other field from [`benchmarks/videogen/arms-v1.json`](../../benchmarks/videogen/arms-v1.json). An I2V arm attaches `frames/<clip>/first.png`, and the script refuses a frame whose sha256 differs from `refs-v1.json`. It refuses a server that serves another model, and a GPU that does not fall to idle within 120 s. It writes `clip.mp4` and `run.json`: the request, the server block, the seed, start and end, wall seconds, outlet energy (Wh), mean and peak GPU power, peak memory, what ffprobe found, the node role and the output sha256. A failure, a clip unlike the arm included, writes `run.json` with the error, leaves no `clip.mp4`, and exits 1. |
| `batch.py` | Runs `generate.py` for every clip and seed of one arm, in a fixed order. It skips a run whose `run.json` is good, stops at the first failure, and then runs `frames.py` on each clip. |
| `serve.py` | Logs, or with `--start` launches, the exact vLLM-Omni server command for one model, from the `servers` block of `arms-v1.json`. It refuses a `--boundary-ratio` flag. |
| `outlet.py` | Reads a node's smart plug from InfluxDB over a window, in chunks under Grafana's point cap, and integrates it to Wh. It says whether the readings cover the window. |
| `gallery.py` | Builds a static web gallery of every clip in `~/bench-logs/videogen/gallery/` (#1413). It groups the clips by prompt: the reference clip first, then each arm and seed. It links the videos with symlinks and does not copy them. `batch.py` runs it after each clip. |
| `metrics.py` | Writes the clips' throughput, time, energy and memory from each `run.json` to `videogen.prom` in node_exporter's textfile directory, for Prometheus (#1406). `batch.py` runs it after each clip. |
| `judge.py` | `blind` copies each scored run's frames into folders with random ids, writes one judge prompt per clip, and writes the key outside the folders. `ingest` checks a judge reply and writes the score files the gallery reads (#1406). |
| `gallery_serve.py` | Serves the gallery at `:8043` behind a login, read-only, with HTTP Range so a browser can seek in a video. It checks the `Host` header and sends the page's CSP. `set-password` writes the credentials file. |
| `gallery_auth.py` | The login gate's parts: the YAML credentials file (scrypt hash, session secret, mode 0600), the signed session cookie, the client address behind a trusted proxy, and the lockout after failed logins. |
| `imagegen.py` | The image arms (#1424): one `POST /v1/images/generations` (text-to-image, JSON) or `POST /v1/images/edits` (an edit, a form with one or more input images) per run. It writes `image.png` and `run.json`. `generate.py` and `batch.py` call it for an image arm; it has no CLI of its own. |
| `common.py` | Shared code: the default output root, the guard on output paths, the ffmpeg runner, the prompt name, and the PNG size reader. |

Both scripts need `ffmpeg` and `ffprobe` on `PATH`. `frames.py` also needs the
DejaVu Sans font (`fonts-dejavu-core` on Debian and Ubuntu).

## Commands

Set the variables, then paste the commands. `START` and `DUR` are in seconds.
`CROP` is `W:H:X:Y` in source pixels.

```sh
export VG=~/bench-logs/videogen
export SRC=$VG/source/IMG_0001.MOV
export NAME=dog-a
export START=12.0
export DUR=8.0

# Cut, strip and re-encode the reference clip.
uv run python scripts/videogen/cut.py "$SRC" "$VG/ref/$NAME.mp4" \
    --start "$START" --duration "$DUR"

# The same, cropped to 16:9 from a portrait source. --overwrite replaces the
# clip from the line above; without it, cut.py refuses an existing output.
export CROP=1080:608:0:700
uv run python scripts/videogen/cut.py "$SRC" "$VG/ref/$NAME.mp4" \
    --start "$START" --duration "$DUR" --crop "$CROP" --overwrite

# Only remove audio, tags and chapters, with no re-encode.
uv run python scripts/videogen/cut.py "$SRC" "$VG/source/$NAME-stripped.mov" --strip-only

# Extract frames. The default output is $VG/frames/$NAME/.
uv run python scripts/videogen/frames.py "$VG/ref/$NAME.mp4"
```

`frames.py` refuses a directory that holds an earlier run's outputs. Add
`--overwrite` to replace them.

## Generating the arms (#1406, #1407)

`arms-v1.json` holds the fixed settings of the four arms (`h3-t2v`, `h3-i2v`,
`wan-t2v`, `wan-i2v`), the seeds (1, 2, 3), a fallback size per model, and
one `servers` block per model. Each value names its source in the file. The
file is `locked` (2026-11-17), after one timing probe per model. Its `locked`
block records the probes and the verdict: H3 keeps 1280x704, and both Wan
arms use 832x480, because 1280x720 took about 2.3 h per clip. The two models
are compared at different resolutions, so the report names each model's size
beside every score. A change is a new version file, not an edit.
`generate.py` and `batch.py` refuse a scored run from a file that is not
locked; `--allow-draft` overrides this, and such a clip does not count.

**Start each server with `serve.py`.** H3 runs on the head (:8040) and Wan 2.2
on the worker (:8041) (#1405). The `servers` block holds the exact command:
the env, the memory scope and the argv. Two parts of it matter most:

- `VLLM_OMNI_VIDEO_SYNC_TIMEOUT=7200`. The default is 600 s, which is shorter
  than one 50-step H3 clip.
- `--served-model-name` with the Hugging Face id. Each request names that
  model, and `generate.py` reads `/v1/models` first and refuses a server that
  serves another model.

The Wan server must not set `--boundary-ratio`. A value set at startup
overrides the `boundary_ratio` in each request, so `wan-t2v` (0.875) and
`wan-i2v` (0.9) would both get the startup value. `serve.py` and the arms
check refuse it. Run `serve.py` on the node that the block names. If that
node has no checkout, run it without `--start` on the head, and run the
logged line on the node over ssh.

Before each request, `generate.py` waits for the node's GPU to fall to the
idle floor (20 W, from `scripts/heartbeat.py`). It reads the power once a
second for up to 120 s, because the GPU still draws its working power for a
few seconds after the last clip ends. If the GPU is still above the floor
after 120 s, another job is running, so the script refuses: the clip's time
and energy would include that job. If the power cannot be read, it warns and
goes on. `run.json` records the passing reading, the wait and every reading. The I2V arms need the first frames, so run
`frames.py` on the three reference clips first.

```sh
export VG=~/bench-logs/videogen
# Replace HEAD_HOST with the head's name.
export H3=http://HEAD_HOST:8040
export WAN=http://10.0.0.2:8041

# Log the exact server command (on the head), or start it.
uv run python scripts/videogen/serve.py --model h3
uv run python scripts/videogen/serve.py --model h3 --start
# On the worker: wan-t2v first, then wan-i2v for the I2V arm.
uv run python scripts/videogen/serve.py --model wan-t2v --start

# One timing probe per model. It writes to $VG/probe/ and is not scored.
uv run python scripts/videogen/generate.py --arm h3-t2v --clip dog-a --seed 1 \
    --server "$H3" --probe
uv run python scripts/videogen/generate.py --arm wan-t2v --clip dog-a --seed 1 \
    --server "$WAN" --probe

# The same probe at the fallback size.
uv run python scripts/videogen/generate.py --arm wan-t2v --clip dog-a --seed 1 \
    --server "$WAN" --probe --fallback

# One scored clip, into $VG/out/<arm>/<clip>-s<seed>/.
uv run python scripts/videogen/generate.py --arm h3-i2v --clip dog-b --seed 2 \
    --server "$H3"

# A whole arm: 3 clips x 3 seeds, then frames into $VG/frames-out/<arm>/.
uv run python scripts/videogen/batch.py --arm h3-t2v --server "$H3"
uv run python scripts/videogen/batch.py --arm wan-t2v --server "$WAN"
```

**What makes a clip count.** ffprobe must find the arm's frame count (H3 124,
Wan 81), the width and height sent, and the fps sent. H3 must also have an
audio stream. A mismatch fails the run. `batch.py` counts a run as done only
when its clip still has the recorded sha256, its request and server block
match the arms file, the file was locked (or `--allow-draft`), and the outlet
energy is complete.

**Power and memory.** During the request, a thread reads the GPU power from
the node's DCGM exporter (`:9400`) and MemAvailable from its node_exporter
(`:9100`) once a second. It reads them on the `--server` host; pass
`--metrics-host` if the exporters answer on another name. Peak memory is
MemTotal minus the lowest MemAvailable, for the whole node.

**Outlet energy.** The outlet meter is a smart plug that Home Assistant
records into InfluxDB about every 5 s. After the request, `outlet.py` reads
the plug's readings for the request window through `gcx` and integrates them.
Grafana refuses an answer over 1,000 points (about 83 minutes of readings),
so it reads in chunks of 60 minutes. A `gcx` error is an error, not an empty
answer. The reading is complete only when readings cover both ends of the
window and no gap is over 30 s. An incomplete reading keeps its partial Wh
under `outlet`, and the top-level `energy_wh` is null. The arm's server
`node` (`head` or `worker`) picks the plug; `--node` overrides it. `gcx`
needs `~/.config/gcx/token`.

## The few-step arms (#1412)

[`fast-arms-v1.json`](../../benchmarks/videogen/fast-arms-v1.json) holds the
two few-step video arms for the meme-bot screen. It uses the same prompts,
clips and seeds as `arms-v1.json`, and it is `draft` until one timing probe
per model.

| arm | server (node, port) | settings |
|---|---|---|
| `fastwan-t2v` | `fastwan` (head, :8040) | FastWan2.2-TI2V-5B, BF16. 1280x704, 121 frames at 24 fps, 3 DMD steps |
| `wan-t2v-4step` | `wan-t2v-4step` (worker, :8041) | Wan 2.2 T2V A14B with the lightx2v 4-step distill LoRA, BF16. 832x480, 81 frames at 16 fps, 4 Euler steps at shift 5.0, guidance 1.0 |

Three facts from the vLLM-Omni 0.32.0 source set these arms. The file's
`verified_against` block cites each one.

- The server runs FastWan's three DMD timesteps (1000, 757, 522) for every
  request. The step count in the request does not change them.
- `--lora-backend distill` fuses the LoRA after the weights load, and online
  FP8 quantizes during the load. So the LoRA meets FP8 weights: the server
  fails, or it drops the LoRA with no error
  ([vllm-project/vllm-omni#10162](https://github.com/vllm-project/vllm-omni/pull/10162)).
  The `wan-t2v-4step` server runs BF16, and `generate.check_server` refuses
  a block that sets `--lora-backend distill` and `--quantization`.
- `--lora-path` takes the high-noise file first, then the low-noise file.

Put the weights on the node that runs the server:
`~/models/FastWan2.2-TI2V-5B-FullAttn-Diffusers` on the head, and the two
T2V LoRA files in `~/models/Wan2.2-Distill-Loras/` on the worker, beside the
base model.

```sh
# Replace HEAD_HOST with the head's name.
export FAST=http://HEAD_HOST:8040
export WAN4=http://10.0.0.2:8041

uv run python scripts/videogen/serve.py --model fastwan --start
uv run python scripts/videogen/serve.py --model wan-t2v-4step --start --host 10.0.0.2

uv run python scripts/videogen/generate.py --arm fastwan-t2v --clip dog-a --seed 1 \
    --server "$FAST" --probe
uv run python scripts/videogen/generate.py --arm wan-t2v-4step --clip dog-a --seed 1 \
    --server "$WAN4" --probe
```

## Images and image edits (#1424)

The image screen uses the same scripts. Two more arms files hold its
settings, and each names its own prompts file:

| file | arms | prompts |
|---|---|---|
| [`image-arms-v1.json`](../../benchmarks/videogen/image-arms-v1.json) | `qwen-image`, `hidream-o1`, `z-image-turbo`, `flux2-klein-4b` | [`prompts/image-v1.json`](../../benchmarks/videogen/prompts/image-v1.json): `meme-text`, `photo-dog`, `cartoon`, `crowded` |
| [`edit-arms-v1.json`](../../benchmarks/videogen/edit-arms-v1.json) | `qwen-image-edit`, `flux2-klein-4b-edit` | [`prompts/edit-v1.json`](../../benchmarks/videogen/prompts/edit-v1.json): `caption-swap`, `restyle`, `face-swap` |

Both files are `draft`. Lock each one after one timing probe per model, as
for video. Each arm's `sources` names where its steps and guidance come
from (the model card at the pinned revision). Each file's
`verified_against` cites the vLLM-Omni 0.32.0 source for every field it
sends. Two facts from that source matter:

- The image endpoints send no `x-inference-time-s`. `server_metrics`
  keeps the `metrics` object of the reply (`peak_memory_mb`,
  `stage_durations`). `server_s` is the sum of the `stage_<id>_<what>_ms`
  keys of `stage_durations`, in seconds, and `server_s_from` lists the
  keys it summed.
- vLLM-Omni 0.32.0 runs HiDream-O1 with its UniPC scheduler at shift 3.0
  for every request. The Dev model card's recipe (flash scheduler, shift
  1.0) is not available, so `hidream-o1` is not the upstream recipe.
- vLLM-Omni 0.32.0 snaps every HiDream-O1 size to the nearest aspect ratio
  in a fixed list, and the smallest is 2048x2048
  (`pipeline_hidream_o1_image.py:42-75`, `:540-552`). So `hidream-o1`
  asks for 2048x2048, and the other arms ask for 1024x1024. **HiDream-O1
  draws 4x the pixels: its seconds and Wh are not like-for-like** with the
  other arms. The judge sees every candidate at 1024x1024 (see the judge
  section).
- Qwen-Image-2.1's card lists sizes of about 4 MP. `qwen-image` asks for
  1024x1024 (1 MP), the size a chat bot posts.

**Weights** go to `~/models/imagegen/<server name>` on the node that the
server block names. `serve.py` reads every arms file; a server that serves
arms in two files (`flux2-klein-4b`) must have the same block in both, and
a test holds that.

**Input images** of the edit prompts live in
`~/bench-logs/videogen/ref/imagegen/` (`--inputs-root`), never in this
repository. `prompts/edit-v1.json` pins each one by sha256, in the order
the request sends them, and `generate.py` refuses an input with another
hash. `face-swap` sends two images: image 1 is the still, image 2 is the
face. No file here names or describes a person in an input image.

**What makes an image count.** The reply must be JSON with exactly one
base64 PNG. The PNG must have the size the arm asked for (1024x1024 for
text-to-image, 2048x2048 for `hidream-o1`, 1024x576 for an edit).
Anything else fails the run, and the run leaves no `image.png`. `run.json`
records the same measurements as a clip (wall seconds, outlet reading, GPU
power samples, peak memory), plus `media: image`, the PNG size, and
`inputs`: the name, path, size and sha256 of each input image.
`request.inputs` holds the name and sha256 only, so a move of the input
root is not a new request.

**Energy is per block, not per image.** One image takes a few seconds,
and the outlet reports about every 5 s. So:

- A run's own `energy_wh` is null unless its outlet window is complete,
  holds at least 3 readings and lasts at least 30 s. `outlet` (with its
  `readings` count) stays in `run.json` either way.
- `batch.py` runs an arm as one block on one server start: one discarded
  warm-up request (its wall time is logged, never scored), one GPU settle,
  then every request back to back with no settle between them. After the
  block, one outlet read writes `out/<arm>/block.json`: the block's Wh,
  image count, Wh per image, readings and duration. Wh per image is null
  when the reading is incomplete or a request in the block failed.
- The gallery and the metrics report the block's Wh per image for an image
  arm, and say so.
- A run under 10 s keeps its GPU power samples (`gpu_power.readings`) but
  has no GPU mean: the sampler reads about once a second and lags the load,
  so a 3 s image read only the idle 7 W.

Run one `batch.py` per server start: the warm-up is once per batch.

```sh
export VG=~/bench-logs/videogen
# Replace HEAD_HOST with the head's name.
export HEAD=http://HEAD_HOST:8040
export WORKER=http://10.0.0.2:8041

# Log the server command, or start it (one model per Spark).
uv run python scripts/videogen/serve.py --model z-image-turbo
uv run python scripts/videogen/serve.py --model z-image-turbo --start
uv run python scripts/videogen/serve.py --model flux2-klein-4b --start

# One timing probe per model, into $VG/probe/.
uv run python scripts/videogen/generate.py --arm z-image-turbo --prompt meme-text \
    --seed 1 --server "$HEAD" --probe
uv run python scripts/videogen/generate.py --arm flux2-klein-4b-edit \
    --prompt face-swap --seed 1 --server "$WORKER" --probe

# A whole arm: every prompt x seed, into $VG/out/<arm>/<prompt>-s<seed>/.
uv run python scripts/videogen/batch.py --arm z-image-turbo --server "$HEAD"
uv run python scripts/videogen/batch.py --arm flux2-klein-4b --server "$WORKER"
```

## The gallery (#1413)

`gallery.py` builds one page, `index.html`, in `~/bench-logs/videogen/gallery/`.
The page groups the clips by prompt. Each group shows the prompt text, the
reference clip, and then every run in `out/` and `probe/`, in order of arm,
then seed. Filters select a model, an arm, probe or scored, and a seed. The
page makes no external request, so it works with no internet.

Each card shows the video and a short summary: the model, the size, the
status, the wall time, the energy, the peak memory, the date and the scores.
Under the summary, collapsed groups hold the full metadata from `run.json`.
Tap a group to open it.

| group | what it shows |
|---|---|
| Request | T2V or I2V, the full prompt, the negative prompt (folded when long), the size, the frames or the length, fps, steps, `guidance_scale`, `guidance_scale_2`, `flow_shift`, `boundary_ratio`, `task`, `audio_flow_shift`, the seed, and for I2V the first frame sent (`frames/<clip>/first.png`) |
| Server | the model and revision, the served model id, the vLLM-Omni version, the quantization, the node role, the memory cap and the server argv |
| Measurements | the wall time, the server's inference time (`x-inference-time-s`), the stage durations, the energy and whether it is complete (with the largest gap), the outlet and GPU power, the GPU power before the request, and the peak memory |
| Clip check | what ffprobe found (frames, size, fps, audio, codec), the sha256 and the file size |
| Raw run.json | the whole file, pretty-printed |

An image run (#1424) gets an image card in its own part of the page, under
"Images" or "Image edits", after the video prompts. The card shows the PNG,
the same summary as a clip plus the steps and the seed, and the groups
Request, Server, Measurements, Image check and Raw run.json. An edit card
shows its input images beside the output, as image 1 and image 2. The
page links them from `ref/imagegen/` as `media/inputs/<file>`. An image
prompt gets a section only once it has a run. The energy row of an image
card is "block energy": the Wh per image from its arm's `block.json`. The
filters count "items shown", and hide a part heading when they hide every
section under it.

A field that `run.json` does not hold shows "—". The first probes wrote
schema 1, which has fewer fields; they still render. The reference card shows
how the clip was cut (source, window, crop, sha256), from
[`refs-v1.json`](../../benchmarks/videogen/refs-v1.json).

- The page does not copy a video. `media/` holds relative symlinks into
  `out/`, `probe/`, `ref/` and `frames/`.
- The arm, clip and seed of a card come from its directory names, not from
  `run.json`. A name with a character outside `[A-Za-z0-9_-]` is skipped.
- A rebuild is safe to run at any time. It writes `index.html` through a temp
  file and a rename, it does not touch an unchanged page, and it removes the
  link of a run that is gone.
- `gallery.py` refuses an output inside a git work tree, like the other
  scripts here.
- `batch.py` rebuilds the gallery after each clip it makes. A failed rebuild
  logs a warning and the batch continues. `--no-gallery` turns it off.
  `generate.py` does not rebuild it, so run `gallery.py` after a probe.

**Scores.** The judge writes one file per run to
`$VG/scores/<arm>/<run dir name>.json`. For a scored run that is
`$VG/scores/<arm>/<clip>-s<seed>.json`. A run with no file shows "not
scored"; a bad file shows why and does not stop the build.

```json
{"schema": 1, "adherence": {"passed": 11, "total": 12}, "defects": 0,
 "judge": "blind judge v1", "notes": "optional free text"}
```

**The login gate.** The gallery can be reached from the internet through a
reverse proxy, so every request needs a login. Only the login form itself
(`GET /login` and `POST /login`) is open.

- Without a session, the page gets a 303 to `login`, and any other file gets
  401. Every link and redirect is relative, so the site works under a path
  prefix such as `/videogen/` and at the root.
- The form has a username, a password and one button. A failure says "Wrong
  username or password." and does not say which field was wrong.
- The credentials live in `~/.config/local-llm/videogen-gallery/auth.yaml`
  (`--auth-file`), never in this repository. The file holds one user, a
  salted scrypt hash of the password (never the password) and the session
  secret. The server refuses to start when the file is missing, or when its
  mode is not 0600. There is no open mode.
- A session is a signed cookie that lasts 30 days (`HttpOnly`,
  `SameSite=Lax`, and `Secure` when a trusted proxy says the client used
  HTTPS). The cookie has no `Path`, so the browser scopes it to the
  directory of the login URL. The **log out** button on the page ends it:
  the server refuses that cookie from then on, until it would have expired.
  It keeps that list in memory, so a restart forgets it.
- A login over plain HTTP on the LAN (`http://<head IP>:8043/`) sends the
  password and the cookie in cleartext. Prefer the HTTPS URL.
- A login or logout POST from another site is refused (403), by its
  `Sec-Fetch-Site` and `Origin` headers.
- `set-password` makes a new session secret, so a new password ends every
  session. The server reads the file again when it changes; no restart is
  needed.
- After 5 failed logins from one client in 10 minutes, the server refuses
  logins from that client for 10 minutes (429). An IPv6 client counts by
  its /64. At most 2 password checks run at once; more get 429. The client
  is the peer address. Only when the peer is a `--trusted-proxy` does the server read the
  client from `X-Forwarded-For` (the entry that the proxy added) and the
  scheme from `X-Forwarded-Proto`.
- The server logs each login, good or bad, with the client address. It never
  logs a password.

**The server.** `gallery_serve.py` uses the Python standard library, and
PyYAML for the credentials file.

- It answers GET and HEAD for files, POST only for `login` and `logout`, and
  405 to every other method.
- It serves a file only from the gallery directory. It refuses a `..`
  segment, a hidden name, a directory, and a file type other than HTML, CSS,
  JS, MP4, PNG and JPEG (an edit's input image). The link name and the real
  file must have the same type.
- It refuses a symlink whose real path is outside `~/bench-logs/videogen/`
  (`--allow`).
- It answers only a `Host` that is an IP literal, `localhost`, or a name
  given with `--host-name`. Any other `Host` gets 421. This blocks a
  DNS-rebinding page in a LAN browser.
- It sends the Content-Security-Policy that `gallery.py` writes to
  `gallery/.csp`. The policy names the page's own CSS and JS by sha256,
  allows nothing from another origin, and lets a form post only to this
  server. The login page has its own policy of the same kind.
- It closes a connection that is idle for 30 s, and it serves at most 64
  connections at once.
- It binds `0.0.0.0:8043`.

The systemd **user** unit `systemd/local-llm-videogen-gallery.service` runs
the server from `~/git/local-llm` on the head. Linger must be on
(`loginctl show-user "$USER" -p Linger`).

**Browsing adds load.** A video streams from the head's disk and CPU. Do not
browse the gallery while a scored clip runs on the head. If you must, note
the time, so the run can record it as background load.

```sh
export VG=~/bench-logs/videogen

# Build or refresh the gallery by hand.
uv run python scripts/videogen/gallery.py

# The same, into another directory (for a test build).
uv run python scripts/videogen/gallery.py --root "$VG" --out "$(mktemp -d)/gallery"
```

**Deploy or update the server** (on the head, as the operator's user). The
unit runs `uv run --frozen --no-sync`, so it never installs a package. Run
`uv sync` first: the server needs PyYAML in `~/git/local-llm/.venv`.

```sh
cd ~/git/local-llm
git pull
uv sync

# Set the one user and password. It asks twice, with no echo. Piped input
# works too (one line). It writes auth.yaml at mode 0600, in a 0700 directory.
export GALLERY_USER=NAME
uv run python scripts/videogen/gallery_serve.py set-password --user "$GALLERY_USER"

cp systemd/local-llm-videogen-gallery.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now local-llm-videogen-gallery.service
systemctl --user restart local-llm-videogen-gallery.service

# Check it: without a session, the page redirects to the login form.
systemctl --user status local-llm-videogen-gallery.service
curl -sI http://127.0.0.1:8043/ | head -n 3
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8043/login
journalctl --user -u local-llm-videogen-gallery.service -n 20
```

To change the password, run `set-password` again. Every open session ends at
once.

**Host names and the proxy.** The server answers an IP address with no more
setup: `http://<head IP>:8043/`. To use a name, or to serve through a reverse
proxy, add a drop-in. This repository is public, so it holds no host name and
no LAN address. The unit passes `$GALLERY_HOST_ARGS` to the server, and an
unset variable adds nothing.

- `--host-name NAME`: a name that the browser or the proxy sends in `Host`.
  Add the proxy's public name too.
- `--trusted-proxy IP`: the reverse proxy's address. Without it, the server
  ignores `X-Forwarded-For` and `X-Forwarded-Proto`: the lockout then counts
  every internet client as the proxy, and the cookie never gets `Secure`.

```sh
# Opens an editor on a drop-in. Add the two lines below, with your values.
systemctl --user edit local-llm-videogen-gallery.service
#   [Service]
#   Environment="GALLERY_HOST_ARGS=--host-name NAME --host-name PUBLIC_NAME --trusted-proxy PROXY_IP"
systemctl --user restart local-llm-videogen-gallery.service
```

The proxy must pass the path below its prefix (`/videogen/x` to `/x`) and set
`X-Forwarded-For` and `X-Forwarded-Proto`.

## Prometheus metrics (#1406)

vLLM-Omni's own `/metrics` has the latency of each request and the seconds
per step. It has no count of video frames or video seconds, and its token
counters read 0 for a diffusion model. `metrics.py` takes those facts from
each `run.json` and writes them to
`/var/lib/node_exporter/textfile_collector/videogen.prom`. node_exporter on
the DGX head reads that directory (`--collector.textfile.directory`), and
Prometheus already scrapes node_exporter.

- It reads the scored runs in `out/`. `--include-probes` adds `probe/`, with
  `kind="probe"`.
- The file is a function of the `run.json` files only (and the build time),
  so a rerun writes the same series.
- It writes a temp file (`.videogen.prom.tmp.<pid>`) in the same directory,
  syncs it and renames it. A scrape never reads half a file, and
  node_exporter does not read the temp file, because its name does not end
  in `.prom`.
- It skips a `run.json` that is not a JSON object, that has no status, or
  that says ok but lacks the ffprobe frames (at least 1), the fps, `wall_s`
  or `end`. It logs a warning for each, and goes on. The first schema-1
  probe has no ffprobe block; it logs that one at INFO.
- `batch.py` runs it after each clip, after the gallery and outside the timed
  request. A missing or unwritable directory logs one warning per batch, and
  the batch goes on. `--no-metrics` turns it off.
- Two batches (head and worker) write the same file, so the scan and the
  write hold an exclusive lock on `$VG/.metrics.lock`. A writer waits up to
  60 s for it, then gives up with an error.

Each series has these labels: `arm`, `model` (the served model id), `node`
(`head` or `worker`), `mode` (`t2v` or `i2v`), `kind` (`scored` or `probe`),
`width`, `height` and `fps` (the output fps). The size is what the run asked
for (`expected`, then the request, then ffprobe), and never 0. A throughput
compares only at one size, so the size is a label. There is no clip or seed
label, so the number of series stays small.

**Every series is a gauge.** The file is a snapshot of the `run.json` files
on disk now, not a running count, and its sums can fall. A remake deletes the
old `run.json` at the start of its request and writes the new one about 40
minutes later. The other node's batch writes the file in that gap, so the
sums dip by one clip and then come back. A counter that falls looks like a
reset to Prometheus, and `rate()` would then count the whole sum again. With
a gauge, use `delta()`: over a window that holds the dip and its recovery,
the two cancel, and the net change is zero, which is the truth.

The units are Prometheus base units: seconds, joules and bytes.

| metric | what it is |
|---|---|
| `videogen_clips` | Runs with a `run.json`, with a `status` label (`ok`, `failed`). |
| `videogen_frames` | Output frames (ffprobe), summed over the ok runs. |
| `videogen_video_seconds` | Seconds of output video (frames ÷ fps), summed over the ok runs. |
| `videogen_wall_seconds` | Client wall seconds, summed over the ok runs. |
| `videogen_server_seconds` | Server inference seconds (`x-inference-time-s`), summed over the ok runs that report it. |
| `videogen_server_clips` | The ok runs that report the server time. |
| `videogen_energy_joules` | Outlet energy (J), summed over the ok runs with a complete reading. |
| `videogen_energy_clips` | The ok runs with a complete energy reading. |
| `videogen_steps` | Denoising steps requested (`num_inference_steps`), summed over the ok runs that record it. |
| `videogen_step_clips` | The ok runs that record the steps. |
| `videogen_last_frames_per_second` | The latest ok run: output frames ÷ wall seconds. |
| `videogen_last_wall_seconds` | The latest ok run: wall seconds. |
| `videogen_last_realtime_factor` | The latest ok run: wall seconds ÷ video seconds. |
| `videogen_last_peak_memory_bytes` | The latest ok run: peak used memory of the node, bytes. |
| `videogen_last_energy_joules` | The latest ok run: outlet energy (J), only when the reading is complete. |
| `videogen_last_timestamp_seconds` | The latest ok run: its end time. |
| `videogen_metrics_build_timestamp_seconds` | When `metrics.py` last wrote the file. |

**Images (#1424)** have their own families, with the labels `arm`,
`model`, `node`, `mode` (`t2i` or `edit`), `kind`, `width` and `height`.
An image has no frames, no fps and no video seconds. A `kind` label on the
clip families would add image wall seconds to `videogen_wall_seconds`, and
every query above that divides by it would mix clips and images. The image
families appear only when there is an image run. There is no image server
time, because the image endpoints do not send it. The energy families
report the arm's latest block (`out/<arm>/block.json`), not one request.

| metric | what it is |
|---|---|
| `videogen_images` | Image runs with a `run.json`, with a `status` label. |
| `videogen_image_wall_seconds` | Client wall seconds, summed over the ok images. |
| `videogen_image_block_energy_joules_per_image` | Block energy per image (J): the latest block's outlet joules over its images. Absent when the reading is incomplete or a request in the block failed. |
| `videogen_image_block_energy_joules` | The latest block's outlet energy (J), when the reading is complete. |
| `videogen_image_block_images` | The images in the latest block. |
| `videogen_image_block_seconds` | The latest block, first start to last end, in seconds. |
| `videogen_image_steps` | Denoising steps requested, summed over the ok images that record them. |
| `videogen_image_step_images` | The ok images that record the steps. |
| `videogen_image_last_wall_seconds` | The latest ok image: wall seconds. |
| `videogen_image_last_peak_memory_bytes` | The latest ok image: peak used memory of the node, bytes. |
| `videogen_image_last_timestamp_seconds` | The latest ok image: its end time. |

"Latest" is by the run's end time, per label set. **Frames per second
counts output frames**, the frames in the MP4, not the latent frames that
the model denoises.

**Divide like with like.** `videogen_server_seconds`, `videogen_energy_joules`
and `videogen_steps` sum only the runs that have the value. Divide each by its
own count (`videogen_server_clips`, `videogen_energy_clips`,
`videogen_step_clips`), not by `videogen_clips` and not by
`videogen_wall_seconds`.

**Queries.** One clip takes about 40 minutes, and the file changes only when
a clip ends. Use a window of several clips.

```promql
# Output frames per wall second over a window.
delta(videogen_frames[6h]) / delta(videogen_wall_seconds[6h])

# Real-time factor over a window: wall seconds per second of video.
delta(videogen_wall_seconds[6h]) / delta(videogen_video_seconds[6h])

# The same over every run on disk.
videogen_frames / videogen_wall_seconds
videogen_wall_seconds / videogen_video_seconds

# Energy per clip in Wh, from complete readings only.
videogen_energy_joules / videogen_energy_clips / 3600

# Server seconds per clip, from the runs that report it.
videogen_server_seconds / videogen_server_clips

# The latest run's energy in Wh and peak memory in GiB.
videogen_last_energy_joules / 3600
videogen_last_peak_memory_bytes / 2^30
```

**One-time setup** (on the head). The textfile directory belongs to root.
The script writes a temp file in it and renames it, so the operator's user
needs write access to the directory itself, not only to `videogen.prom`.
Give it once with an ACL:

```sh
sudo setfacl -m u:$USER:rwx /var/lib/node_exporter/textfile_collector
getfacl /var/lib/node_exporter/textfile_collector
```

```sh
export VG=~/bench-logs/videogen

# Write the file by hand.
uv run python scripts/videogen/metrics.py

# A test run into a scratch directory, with the probes.
export PROM_DIR="$(mktemp -d)"
uv run python scripts/videogen/metrics.py --root "$VG" --textfile-dir "$PROM_DIR" --include-probes
cat "$PROM_DIR/videogen.prom"

# Check what node_exporter serves (port 9100 on the head).
curl -s http://127.0.0.1:9100/metrics | grep '^videogen_'
```

## The blind judge (#1402, #1406)

A Claude subagent scores each clip from its frames. It must not know which
model made a clip. `judge.py blind` hides it:

- Each run gets a random 6-character id from Python's `secrets`. Nothing
  seeds it, so the public code cannot rebuild the map. The key is the only
  record.
- It copies only `s*.jpg` and `strip.jpg` to `OUT/<clip>/<id>/`, in a random
  order. Then it sets every file and folder to one fixed mtime. It does not
  copy `first.png` or `meta.json`.
- No path or file that the judge reads names the arm, the model or the seed.
  It refuses an `--out` path that names an arm or a model (from
  `arms-v1.json`), an `--out` that is not empty, and an `--out` inside a git
  work tree.
- It expects every seed in `arms-v1.json` for each arm and clip. It logs
  each missing run, or a run whose `run.json` status is not `ok`. It refuses
  to go on unless you add `--allow-missing`.
- The stills on disk must be exactly the stills in `meta.json`. Each copy
  must match its sha256 in `meta.json`, and the run's `clip.mp4` must match
  `clip_sha256`. Stale frames stop the command.
- `OUT/<clip>/PROMPT.md` is the prompt for one judge: the clip's folders in
  a random order, its checklist, and the numbered fixed defect list from
  `prompts/v1.json`.
- The key maps each id to its arm, clip, seed, clip path and clip sha256. It
  goes to `$VG/judge/keys/<OUT name>.json`, mode 0600, outside the blind
  tree. Do not give it to the judge.

Give one subagent one `PROMPT.md`. Save its reply as JSON in a sibling
folder, `<OUT>-answers/<clip>.json`, never inside the blind tree.

`judge.py ingest` checks the whole reply before it writes anything:

- Each id must be in the key, once. A key repeated at any level of the JSON
  is an error.
- The reply must answer every id of each clip that it touches.
- Each checklist number must occur exactly once.
- Each defect type must be on the fixed list. The full label, the text
  before " (" (`body morph`), and the list number (`2` or `"2"`) all count.
  The score file stores the full label.
- Each clip must still have the sha256 it had at `blind`. A run remade since
  then is an error.

One error refuses the whole file. It then writes
`$VG/scores/<arm>/<clip>-s<seed>.json` for each run, in the gallery's schema,
with the items, the defects (`defect_list`) and the `clip_sha256` added. The
gallery shows "score is for another clip" when that hash differs from the
run's `run.json`. `ingest` refuses to replace a score file unless you add
`--overwrite`, and refuses a `--scores-root` outside the key's root unless
you add `--force-scores-root`. It logs the mean adherence and the total
defects for each arm.

```sh
export VG=~/bench-logs/videogen
export BLIND=$VG/judge/t2v-v1

uv run python scripts/videogen/judge.py blind --arms h3-t2v wan-t2v --out "$BLIND"
# Give $BLIND/dog-a/PROMPT.md to a subagent; save its reply.
mkdir -p "$BLIND-answers"
uv run python scripts/videogen/judge.py ingest --blind "$BLIND" \
    --answers "$BLIND-answers/dog-a.json" --judge "blind judge v1"
uv run python scripts/videogen/gallery.py
```

**Images (#1424).** `blind` works the same way for the image arms. Name
arms from one arms file; `--arms-file`, `--prompts` and `--clips` then
default to that file, its prompts file and its prompt list.

- The image is the evidence: `blind` writes `image.png` to
  `OUT/<prompt>/<id>/image.png`, with no frame extraction. The image must
  match the sha256 that its `run.json` records.
- `blind` does not copy the server's PNG. It re-encodes it: RGB, alpha
  flattened on white, LANCZOS to 1024x1024 (text-to-image) or 1024x576
  (edits), one compress level, and no ancillary chunk. The encoder, the
  chunks and the 2048x2048 size of `hidream-o1` would each name a model.
  The key records the source sha256 and the blind copy's own sha256, and
  `ingest` refuses a reply when the blind copy changed.
- An edit prompt's input images go once to
  `OUT/<prompt>/input/image-1.jpg`, `image-2.jpg`, and so on, in the order
  the request sent them. Each must match the sha256 that `edit-v1.json`
  pins. `PROMPT.md` names them as the reference, not as candidates.
- The random ids, the fixed mtime, the random order, the key outside `OUT`
  and every `ingest` check are the same as for clips.

```sh
export VG=~/bench-logs/videogen
export BLIND=$VG/judge/image-v1
uv run python scripts/videogen/judge.py blind \
    --arms qwen-image hidream-o1 z-image-turbo flux2-klein-4b --out "$BLIND"
export BLIND=$VG/judge/edit-v1
uv run python scripts/videogen/judge.py blind \
    --arms qwen-image-edit flux2-klein-4b-edit --out "$BLIND"
```

The first blind directories kept the key inside the tree, as `key.json`,
with no root and no clip paths. `ingest` still reads that format: pass
`--key "$BLIND/key.json"`. It then takes the root from `--root` (default
`$VG`) and finds each clip at `out/<arm>/<clip>-s<seed>/clip.mp4`.
