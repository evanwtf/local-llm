# Stand up a single-Spark pick, step by step

**Written 2026-10-22T08:40:00-0400 for [#1202](https://github.com/evanwtf/local-llm/issues/1202).**
This page covers the single-Spark setups in
[`RECOMMENDATIONS.md`](RECOMMENDATIONS.md), sections 2 and 4. Each section is a
numbered procedure for one NVIDIA DGX Spark (GB10, 128 GB). The shared steps
are in [`docs/howto.md`](../../docs/howto.md).

A **stack** is a model, an engine and a harness. The harness is OpenCode for
every row here. The Spark runs the model server. OpenCode runs on a second
machine (a laptop) and reaches the Spark over the network.

Each command comes from a file in this repository, or from the server command
line that the benchmark recorded on each result row
(`results.jsonl` → `env.server_argv`). The source is named under each
section. Where the repository does not record a step, the page says so.
Paths that the rows recorded under a home directory are written as `$HOME`
here.

## Before any stack

### On the Spark

The rows ran on DGX OS 7 (Ubuntu 24.04, aarch64), NVIDIA driver 580, CUDA
13.0 ([`../Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md`](../Cortex-X925-128GB-GB10-x2/RECOMMENDATIONS.md),
section 1; [`VERSIONS.md`](VERSIONS.md)). DGX OS ships the driver, CUDA,
Docker and the NVIDIA Container Toolkit.

1. Check the GPU and the CUDA compiler:

   ```sh
   nvidia-smi            # expect "NVIDIA GB10"
   /usr/local/cuda/bin/nvcc --version
   ```

2. Let your user run Docker without `sudo`. Then log out and log in again:

   ```sh
   sudo usermod -aG docker "$USER"
   docker ps             # must work without sudo
   ```

3. Install the out-of-memory guard. The 128 GB pool is shared by CPU and GPU.
   When it runs out, the kernel can kill `sshd` and lock you out
   ([`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md)):

   ```sh
   sudo apt-get install -y earlyoom git
   ```

   Step 6 applies this repo's configuration to it.

4. Install `uv` and the Hugging Face CLI (`hf`). This step is general setup,
   not a command the repo records:

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv tool install "huggingface_hub[cli]"
   hf --version
   ```

5. Clone this repo:

   ```sh
   git clone https://github.com/evanwtf/local-llm ~/git/local-llm
   cd ~/git/local-llm && uv sync
   ```

6. Apply the earlyoom configuration. It kills the largest process at 1.0 GiB
   free, and never `sshd`:

   ```sh
   cd ~/git/local-llm && sudo -E "$(command -v uv)" run python scripts/setup_earlyoom.py --apply
   uv run python scripts/setup_earlyoom.py      # check: no drift reported
   ```

7. Find the Spark's network address. The laptop uses it as `<spark-address>`:

   ```sh
   hostname -I
   ```

**Run one model server at a time.** Two servers do not fit in one 128 GB pool.
Stop one, and wait until `free -g` shows the memory back, before you start
the next.

### On the laptop

1. Install OpenCode:

   ```sh
   curl -fsSL https://opencode.ai/install | bash
   ```

2. Create `~/.config/opencode/opencode.json` with the provider block from the
   stack's section. The general shape is:

   ```json
   {
     "$schema": "https://opencode.ai/config.json",
     "model": "spark/MODEL",
     "provider": {
       "spark": {
         "name": "DGX Spark (LAN)",
         "npm": "@ai-sdk/openai-compatible",
         "options": { "baseURL": "http://<spark-address>:PORT/v1", "apiKey": "local" },
         "models": { "MODEL": { "tool_call": true, "reasoning": false } }
       }
     }
   }
   ```

   Set `"reasoning": true` only for a thinking-on row (row 9).

### The check that every stack must pass

`/health` and `/v1/models` do not prove that a model answers. From the laptop,
send one real chat request. Replace `PORT` and `MODEL` with the stack's
values:

```sh
curl -s http://<spark-address>:PORT/v1/chat/completions \
  -H 'Content-Type: application/json' -H 'Authorization: Bearer local' \
  -d '{"model":"MODEL","max_tokens":1024,
       "messages":[{"role":"user","content":"What is 2+2? Reply with the number only."}]}'
```

`choices[0].message.content` must contain `4`. An empty `content` with
`finish_reason` `length` means the model spent the budget on reasoning:
send a larger `max_tokens` ([#349](https://github.com/evanwtf/local-llm/issues/349)).

### Run the agent

On the laptop. **`--dir` is not optional.** Without it, OpenCode writes the
files somewhere else:

```sh
cd ~/some/project
opencode run --dir "$PWD" "add a --verbose flag to the CLI and a test for it"
```

### Build llama.cpp for GB10 (rows 3, 4, 5 and 9)

The flags come from the CMake cache of the `b11669` build that served row 5.

1. Install the build tools:

   ```sh
   sudo apt-get install -y cmake build-essential
   ```

2. Clone and build one release tag. Use `b11669` for row 5:

   ```sh
   TAG=b11669
   git clone https://github.com/ggml-org/llama.cpp ~/engines/llama.cpp-$TAG
   cd ~/engines/llama.cpp-$TAG && git checkout $TAG
   cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121 \
     -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF
   cmake --build build -j 8
   ```

   The project's own Spark builds come from the `evanwtf/llama.cpp` fork. It
   only adds a Prometheus `model` label
   ([`docs/dgx-spark-runbook.md`](../../docs/dgx-spark-runbook.md)). You do not
   need it to serve.

---

## Row 1. Qwen3.6-35B-A3B NVFP4 on SGLang (fastest agent)

Download: about 22 GiB. Measured: 90/90.
Source: [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) section 1;
`tasks.toml` → `[backend.qwen36a3bsglangdgx]` and
`[backend.qwen36a3bsglang1017dgx]`.

1. On the Spark, start SGLang's official image, pinned by digest. It downloads
   the weights into your Hugging Face cache on first start. The default MoE
   runner refuses NVFP4 on GB10, so `--moe-runner-backend flashinfer_cutlass`
   is required ([#553](https://github.com/evanwtf/local-llm/issues/553)):

   ```sh
   docker run --rm --name a3b-sglang --gpus all --network host --ipc=host \
     --user "$(id -u):$(id -g)" -e HOME=/tmp/home -e HF_HOME=/hf \
     -v "$HOME/.cache/huggingface:/hf" \
     lmsysorg/sglang@sha256:00205b89f74691f76a0ffbd6846376d9323971930a5d59bf63a65dadc7d67927 \
     python3 -m sglang.launch_server \
     --model-path nvidia/Qwen3.6-35B-A3B-NVFP4 --served-model-name qwen3.6-35b-a3b-nvfp4 \
     --host 0.0.0.0 --port 8030 --context-length 262144 --kv-cache-dtype fp8_e4m3 \
     --mem-fraction-static 0.60 --max-running-requests 8 \
     --moe-runner-backend flashinfer_cutlass \
     --reasoning-parser qwen3 --tool-call-parser qwen3_coder \
     --default-chat-template-kwargs '{"enable_thinking":false}'
   ```

   The 2026-10-20 refresh ([#1087](https://github.com/evanwtf/local-llm/issues/1087))
   used image `lmsysorg/sglang:nightly-cu134-20261017-a126e8d` and
   `--mem-fraction-static 0.85`, on a Spark that ran nothing but the server.
   The weights were revision `1355db6a052410cfd62085d94b58866fd0f2c3c5`.
2. Wait for the log line that says the server is ready.
3. From the laptop, do [the check](#the-check-that-every-stack-must-pass) with
   `PORT=8030`, `MODEL=qwen3.6-35b-a3b-nvfp4`.
4. Use the provider block with `PORT=8030` and `MODEL=qwen3.6-35b-a3b-nvfp4`.
5. [Run the agent](#run-the-agent).
6. Stop: `docker stop a3b-sglang`.

## Row 2. Qwen3.6-35B-A3B NVFP4 on vLLM 0.29.0 (pip-installed engine)

Download: about 24 GiB. Measured: 379/402.
Source: [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) section 4; the recorded
argv of `qwen36a3bnvfp4dgx` (vLLM 0.29.0).

1. On the Spark, create a virtual environment with vLLM 0.29.0 and `ninja`.
   **The repo does not record this install command.** It records only that
   `~/venvs/vllm` holds the `vllm` 0.29.0 package from pip
   ([`docs/external-repos.md`](../../docs/external-repos.md)):

   ```sh
   uv venv ~/venvs/vllm --python 3.12
   uv pip install --python ~/venvs/vllm/bin/python "vllm==0.29.0" ninja
   ```

2. Start the server. `nvcc` and `ninja` must be on `PATH`. Without them the
   model loads and then dies at the FlashInfer JIT step:

   ```sh
   PATH="$HOME/venvs/vllm/bin:/usr/local/cuda/bin:$PATH" CUDACXX=/usr/local/cuda/bin/nvcc \
   vllm serve nvidia/Qwen3.6-35B-A3B-NVFP4 \
     --served-model-name qwen3.6-35b-a3b-nvfp4 --host 0.0.0.0 --port 8030 \
     --gpu-memory-utilization 0.6 --max-model-len 262144 --max-num-seqs 8 \
     --kv-cache-dtype fp8 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
     --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking": false}'
   ```

3. Do the check with `PORT=8030`, `MODEL=qwen3.6-35b-a3b-nvfp4`.
4. Use the provider block with the same values.
5. [Run the agent](#run-the-agent). This stack misses the `script-transform`
   task often ([#477](https://github.com/evanwtf/local-llm/issues/477)).
6. Stop: Ctrl-C the server. Wait until `free -g` shows the memory back.

## Row 3. Qwen3.8-Flash-Next `UD-Q3_K_XL` on llama.cpp, thinking off (simplest server)

Download: 84 GiB. Measured: 331/331.
Source: the recorded argv of `qwen38fnq3nothinkdgx`.

1. [Build llama.cpp](#build-llamacpp-for-gb10-rows-3-4-5-and-9). The 331/331
   rows ran older builds (`d3f3811` and `481c65f`); the 2026-10-15 refresh
   ran `b11642`.
2. Download the three shards. The repo does not pin their revision:

   ```sh
   hf download unsloth/Qwen3.8-Flash-Next-GGUF --include "UD-Q3_K_XL/*" \
     --local-dir ~/models/Qwen3.8-Flash-Next-GGUF
   ```

3. Start the server. Pass the first shard; llama.cpp finds the rest. Set
   `TAG` to the tag you built (rows 4 and 9 use it too):

   ```sh
   TAG=b11669
   ~/engines/llama.cpp-$TAG/build/bin/llama-server \
     -m ~/models/Qwen3.8-Flash-Next-GGUF/UD-Q3_K_XL/Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf \
     -a qwen3.8-flash-next-q3-nothink -c 131072 -ngl 99 -np 1 --jinja \
     --chat-template-kwargs '{"enable_thinking": false}' \
     --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.05 \
     --host 0.0.0.0 --port 8020 --metrics
   ```

   The refresh ([#1089](https://github.com/evanwtf/local-llm/issues/1089))
   used the model card's non-thinking sampler instead:
   `--temp 0.7 --top-p 0.8 --top-k 20 --min-p 0 --presence-penalty 1.5`.
4. Do the check with `PORT=8020`, `MODEL=qwen3.8-flash-next-q3-nothink`.
5. Use the provider block with the same values.
6. [Run the agent](#run-the-agent).
7. Stop: Ctrl-C `llama-server`.

## Row 4. The same, with a `q8_0` KV cache

Measured: 90/90. Same weights as row 3. The smaller KV cache saves memory and
does not change the pass rate ([#344](https://github.com/evanwtf/local-llm/issues/344)).
Source: `tasks.toml` → `[backend.qwen38fnq3nothinkkv8dgx]` and the #344
comment that names the flags.

1. Do steps 1 and 2 of row 3.
2. Start the server with row 3's command, and change three things: the alias,
   the port, and two KV flags:

   ```sh
   ~/engines/llama.cpp-$TAG/build/bin/llama-server \
     -m ~/models/Qwen3.8-Flash-Next-GGUF/UD-Q3_K_XL/Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf \
     -a qwen3.8-flash-next-q3-nothink-kv8 -c 131072 -ngl 99 -np 1 --jinja \
     --chat-template-kwargs '{"enable_thinking": false}' \
     --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.05 \
     -ctk q8_0 -ctv q8_0 \
     --host 0.0.0.0 --port 8022 --metrics
   ```

3. Do the check with `PORT=8022`, `MODEL=qwen3.8-flash-next-q3-nothink-kv8`.
4. Use the provider block with the same values.
5. [Run the agent](#run-the-agent).
6. Stop: Ctrl-C `llama-server`.

## Row 5. Nemotron-3.5-Bolt-74B-A9B 24GB on llama.cpp with a DFlash drafter (long tasks)

Download: about 23 GiB (model 20.5 GiB, image projector 1.6 GiB, drafter
0.9 GiB). Measured: 30/30 on the standard set, 37/42 on replay + hard.
Source: the recorded argv of `nemotron35boltdflashnothinkrcdgx`; the
revisions are in [#1103](https://github.com/evanwtf/local-llm/issues/1103).

1. [Build llama.cpp](#build-llamacpp-for-gb10-rows-3-4-5-and-9) at `TAG=b11669`
   (commit `620d2225c`).
2. Download the model, its image projector and the drafter, at the measured
   revisions:

   ```sh
   D=~/models/nvidia-nemotron35-bolt-24gb
   hf download nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-GGUF \
     NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB.gguf mmproj-NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB.gguf \
     --revision 188282901e3b6f8169aff9de69022a78350259b0 --local-dir $D
   hf download nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr-GGUF \
     NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr.gguf \
     --revision f640c389aa24ddbe13150ef8753495446a488858 --local-dir $D
   ```

3. Start the server:

   ```sh
   D=~/models/nvidia-nemotron35-bolt-24gb
   ~/engines/llama.cpp-b11669/build/bin/llama-server \
     --model $D/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB.gguf \
     --mmproj $D/mmproj-NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB.gguf \
     --spec-draft-model $D/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-LiLiCorr.gguf \
     --spec-draft-ngl 999 --spec-type draft-dflash --spec-draft-n-max 7 \
     --batch-size 4096 --ubatch-size 2048 --flash-attn on --temp 1.0 --top-p 0.95 \
     --alias nemotron-3.5-bolt-nothink -c 262144 -ngl 999 -np 1 --jinja \
     --host 0.0.0.0 --port 8020 --metrics \
     --chat-template-kwargs '{"enable_thinking":false}'
   ```

4. Do the check with `PORT=8020`, `MODEL=nemotron-3.5-bolt-nothink`.
5. Use the provider block with the same values.
6. [Run the agent](#run-the-agent).
7. Stop: Ctrl-C `llama-server`.

## Row 6. Qwen3.8-27B NVFP4 on SGLang with the DFlash2 drafter (dense model)

Measured: 90/90. Source: `tasks.toml` →
`[backend.qwen3827bsglangdflash2nothinkdgx]` and
`[backend.qwen3827bsglangdflash2nothink1017dgx]`; the recipe's
`start-dflash.sh` at `9fb18ed`, read from a local checkout.

The server comes from a third-party recipe,
[`MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark`](https://github.com/MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark).
It pulls SGLang image `lmsysorg/sglang@sha256:00205b89f746…` and the drafter
`z-lab/Qwen3.8-27B-DFlash2` at `50307d4c4cde6860d4eee73e2547cd786fe8e8a4`.
The target is `RadixArk/Qwen3.8-27B-NVFP4-BF16-LMHead`. The repo does not pin
its revision.

1. On the Spark, clone the recipe at the measured commit:

   ```sh
   git clone https://github.com/MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark ~/engines/miaai-qwen3827b-sglang
   cd ~/engines/miaai-qwen3827b-sglang && git checkout 9fb18ed
   ```

2. Start it. `DF_EXTRA` appends flags to the server command. The recipe ships
   `--mem-fraction-static 0.90`. The measured rows used 0.70, so that an agent
   client fits on the same box ([#350](https://github.com/evanwtf/local-llm/issues/350)).
   They also turned thinking off:

   ```sh
   cd ~/engines/miaai-qwen3827b-sglang
   DF_EXTRA='--mem-fraction-static 0.70 --default-chat-template-kwargs {"enable_thinking":false}' \
     ./start-dflash.sh
   ```

   With the client on the laptop, 0.85 also measured clean. **Do not use
   0.90**: under load it took the Spark down to 6.2 GiB free, below
   earlyoom's line ([#582](https://github.com/evanwtf/local-llm/issues/582)).
3. Do the check with `PORT=8888`, `MODEL=qwen3.8-27b-sglang`.
4. Use the provider block with the same values.
5. [Run the agent](#run-the-agent).
6. Stop: `./stop.sh` in the recipe directory.

## Row 7. Qwen3.8-Flash-Next NVFP4 (NVIDIA's checkpoint) on vLLM, MTP-3 (larger model, 262K context)

Download: 124 GiB, plus a 27 GiB table that the first start builds. Measured:
90/90. Source: `tasks.toml` → `[backend.qwen38fnnvidianvfp4nothinkdgx]`;
[#468](https://github.com/evanwtf/local-llm/issues/468) and
[#493](https://github.com/evanwtf/local-llm/issues/493); the recipe's `.env`
and README, read from the local checkout at `6b50864`.

The server comes from a third-party recipe,
[`MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark`](https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark).
It runs vLLM in the image `vllm/vllm-openai:qwen38-flash-next` with patched
modules.

1. On the Spark, clone the recipe. The repo records the local checkout at
   `6b50864`. It does not record which commit served the #493 rows:

   ```sh
   git clone https://github.com/MiaAI-Lab/Qwen3.8-Flash-Next-Single-DGX-Spark ~/recipes/mia-fn
   cd ~/recipes/mia-fn && git checkout 6b50864
   ```

2. Download NVIDIA's checkpoint with the recipe's script. The rows used
   revision `fc694b54fb0174e0913e6adf86691ef85a4ead47`:

   ```sh
   ./download.sh nvidia/Qwen3.8-Flash-Next-NVFP4
   ```

3. Write `.env`. These are the values that served the rows:

   ```sh
   cat > .env <<'ENV'
   IMAGE="vllm/vllm-openai:qwen38-flash-next"
   SERVED_MODEL_NAME="qwen3.8-flash-next"
   TP1_MODEL_ID="nvidia/Qwen3.8-Flash-Next-NVFP4"
   PLE_GIB=47.68
   MTP_WEIGHTS_GIB=2.34
   HOST_RESERVE_GIB=30
   ABLIT=0
   YARN=0
   MAX_MODEL_LEN=262144
   MTP_NUM_SPECULATIVE_TOKENS=3
   KV_TARGET_GIB=10
   KV_CACHE_DTYPE=fp8
   MAMBA_SSM_CACHE_DTYPE=bfloat16
   MAX_NUM_SEQS=4
   CUDAGRAPH_CAPTURE_SIZES=auto
   MAX_NUM_BATCHED_TOKENS=2048
   MTP_DRAFT_VOCAB=files/draft_vocab_en_code_47k.txt
   CHAT_TEMPLATE=
   MTP_K_SCHEDULE=
   COMPILATION_MODE=0
   EXTRA_DOCKER_ARGS="-e VLLM_USE_V2_MODEL_RUNNER=1"
   HOST_SLACK_GIB=5
   PORT=8888
   BIND=0.0.0.0
   EXTRA_VLLM_ARGS="--default-chat-template-kwargs.enable_thinking=false"
   ENV
   ```

   The repo does not record whether `docker pull` finds the image tag. If
   `start.sh` cannot find it, follow the recipe README.
4. Print the plan without starting anything, then start:

   ```sh
   ./start.sh --no-launch
   ./start.sh
   ```

   The first start takes 10 to 12 minutes.
5. Do the check with `PORT=8888`, `MODEL=qwen3.8-flash-next`.
6. Use the provider block with the same values.
7. [Run the agent](#run-the-agent). For a predictable worst case, remove the
   `EXTRA_VLLM_ARGS` line to turn thinking on: 90/90 at a 120.8 s median
   ([#493](https://github.com/evanwtf/local-llm/issues/493)).
8. Stop: `./stop.sh`.

## Row 8. Nemotron-3.5-Lightning-30B-A3B NVFP4 on TensorFold 1.0.11 (non-Qwen lineage)

Measured: 30/30. Source: the recorded argv of `nemotron35ltf1011c262rcdgx`;
`tasks.toml`; the release's own `RUNTIME.md`.

1. Download the TensorFold release for Linux aarch64 and check its hash:

   ```sh
   mkdir -p ~/engines/tensorfold-dl-1.0.11 && cd ~/engines/tensorfold-dl-1.0.11
   gh release download v1.0.11 -R ashhart/TensorFold \
     -p 'tensorfold-1.0.11-linux-aarch64.tar.gz*'
   sha256sum -c tensorfold-1.0.11-linux-aarch64.tar.gz.sha256
   tar xzf tensorfold-1.0.11-linux-aarch64.tar.gz
   ./tensorfold-1.0.11-linux-aarch64/bin/tensorfold-native --version
   ```

   The measured archive's sha256 is
   `e8f5ba3676a56e991ec6c4851cc6a58273956214c42e8027143f9cacdc1a4d6e`. Without
   `gh`, download the same files from the release page.
2. Download the weights at the measured revision:

   ```sh
   hf download nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 \
     --revision bee7596271d1495f6992ae224aefde4410e816b8
   ```

3. Start the server on the snapshot directory:

   ```sh
   SNAP=$HOME/.cache/huggingface/hub/models--nvidia--NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4/snapshots/bee7596271d1495f6992ae224aefde4410e816b8
   ~/engines/tensorfold-dl-1.0.11/tensorfold-1.0.11-linux-aarch64/bin/tensorfold-native serve "$SNAP" \
     --host 0.0.0.0 --port 8030 --alias nemotron-3.5-lightning-30b-a3b --api-key local \
     --metrics-open --context 262144 --no-thinking --no-update-check
   ```

4. Do the check with `PORT=8030`, `MODEL=nemotron-3.5-lightning-30b-a3b`. The
   server requires the key `local`; the check sends it.
5. Use the provider block with the same values.
6. [Run the agent](#run-the-agent).
7. Stop: Ctrl-C the server.

## Row 9. Qwen3.8-Flash-Next `UD-Q3_K_XL` on llama.cpp, thinking on (shows its work)

Measured: 118/119. The wall time is 193% of row 3's.
Source: the recorded argv of `qwen38fnq3rcdgx`.

1. Do steps 1 and 2 of row 3.
2. Start the server without the thinking-off flag:

   ```sh
   ~/engines/llama.cpp-$TAG/build/bin/llama-server \
     -m ~/models/Qwen3.8-Flash-Next-GGUF/UD-Q3_K_XL/Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf \
     --alias qwen3.8-flash-next-q3 -c 131072 -ngl 99 -np 1 --jinja \
     --host 0.0.0.0 --port 8020 --metrics
   ```

   The rows ran with the GGUF's own sampler: temperature 1.0, top-p 0.95,
   top-k 20, min-p 0.05 (read back from the server).
3. Do the check with `PORT=8020`, `MODEL=qwen3.8-flash-next-q3`. Keep
   `max_tokens` at 1024 or more.
4. Use the provider block with the same values and `"reasoning": true`.
5. [Run the agent](#run-the-agent).
6. Stop: Ctrl-C `llama-server`.

---

## What this page could not confirm from the repo

- The command that installed vLLM 0.29.0 in `~/venvs/vllm` (row 2).
- A pinned revision for the Unsloth GGUF shards (rows 3, 4, 9) and for
  `RadixArk/Qwen3.8-27B-NVFP4-BF16-LMHead` (row 6).
- The recipe commit that served row 7's measured rows, and whether its image
  tag is on Docker Hub.
- The `uv` and `hf` install steps. They are general setup.
