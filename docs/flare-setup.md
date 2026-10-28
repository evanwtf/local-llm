# NVIDIA Flare on two DGX Sparks, step by step

This page is the setup record for NVIDIA Flare
([`NVIDIA/flare-local-inference`](https://github.com/NVIDIA/flare-local-inference))
on the two-Spark cluster (#1207, #1210), from the commands that ran.

**Status: stopped after step 5.** Flare did not serve two-Spark GLM on this
cluster at any of three pins (see "Problems hit"), and #1207 closed on
2026-10-22. Flare could at best make the GLM fast option faster; it cannot
close GLM's hidden-test gap. A new issue is worth filing when an upstream
release says two-Spark GLM works.

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
| Flare | `main` at `947a5a6d` (2026-10-22); also tried `5f37bd1` and `df8c2ab6` (see "Problems hit") |
| FlashInfer fork (pinned by Flare's `pyproject.toml`) | `local-inference-lab/flashinfer` at `03de0b663abe8f5dc69d4e10118587ab459b13eb` |
| Python | 3.12 (Flare's `.python-version`) |
| Rust | 1.99.1 (rustup; the same on both nodes) |
| OS packages | protoc 3.21.12, ninja 1.11.1 (Ubuntu 24.04) |
| weights | `nvidia/GLM-5.3-Flash-FP4-Local` at `76348ddb2b`, 166 GiB (#1128); the last tests used `5b865baa` (165.5 GiB). Both revisions are now in the NAS archive, not on the nodes |

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
   git checkout 947a5a6d
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

2. Install the native Python extension. `uv sync` does not rebuild it, so
   do this after every checkout change:

   ```sh
   uv pip install --python .venv/bin/python --no-deps ./heliopause
   ```

## 5. Build the native CLI (both nodes)

1. On each node, run:

   ```sh
   cd ~/git/flare-local-inference
   export PATH=$HOME/.cargo/bin:$HOME/.local/bin:/usr/local/cuda/bin:$PATH
   cargo build --release --manifest-path flare/Cargo.toml
   ```

2. Check the result: `ls -la flare/target/release/flare`.

3. Check that both nodes built the same thing. The binary size and
   `flare --version` must match on both nodes:

   ```sh
   ls -la flare/target/release/flare
   ./flare/target/release/flare --version
   ```

On 2026-10-22, each node built it in about 37 s, and both binaries are
20,410,792 bytes (`flare 0.1.0`).

**Use the same Rust version on both nodes.** The worker node had no Rust, and
rustup installed 1.99.1 there; the head node had 1.98.1. Run
`rustup update stable` on the older node and build again. Flare rejects
preparation hosts whose sources do not match, and the same toolchain removes
one difference before it can matter.

**Do not build while a benchmark runs.** A compile loads the CPU of the node
that serves the run.

## Problems hit, and their fixes

**`GLM linear_num_heads requires 64, got None`** (Flare `5f37bd1`). The
recipe pins `nvidia/GLM-5.3-Flash-FP4-Local@76348ddb`, but that loader cannot
read the checkpoint's config or quantization layout. Upstream fixed it on
2026-10-22 (NVIDIA/flare-local-inference#17); a later `main` loads it and
fails at the weight transfer (below). A config patch is not enough: the next
failure is `f_b_proj: missing quantized_layers declaration`.

**`AttributeError: module 'flare_heliopause' has no attribute ...`.** The
native extension is older than the checkout. Run step 4.2 again.

**`PermissionError` under `~/.cache`** (for example `~/.cache/b12x`).
Containers left root-owned directories in the home cache. Fix it on each
node:

```sh
sudo chown -R "$USER:$USER" ~/.cache ~/.triton
```

**`rank zero rejected rank 1: source identity differs`.** The two checkouts
differ, even by one local line. Make `git status` clean and the commit equal on
both nodes.

**`BrokenPipeError` at "Load weights"** (Flare `947a5a6d`, `fea9d61d` and
`df8c2ab6`, 2026-10-22). Not solved. The ranks connect and the checkpoint is
inspected; then rank 1's weight load from rank 0 over a socket
(`RemoteWeightSession`) fails, even with its own copy on disk. Rank 0 logs
only `a tensor-parallel peer disconnected before shutdown`. See #1207.

**`RuntimeError: tuning races differ across ranks`** (Flare `5f37bd1`, with
FlashInfer reverted to `622cec10`, checkpoint `5b865baa`). Not solved. The
weights load in 50 s; then the two ranks' sets of 17 tuning-race keys do not
overlap. It is deterministic: clearing `~/.cache/flare` and `~/.cache/b12x`
and freeing memory did not change it. See #1207.
