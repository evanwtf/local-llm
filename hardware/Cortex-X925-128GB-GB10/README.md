# Cortex-X925-128GB-GB10

**NVIDIA DGX Spark.** The directory name is derived by
`scripts/hardware_id.py`, never typed, and it cannot say either of the two
things that matter most about this machine. They are recorded here because
#292 requires it.

## 1. This is a DGX Spark

The name reads as a generic Arm box with a discrete GPU. It is not. It is
NVIDIA's GB10 Grace-Blackwell desktop: a 20-core Cortex-X925/A725 Grace CPU and
a Blackwell GPU on one package, sharing one pool of memory.

## 2. The 128 GB is UNIFIED, not VRAM

This is the assumption-breaker. **Every candidates-by-VRAM judgement in this
repo assumes discrete VRAM, and that assumption is false here** — in exactly
the way it is false on the M5 Max, and for the same reason.

Ollama sees it as an integrated device and offers the whole pool:

```
msg="inference compute" library=CUDA compute=12.1 name=CUDA0
  description="NVIDIA GB10" driver=13.0 type=iGPU
  total="121.7 GiB" available="117.8 GiB"
```

`type=iGPU`, `total=121.7 GiB`. A 98 GiB model is a candidate here. On a
12 GiB RTX 3080 Ti it is not, and no amount of system RAM changes that. When
reading any sizing note in this repo, check whether it was written for a
discrete card before applying it to this machine.

Memory is named by installed size (128 GB) and reported as usable (121.7 GiB),
per `hardware/README.md`.

## 3. Blackwell, and what that costs us

`cc=1210` (sm_121). Two consequences, both observed:

- Ollama's bundled **CUDA 12** build does not carry this architecture and is
  skipped automatically; it loads `cuda_v13`. This is correct behaviour, not an
  error, and the log line saying so is not a warning to chase:
  `skipping CUDA device — compute capability not in compiled architectures ... archs="[500 ... 1000 1200]"`
- **NVFP4 is this GPU's native 4-bit format and we cannot yet reach it through
  Ollama.** The `nvfp4` and `mxfp8` tags ship as sharded
  `vnd.ollama.image.tensor` layers, which Ollama 0.34.0 routes to the MLX
  runtime; the pull fails before any weights move. See #293.

## 4. What does not run here

- **The Swift half of the agent suite.** `~/git/monitor` is an AppKit desktop
  application; `swift test` is not an oracle off macOS. The five Swift
  excisions are gated `platform = "darwin"` and are **skipped**, leaving a
  missing cell rather than a zero. This machine's full matrix is **10 tasks**:
  8 Python excisions + 2 script tasks.
- **Everything MLX.** `mlx-serve`, and every `-mlx`/`mxfp8` Ollama tag.
- **ds4 on Metal.** The `ivanfioravanti/ds4-metal` fork that the Mac's ds4
  rows were taken on is a Metal fork. ds4 claims CUDA support; that is untested
  here and is its own issue when it is tried.

Any comparison between this machine and the M5 Max must therefore be
**Python-only**, and must not pool an MLX-quantized row with a GGUF one.

## Facts

| | |
|---|---|
| machine | NVIDIA DGX Spark |
| CPU | 20-core Arm (Cortex-X925 + A725), aarch64 |
| memory | 128 GB installed, 121.7 GiB usable, **unified** |
| GPU | NVIDIA GB10, Blackwell, `cc=1210` / sm_121 |
| OS | Ubuntu 24.04.5 LTS, Linux 6.17.0-1032-nvidia |
| CUDA | 13.0.88, driver 580.173.02 |
| acquired | 2026-09-11 |
