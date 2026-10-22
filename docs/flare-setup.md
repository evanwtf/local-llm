# NVIDIA Flare on two DGX Sparks, step by step

This page is the setup record for NVIDIA Flare
([`NVIDIA/flare-local-inference`](https://github.com/NVIDIA/flare-local-inference))
on the two-Spark cluster (#1207, #1210). It is written as the install happens,
from the commands that actually ran. A step that has not run yet says so.

Flare is not NVIDIA FLARE, the federated-learning SDK at
https://developer.nvidia.com/flare. The two share a name only.

**What Flare is.** A local inference server for SM120/SM121 GPUs. **Ignition**
(Python: PyTorch, Transformers, FlashInfer/b12x) prepares a model into a signed
**runtime package**: tuned kernels, compiled forwards and captured CUDA graphs.
**Helios**, in Rust, serves that package without Python. **Orbit** runs one
tensor-parallel rank per Spark over RoCE. The upstream README calls it "a
research prototype".

**Node names.** `<head-node>` is node A, which serves the API. `<worker-node>`
is node B, rank 1. The steps never use a host name from this cluster.

**Pinned versions** (2026-10-22):

| part | version |
|---|---|
| Flare | `main` at `5f37bd1406` (2026-10-21, "Clarify project status") |
| FlashInfer fork (pinned by Flare's `pyproject.toml`) | `local-inference-lab/flashinfer` at `03de0b663abe8f5dc69d4e10118587ab459b13eb` |
| Python | 3.12 (Flare's `.python-version`) |
| Rust | 1.98.1 (rustup) |
| OS packages | protoc 3.21.12, ninja 1.11.1 (Ubuntu 24.04) |
| weights | `nvidia/GLM-5.3-Flash-FP4-Local` at `76348ddb2b`, 166 GiB, already in `~/models` on both nodes (#1128) |

## 1. Install the build packages (both nodes)

Flare's build needs a C/C++ compiler, CMake, Ninja, pkg-config, `protoc`,
OpenSSL headers, Python headers and liburing headers
([upstream: environment setup](https://github.com/NVIDIA/flare-local-inference/blob/main/docs/model-preparation.md#environment-setup)).
On 2026-10-22, both nodes already had `build-essential`, `cmake` and
`pkg-config`, and lacked the rest.

1. On the head node, run:

   ```sh
   sudo apt-get install -y --no-install-recommends \
     ninja-build protobuf-compiler python3-dev liburing-dev libssl-dev
   ```

2. On the worker node, run the same command.
3. On each node, check the result:

   ```sh
   dpkg -s ninja-build protobuf-compiler python3-dev liburing-dev libssl-dev >/dev/null && echo ok
   ls /usr/include/python3.12/Python.h
   protoc --version     # libprotoc 3.21.12
   ninja --version      # 1.11.1
   ```

**Why `python3-dev` matters beyond Flare.** Without it, Triton cannot compile
the small helper it builds when vLLM starts, because `Python.h` is missing. On
2026-10-22 that stopped the #1088 vLLM 0.32.0 server on the worker node.

## 2. Install Rust (both nodes)

Flare's native CLI is a Cargo project. Install Rust with rustup, not with
`apt`: Ubuntu's `cargo` is too old for current lock files
(`docs/dgx-spark-runbook.md`).

1. On each node, check for it: `~/.cargo/bin/rustc --version`.
2. If it is missing, install it:

   ```sh
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
   ```

## 3. Get the source at the pinned commit (both nodes)

1. On each node, run:

   ```sh
   cd ~/git
   git clone https://github.com/NVIDIA/flare-local-inference
   cd flare-local-inference
   git checkout 5f37bd1406
   ```

## 4. Install the Python environment (both nodes)

1. On each node, run:

   ```sh
   cd ~/git/flare-local-inference
   export PATH=$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/cuda/bin:$PATH
   uv sync --locked --extra prepare --extra test
   ```

   This downloads PyTorch and the pinned FlashInfer fork. Do it between
   benchmark runs: a run that overlaps a download on either node is void.

*Steps 5 onward have not run yet.*

## 5. Build the native CLI (both nodes)

## 6. Prepare a TP2 GLM package across both nodes

## 7. Serve with Orbit

## 8. Check that it works

## 9. Point OpenCode at it

## 10. Stop and clean up

## Problems hit, and their fixes
