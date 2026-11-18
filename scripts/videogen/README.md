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
| `generate.py` | Makes one clip: one `POST /v1/videos/sync` to a vLLM-Omni server, with the prompt from `prompts/v1.json` and every other field from [`benchmarks/videogen/arms-v1.json`](../../benchmarks/videogen/arms-v1.json). An I2V arm attaches `frames/<clip>/first.png`, and the script refuses a frame whose sha256 differs from `refs-v1.json`. It refuses a server that serves another model and a GPU that is not idle. It writes `clip.mp4` and `run.json`: the request, the server block, the seed, start and end, wall seconds, outlet energy (Wh), mean and peak GPU power, peak memory, what ffprobe found, the node role and the output sha256. A failure, a clip unlike the arm included, writes `run.json` with the error, leaves no `clip.mp4`, and exits 1. |
| `batch.py` | Runs `generate.py` for every clip and seed of one arm, in a fixed order. It skips a run whose `run.json` is good, stops at the first failure, and then runs `frames.py` on each clip. |
| `serve.py` | Logs, or with `--start` launches, the exact vLLM-Omni server command for one model, from the `servers` block of `arms-v1.json`. It refuses a `--boundary-ratio` flag. |
| `outlet.py` | Reads a node's smart plug from InfluxDB over a window, in chunks under Grafana's point cap, and integrates it to Wh. It says whether the readings cover the window. |
| `common.py` | Shared code: the default output root, the guard on output paths, and the ffmpeg runner. |

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
one `servers` block per model. Each value names its source in the file. Its
`status` is `draft` until one timing probe per model sets the size. Then set
`status` to `locked` and do not change the file again. `generate.py` and
`batch.py` refuse a scored run from a draft file. `--allow-draft` overrides
this; a scored clip from a draft file does not count.

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

`generate.py` also refuses to start when the node's GPU draws more than the
idle floor (20 W, from `scripts/heartbeat.py`): the clip's time and energy
would include the other job. The I2V arms need the first frames, so run
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
