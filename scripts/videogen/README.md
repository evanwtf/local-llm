# scripts/videogen/

Tools that make the reference material for the video-generation judge
([#1402](https://github.com/evanwtf/local-llm/issues/1402),
[#1404](https://github.com/evanwtf/local-llm/issues/1404)). Each model gets a
prompt written from a real reference clip. The judge scores the model's output
against that clip, so a rerun must give the same clip and the same frames.

**Clips and frames never go in this repository.** The repository is public. The
source videos are the operator's own, and a phone video can carry its GPS
position in a tag. Keep every clip, frame and `first.png` in
`~/bench-logs/videogen/`. Only code, prompts, checklists and scores go in git.
Three guards hold this rule:

- Both scripts refuse an output path inside the repository.
- `.gitignore` ignores `*.mp4`, `*.mov`, `*.MOV`, `*.m4v` and `*.mkv`.
- The `refuse-video-files` pre-commit hook refuses a commit that stages a video
  file, or an image under `benchmarks/videogen/`.

The frozen prompts and checklists are in
[`benchmarks/videogen/prompts/v1.json`](../../benchmarks/videogen/prompts/v1.json).
Do not edit v1. A change is a new version file.

## The scripts

| script | what it does |
|---|---|
| `cut.py` | Cuts a window from a source video, crops it if you ask, and re-encodes it to 1920x1080 at 30 fps (libx264, CRF 14). It removes audio and every tag. `--strip-only` removes audio and tags with no re-encode. Then it reads the tags back with ffprobe. If a location tag remains, it deletes the output and exits nonzero. |
| `frames.py` | From a clip, writes one frame per second (`s01.jpg`, ...; 1280 px wide), `first.png` (the first frame, the image-to-video input), `strip.jpg` (the busiest 2 s at 4 fps, 640 px wide, tiled 4x2, with the time on each tile) and `meta.json`. The busiest 2 s is the window with the largest sum of per-frame luma change (ffmpeg `signalstats` YDIF). |
| `common.py` | Shared code: the default output root, the guard on output paths, and the ffmpeg runner. |

Both scripts need `ffmpeg` and `ffprobe` on `PATH`.

## Commands

Set the variables, then paste the commands. `START` and `DUR` are in seconds.
`CROP` is `W:H:X:Y` in source pixels.

```sh
export VG=~/bench-logs/videogen
export SRC=$VG/source/IMG_0001.MOV
export NAME=dog-a
export START=3.0
export DUR=8.0

# Cut, strip and re-encode the reference clip.
uv run python scripts/videogen/cut.py "$SRC" "$VG/clips/$NAME.mp4" \
    --start "$START" --duration "$DUR"

# The same, cropped to 16:9 from a portrait source.
export CROP=1080:608:0:656
uv run python scripts/videogen/cut.py "$SRC" "$VG/clips/$NAME.mp4" \
    --start "$START" --duration "$DUR" --crop "$CROP"

# Only remove audio and tags, with no re-encode.
uv run python scripts/videogen/cut.py "$SRC" "$VG/clips/$NAME-stripped.mov" --strip-only

# Extract frames. The default output is $VG/frames/$NAME/.
uv run python scripts/videogen/frames.py "$VG/clips/$NAME.mp4"
```

`frames.py` refuses a directory that holds an earlier run's outputs. Add
`--overwrite` to replace them.
