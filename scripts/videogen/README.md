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
