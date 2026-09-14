# MiniMax-H3 video generation on the DGX Spark (spark-231e)

How to stand up **MiniMax-H3** text-to-video-and-audio on the one GB10 Spark and
generate a clip. This is the recipe **as actually built and verified on
2026-09-14** (issue #385), not a copy of an upstream README — run it to recreate
the working environment. It follows the official
[`vllm-project/vllm-omni` GB10 recipe](https://github.com/vllm-project/vllm-omni/blob/main/recipes/MiniMaxAI/MiniMax-H3-Spark-GB10.md)
but pins the exact versions and paths that work here.

H3 is a 33B omni transformer that emits **video + 32 kHz stereo audio in one
pass**, 4–15 s, 24 fps. On one Spark it is a *clip factory*, not real-time.

> **What to expect (960×576, 10 steps, FP8 — measured on this box).** Duration is
> **hard-capped at 4–15 s** (>15 s → HTTP 500). Render time scales *worse* than
> linearly with duration (attention is superlinear in sequence length):
>
> | clip length | render time | per denoise step |
> |---|---|---|
> | 4 s (107 frames) | **~3.5 min** (206–209 s) | ~20.6 s |
> | 15 s (362 frames), the max | **~17 min** (1040 s) | ~104 s |
>
> More steps scale it linearly: 50-step "full quality" is ~5× these (hours at 15 s).
> Warm ≈ cold — there is no first-run penalty to wait out. For faster iteration use
> fewer steps, a shorter clip, or the ComfyUI turbo path (#385), not this FP8 one.

> **Single-tenant.** H3 needs almost the whole 128 GB unified pool. It does **not**
> co-reside with a coding server — stop Flash-Next / any vLLM on :8030 first.
> earlyoom (#362) is the backstop, but a running LLM will make H3 OOM.

---

## 0. Prerequisites (already true on this box)

- `torch 2.13.0+cu130` is what vllm 0.28.0 reconciles to; it sees the GB10 as
  `capability (12, 1)` = sm_121. Confirmed working.
- ~140 GiB free disk for the weights (`df -h /`; the box had 2.7 TB free).
- The unified pool free: no model server resident (`free -g` shows ~117 GiB avail).
- earlyoom active (`systemctl is-active earlyoom`) — see
  [`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md).

## 1. Weights — `MiniMaxAI/MiniMax-H3`, FL2VA partition (~135 GiB, one-time)

Public repo. `FL2VA` = text/first-frame/last-frame → video+audio (13 transformer
shards + 14 text-encoder shards + video & audio VAEs). `Ref2VA` (image/video/audio
reference, another ~135 GiB) is optional and heavier — skip until needed.

```bash
mkdir -p /home/evan/models/MiniMax-H3
HF_XET_HIGH_PERFORMANCE=1 hf download MiniMaxAI/MiniMax-H3 \
  --include "FL2VA/**" --local-dir /home/evan/models/MiniMax-H3
```

Verify: `du -sh /home/evan/models/MiniMax-H3/FL2VA` ≈ 135G; 13 files in
`FL2VA/transformer/`, 14 in `FL2VA/text_encoder/`, plus `model_index.json` and
both VAEs.

## 2. Isolated venv — vllm-omni 0.28.0 **+ vllm 0.28.0** (one-time)

**Do not install into `~/venvs/vllm` (the 0.29.0 LLM-lane vllm).** vllm-omni
0.28.0 requires vllm **0.28.x**, a different version, so it gets its own venv.
vllm-omni is a pure-Python wheel and does **not** pull vllm as a dependency —
install the matching vllm yourself, second.

```bash
python3 -m venv /home/evan/venvs/vllm-omni
/home/evan/venvs/vllm-omni/bin/pip install --upgrade pip
/home/evan/venvs/vllm-omni/bin/pip install "vllm-omni==0.28.0"
/home/evan/venvs/vllm-omni/bin/pip install "vllm==0.28.0"   # aarch64 wheel; reconciles torch->2.13.0+cu130
```

Sanity check (should print GB10 / (12, 1) and import both):

```bash
/home/evan/venvs/vllm-omni/bin/python - <<'PY'
import torch, vllm, vllm_omni
print("torch", torch.__version__, "cuda", torch.version.cuda)
print(torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))
print("vllm", vllm.__version__, "+ vllm_omni ok")
PY
```

## 3. Launch the server (FP8 mandatory)

BF16 FL2VA is 135 GiB > 121 GiB usable, so **online FP8 is required** (quantizes
the DiT 62→31 GiB; text encoder and VAEs stay BF16). Peak for T2VA is ~97.7 GiB.
Served on **:8040** to stay clear of the LLM lane's :8030. Launched in a
memory-capped scope so a spike can't take the box (earlyoom is the second net).

```bash
SP=/tmp/h3   # any log dir
CUDA_VISIBLE_DEVICES=0 FLASHINFER_DISABLE_VERSION_CHECK=1 \
VLLM_WORKER_MULTIPROC_METHOD=spawn VLLM_OMNI_VIDEO_SYNC_TIMEOUT=7200 \
systemd-run --user --scope -p MemoryMax=115G -p MemorySwapMax=0 --unit=h3-omni \
  /home/evan/venvs/vllm-omni/bin/vllm serve /home/evan/models/MiniMax-H3/FL2VA \
    --omni --trust-remote-code --host 0.0.0.0 --port 8040 --init-timeout 3600 \
    --num-gpus 1 --tensor-parallel-size 1 --text-encoder-tp-size 1 \
    --usp 1 --ring 1 --vae-patch-parallel-size 1 \
    --vae-parallel-mode tile --vae-use-tiling \
    --quantization fp8 --enforce-eager --diffusion-attention-backend CUDNN_ATTN \
  > "$SP/h3-omni.log" 2>&1 &
```

`--trust-remote-code` runs MiniMaxAI's own model code (the official author, via
the official vllm-project recipe — the known-good-source bar for this).

Ready when `grep "Application startup complete" $SP/h3-omni.log` and
`curl -s http://127.0.0.1:8040/v1/models` returns the model. **First load ≈ 9 min**
(135 GiB disk-bound read + FP8 quant). Memory climbs to ~97 GiB and holds.

## 4. Generate a clip — `POST /v1/videos/sync`

Multipart form; returns the MP4 (H.264 + AAC) as the response body. Pass the
prompt from a file to avoid shell-quoting (apostrophes etc.).

```bash
printf '%s' "A wave crashes on a rocky shore at sunset, gulls calling." > /tmp/p.txt
curl --fail-with-body -sS -X POST http://127.0.0.1:8040/v1/videos/sync \
  --max-time 1800 -w '\nHTTP %{http_code} e2e=%{time_total}s\n' \
  -F 'prompt=</tmp/p.txt' \
  -F 'width=960' -F 'height=576' -F 'aspect_ratio=16:9' -F 'fps=24' \
  -F 'num_inference_steps=10' -F 'flow_shift=12' -F 'seed=42' \
  -F 'extra_params={"task":"t2va","duration":8.0,"audio_flow_shift":3.0}' \
  -o /home/evan/h3-clips/out.mp4
```

No `model` form field is needed — the request uses whatever the server loaded.

### Parameter reference (exact, from `vllm_omni/entrypoints/openai/serving_video.py`)

Two places: top-level multipart **form fields**, and a JSON blob in the
`extra_params` field. What we ran, and what each does:

| parameter | where | our value | notes |
|---|---|---|---|
| `prompt` | form field (`-F 'prompt=<file'`) | the scene | pass via file to dodge shell-quoting |
| `width` / `height` | form field | `960` / `576` | 960×576 is the validated GB10 geometry |
| `aspect_ratio` | form field | `16:9` | **required** for t2va even with width/height |
| `fps` | form field | `24` | output frame rate; defaults to 24 if omitted |
| `num_inference_steps` | form field | `10` | denoise steps; ~96% of wall. 50 = full quality (~17 min) |
| `seed` | form field | `42` | omit for random |
| `flow_shift` | form field | `12` | flow-matching shift (from the upstream recipe) |
| `guidance_scale` | form field | _(unset)_ | **CFG**: set `5` for speech, `1` for instrumentals; unset = model default |
| `guidance_scale_2` | form field | _(unset)_ | second-stage CFG, optional |
| `negative_prompt` | form field | _(empty)_ | speech recipe pairs CFG 5 with an **empty** negative |
| `sound_duration` | form field | _(unset)_ | optional; scope the audio track independently |
| `task` | `extra_params` JSON | `t2va` | text→video+audio (FL2VA) |
| `duration` | `extra_params` JSON | `4.0`–`15.0` | **hard limit [4, 15] s**; >15 → HTTP 500 |
| `audio_flow_shift` | `extra_params` JSON | `3.0` | audio-branch flow shift (upstream recipe) |

- **`duration` must be in [4, 15] seconds** — the model hard-rejects anything
  outside: `RuntimeError: MiniMax H3 output duration must be in [4, 15] seconds,
  got 20.0`, HTTP 500, instant (no GPU work). For longer than 15 s, generate
  multiple shots and stitch.
- **Speech**: `-F 'guidance_scale=5'` + `-F 'negative_prompt='` (empty). Use
  `guidance_scale=1` for instrumental/ambient audio. Guidance-free speech babbles.

Confirm streams with pyav (in the omni venv):

```bash
/home/evan/venvs/vllm-omni/bin/python -c "import av;c=av.open('/home/evan/h3-clips/out.mp4');[print(s.type,s.codec_context.name) for s in c.streams]"
```

## 5. Measured performance (spark-231e, 960×576, 10 steps, FP8, this session)

| clip | duration | frames | E2E | denoise/step | notes |
|---|---|---|---|---|---|
| cats (cold first run) | 4 s | 107 | 209.1 s | 20.9 s | |
| dogs (warm) | 4 s | 107 | 206.0 s | 20.6 s | warm ≈ cold |
| dogs (max duration) | 15 s | 362 | **1039.9 s (17.3 min)** | 104.0 s | ~5× the 4 s/step |

**Warm ≈ cold**: ~20.6 s/step is the steady-state cost, not a cold-start
artifact. **Duration scales worse than linearly**: 15 s (362 frames) costs ~104 s/step
vs ~20.6 s/step at 4 s (107 frames) — ~5× for ~3.4× the frames, because attention
grows superlinearly with sequence length. So a 15 s clip at 10 steps is ~17 min.
Denoise is ~96% of E2E; a full 50-step pass would be hours. **Memory is flat across
durations** (~30–33 GiB free whether 4 s or 15 s) — the footprint is
weight-dominated, so longer clips cost time, not pool. The sub-2-min
"~95 s" figures in the field reports are the **ComfyUI pruned+turbo** path
(#385), not this vLLM-Omni FP8 one. GPU during render (gcx/DCGM): util ~96 %,
power ~63–69 W, temp ~70 °C, SM clock ~2.4 GHz, `MEM_COPY_UTIL 0` — compute-bound,
not memory-bandwidth-bound.

## 6. Viewing clips over the LAN

The server writes the MP4 to a file on the Spark; it does not serve it. Simplest
viewer is a scoped static server (LAN only):

```bash
python3 -m http.server 8041 --bind 0.0.0.0 --directory /home/evan/h3-clips
# open http://dgx.internal:8041/  (192.168.1.180) in a browser
```

**Output naming convention (this session):** save as
`<sanitized first 30 chars of prompt>-seed<N>.mp4` with a `.json` sidecar holding
the full prompt + all params + timing, so a clip is self-describing.

## 7. Teardown

```bash
systemctl --user stop h3-omni.scope       # frees the ~97 GiB pool
# (leave the http.server running if you still want to browse clips)
```

## Constraints & gotchas (learned here)

- **duration ∈ [4, 15] s**, hard model limit. >15 s → HTTP 500. Longer = stitch.
- **T2VA only for now.** Ref2VA peaks ~114 GiB (≈7 GiB free) — that trips
  earlyoom's 12 GiB SIGTERM floor; it needs earlyoom tuning before it's safe.
- **Version match is load-bearing.** vllm-omni 0.28.0 ⇒ vllm 0.28.x. Mixing with
  0.29.x breaks FP8 loading. Keep this venv separate from the LLM lane's.
- **FP8 is not optional** on 128 GB; BF16 (135 GiB) will not fit.
- No `--enable-cpu-offload` / `--enable-distributed-layerwise-offload`: on unified
  memory they double peak usage and OOM (per the upstream recipe).

Provenance: built for #385 (Video-Gen workstream); depends conceptually on #384
(the ComfyUI alternative) and the OOM backstop #362.
