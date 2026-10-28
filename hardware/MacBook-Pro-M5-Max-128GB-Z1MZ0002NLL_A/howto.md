# Stand up an M5 Max pick, step by step

**Written 2026-10-22T08:40:00-0400 for [#1202](https://github.com/evanwtf/local-llm/issues/1202).**
This page covers the six rows of the M5 Max picks in
[`RECOMMENDATIONS.md`](RECOMMENDATIONS.md), section 2. Each section is a
numbered procedure that starts on a clean Mac. The shared steps are in
[`docs/howto.md`](../../docs/howto.md).

A **stack** is a model, an engine and a harness. The harness is OpenCode for
every row here.

Each command comes from a file in this repository, named under each section.
Where the repository does not record a step, the page says so in that step.

## Before any stack

You need an Apple Silicon Mac with 128 GB of memory and macOS 26 or 27. The
rows were measured on an M5 Max MacBook Pro, 128 GB, macOS 27.

1. Install Homebrew, if `brew --version` fails. Follow the command on
   <https://brew.sh>.
2. Install OpenCode:

   ```sh
   curl -fsSL https://opencode.ai/install | bash
   ```

3. Install `uv`. The tool-format shim (row 4) and the `hf` download tool need it:

   ```sh
   brew install uv
   ```

4. Install the Hugging Face CLI as a `uv` tool. This gives you the `hf` command:

   ```sh
   uv tool install "huggingface_hub[cli]"
   hf --version
   ```

   The repo does not record how `hf` was installed on the M5 Max.
   [`docs/stacks.md`](../../docs/stacks.md) uses
   `pip install -U "huggingface_hub[cli]"` instead.

5. For rows 3 to 6, raise the GPU memory ceiling. macOS does not let the GPU
   hold an 84 GB model by default. The failure looks like a model that will
   not load:

   ```sh
   sudo sysctl iogpu.wired_limit_mb=114688
   sysctl -n iogpu.wired_limit_mb          # expect 114688
   ```

   The setting does not survive a reboot. To make it permanent, clone this
   repo and run `scripts/install-metal-ceiling.sh`
   (source: [`docs/stacks.md`](../../docs/stacks.md), "the memory ceiling").

6. Clone this repo. Row 4 runs a shim from it, and the stop steps use it:

   ```sh
   git clone https://github.com/evanwtf/local-llm ~/git/local-llm
   ```

### The check that every stack must pass

`/health` and `/v1/models` do not prove that a model answers. Send one real
chat request. Replace `PORT`, `MODEL` and `TOKEN` with the values in the
stack's section:

```sh
curl -s http://127.0.0.1:PORT/v1/chat/completions \
  -H 'Content-Type: application/json' -H 'Authorization: Bearer TOKEN' \
  -d '{"model":"MODEL","max_tokens":1024,
       "messages":[{"role":"user","content":"What is 2+2? Reply with the number only."}]}'
```

The reply is JSON. `choices[0].message.content` must contain `4`. An empty
`content` with `finish_reason` `length` means the model spent the budget on
reasoning: send a larger `max_tokens`
([#349](https://github.com/evanwtf/local-llm/issues/349)).

### Run the agent

Every stack ends with the same command. **`--dir` is not optional.** Without
it, OpenCode writes the files somewhere else
([`RECOMMENDATIONS.md`](RECOMMENDATIONS.md), section 1):

```sh
cd ~/some/project
opencode run --dir "$PWD" "add a --verbose flag to the CLI and a test for it"
```

---

## Row 1. Qwen3.6-27B-coding `mxfp8` on Ollama (starting out)

Download: 31 GB. Measured: 24/24.
Source: [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) section 1;
`benchmarks/agent/tasks.toml` → `[backend.qwen36coding]`. The last OpenCode row
ran Ollama 0.34.3.

1. Install Ollama and start it:

   ```sh
   brew install ollama
   ollama serve &
   ```

2. Pull the model:

   ```sh
   ollama pull qwen3.6:27b-coding-mxfp8
   ```

3. Check it. Use `PORT=11434`, `MODEL=qwen3.6:27b-coding-mxfp8`,
   `TOKEN=ollama` in [the check](#the-check-that-every-stack-must-pass).
4. Write the OpenCode config:

   ```sh
   mkdir -p ~/.config/opencode
   cat > ~/.config/opencode/opencode.json <<'JSON'
   {
     "$schema": "https://opencode.ai/config.json",
     "model": "ollama/qwen3.6:27b-coding-mxfp8",
     "provider": {
       "ollama": {
         "name": "ollama (local)",
         "npm": "@ai-sdk/openai-compatible",
         "options": { "baseURL": "http://127.0.0.1:11434/v1", "apiKey": "ollama" },
         "models": { "qwen3.6:27b-coding-mxfp8": { "tool_call": true } }
       }
     }
   }
   JSON
   ```

5. [Run the agent](#run-the-agent).
6. Stop: run `ollama stop qwen3.6:27b-coding-mxfp8`, then stop the
   `ollama serve` job (`kill %1` in the same shell). To free the disk, run
   `ollama rm qwen3.6:27b-coding-mxfp8`.

## Row 2. Ornith-1.5-35B-A3B `Q4_K_M` on Ollama (smallest)

Download: 22 GB. Measured: 60/66. **This stack has shipped wrong code.** Read
what it writes ([#45](https://github.com/evanwtf/local-llm/issues/45)).
Source: `tasks.toml` → `[backend.ornith15]`. The last OpenCode row ran Ollama
0.34.2.

1. Do steps 1 and 2 of row 1, with this pull instead:

   ```sh
   ollama pull ornith-1.5:35b
   ```

2. Check it. Use `PORT=11434`, `MODEL=ornith-1.5:35b`, `TOKEN=ollama`.
3. Use the row 1 config. Replace both `qwen3.6:27b-coding-mxfp8` strings with
   `ornith-1.5:35b`.
4. [Run the agent](#run-the-agent).
5. Stop: `ollama stop ornith-1.5:35b`, then stop `ollama serve`.

## Row 3. Qwen3.8-Flash-Next MLX mixed-4/8-bit on mlx-serve (fastest)

Download: about 100 GiB. Memory: about 100 GiB resident. Measured: 432/436.
Source: `tasks.toml` → `[backend.qwen38fnmlxserve]`; the server argv is
`scripts/lib/stack_arm.py` (the `mlx-serve` branch); the pack name is in
[`docs/m5max-runbook.md`](../../docs/m5max-runbook.md). The rows ran mlx-serve
26.9.1 to 26.9.6 from Homebrew.

1. Do step 5 of [Before any stack](#before-any-stack) (the GPU ceiling).
2. Install mlx-serve. **This step is not in this repo.** It comes from the
   mlx-serve README (<https://github.com/ddalcu/mlx-serve>, read
   2026-10-22):

   ```sh
   brew tap ddalcu/mlx-serve https://github.com/ddalcu/mlx-serve
   brew install mlx-serve
   mlx-serve --version
   ```

3. Download the pack. It lands in `~/.mlx-serve/models/`, not in `~/models`:

   ```sh
   mlx-serve pull ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit
   mlx-serve list
   ```

   The repo does not record a pinned revision for this pack.
4. Start the server. Prompt-lookup speculation is on by default; leave it on.
   That is the measured state ([#262](https://github.com/evanwtf/local-llm/issues/262)):

   ```sh
   mlx-serve --model ~/.mlx-serve/models/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit \
     --serve --host 127.0.0.1 --port 11234 --ctx-size 100000 --kv-quant off
   ```

5. Check it. Use `PORT=11234`,
   `MODEL=Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit`, `TOKEN=local`.
6. Add this provider to `~/.config/opencode/opencode.json`, and set
   `"model": "mlxserve/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit"`:

   ```json
   "mlxserve": {
     "name": "mlx-serve (local)",
     "npm": "@ai-sdk/openai-compatible",
     "options": { "baseURL": "http://127.0.0.1:11234/v1", "apiKey": "local" },
     "models": { "Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit": { "tool_call": true } }
   }
   ```

7. [Run the agent](#run-the-agent).
8. Stop: press Ctrl-C in the server's terminal. Then confirm that no
   `mlx-serve --model` process holds memory (Activity Monitor, or
   `ps -axo pid,rss,command | grep '[m]lx-serve --model'`).

## Row 4. Qwen3.8-Flash-Next `qwen38-q4k` on ds4 upstream main (lighter engine)

Download: 165 GiB, one file. 95 GiB of it stays on disk and is read on
demand, so keep it on a fast internal SSD. Measured: 178/180.
Source: [`docs/stacks.md`](../../docs/stacks.md), "ds4 upstream main";
`tasks.toml` → `[backend.qwen38fnds4main]`. The rows ran ds4 `8db1d1d1` and
`0aaea5a2`.

1. Do step 5 of [Before any stack](#before-any-stack). Install Xcode's command
   line tools if `make --version` fails: `xcode-select --install`.
2. Build ds4 from upstream main. Record the commit you build:

   ```sh
   git clone https://github.com/antirez/ds4.git ~/git/ds4
   cd ~/git/ds4
   make
   git rev-parse --short HEAD
   ```

3. Download the weights with ds4's own script. It writes to `gguf/`:

   ```sh
   cd ~/git/ds4
   ./download_model.sh qwen38-q4k
   ```

4. Start the server from inside the ds4 tree. ds4 finds its Metal shaders by
   the working directory:

   ```sh
   cd ~/git/ds4
   ./ds4-server --metal \
     -m gguf/Qwen3.8-Flash-Next-Q4.gguf \
     --ctx 100000 --warm-weights \
     --kv-disk-dir ~/.ds4/server-kv --kv-disk-space-mb 8192 \
     --host 127.0.0.1 --port 8000
   ```

5. In a second terminal, start the tool-format shim. OpenCode talks to the
   shim, not to ds4. Both halves are required
   ([#112](https://github.com/evanwtf/local-llm/issues/112)):

   ```sh
   cd ~/git/local-llm
   uv run python ds4_qwen_tool_shim.py --upstream http://127.0.0.1:8000 --port 8101
   ```

6. Check it through the shim. Use `PORT=8101`, `MODEL=qwen3.8-flash-next-q4`,
   `TOKEN=dsv4-local`.
7. Add this provider, and set `"model": "ds4qwenshim/qwen3.8-flash-next-q4"`:

   ```json
   "ds4qwenshim": {
     "name": "ds4 via the Qwen tool shim (local)",
     "npm": "@ai-sdk/openai-compatible",
     "options": { "baseURL": "http://127.0.0.1:8101/v1", "apiKey": "dsv4-local" },
     "models": { "qwen3.8-flash-next-q4": { "tool_call": true } }
   }
   ```

8. [Run the agent](#run-the-agent). **Do not add ds4's MTP flags.** They never
   draft under OpenCode ([#151](https://github.com/evanwtf/local-llm/issues/151)).
9. Stop: Ctrl-C the shim, then Ctrl-C `ds4-server`.

## Row 5. Qwen3.8-Flash-Next `UD-Q3_K_XL` on llama.cpp (mainline engine)

Download: 84 GiB. Measured: 135/135.
Source: [`docs/stacks.md`](../../docs/stacks.md), "llama.cpp — the mainline
fallback"; `tasks.toml` → `[backend.qwen38fnq3]`. The last OpenCode rows ran
llama.cpp `8019dc563` (build b11254).

1. Do step 5 of [Before any stack](#before-any-stack).
2. Build llama.cpp with Metal:

   ```sh
   brew install cmake
   git clone https://github.com/ggml-org/llama.cpp ~/git/llama.cpp
   cmake -B ~/git/llama.cpp/build -S ~/git/llama.cpp -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release
   cmake --build ~/git/llama.cpp/build -j "$(sysctl -n hw.ncpu)"
   ```

3. Download the three shards. The repo does not pin a revision for them:

   ```sh
   hf download unsloth/Qwen3.8-Flash-Next-GGUF --include "UD-Q3_K_XL/*" \
     --local-dir ~/models/Qwen3.8-Flash-Next-GGUF
   ```

4. Start the server. Pass the first shard; llama.cpp finds the rest:

   ```sh
   ~/git/llama.cpp/build/bin/llama-server \
     -m ~/models/Qwen3.8-Flash-Next-GGUF/UD-Q3_K_XL/Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf \
     -a qwen3.8-flash-next-q3 --host 127.0.0.1 --port 8020 \
     -c 131072 -np 1 --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0
   ```

   `-np 1` and `-c 131072` are not decoration. At 65,536 tokens a 110 s task
   took 842 s. The sampler is Qwen's published one
   ([`docs/stacks.md`](../../docs/stacks.md)).
5. Check it. Use `PORT=8020`, `MODEL=qwen3.8-flash-next-q3`, `TOKEN=local`.
6. Add this provider, and set `"model": "llamacpp/qwen3.8-flash-next-q3"`:

   ```json
   "llamacpp": {
     "name": "llama.cpp (local)",
     "npm": "@ai-sdk/openai-compatible",
     "options": { "baseURL": "http://127.0.0.1:8020/v1", "apiKey": "local" },
     "models": { "qwen3.8-flash-next-q3": { "tool_call": true } }
   }
   ```

7. [Run the agent](#run-the-agent).
8. Stop: Ctrl-C `llama-server`.

## Row 6. DeepSeek-V4-Flash on ds4 (second lineage)

Download: about 91 GB. Measured: 30/30, all on 2026-09-01 with OpenCode
1.18.25.
Source: [`docs/stacks.md`](../../docs/stacks.md), "DeepSeek-V4-Flash on ds4";
`tasks.toml` → `[backend.ds4]`; `scripts/local_agent.py` → the `lineage` stack.

1. Do step 5 of [Before any stack](#before-any-stack).
2. Build ds4 and download the weights with its script. Follow its prompts:

   ```sh
   git clone https://github.com/antirez/ds4 ~/git/ds4
   cd ~/git/ds4 && make -j "$(sysctl -n hw.ncpu)"
   ./download_model.sh
   ls gguf/
   ```

   **The measured engine was a fork.** `tasks.toml` records the tree
   `ds4-ivan-qwen38fn` (`ivanfioravanti/ds4`, branch `qwen3.8-flash-next`),
   and `scripts/local-agent.sh lineage` builds that fork. Upstream main is
   not measured for this row.
3. Start the server from inside the ds4 tree. Use the file that the script
   downloaded. In `local_agent.py` it is
   `DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf`:

   ```sh
   cd ~/git/ds4
   ./ds4-server -m gguf/<the-file-it-downloaded>.gguf --ctx 100000 --port 8000
   ```

4. Check it. Use `PORT=8000`, `MODEL=deepseek-v4-flash`, `TOKEN=local`.
5. Add this provider, and set `"model": "ds4/deepseek-v4-flash"`:

   ```json
   "ds4": {
     "name": "ds4 (local)",
     "npm": "@ai-sdk/openai-compatible",
     "options": { "baseURL": "http://127.0.0.1:8000/v1", "apiKey": "local" },
     "models": { "deepseek-v4-flash": { "tool_call": true } }
   }
   ```

6. [Run the agent](#run-the-agent).
7. Stop: Ctrl-C `ds4-server`.

---

## The one-script path

`scripts/local-agent.sh` does rows 1, 5 and 6 for you, and asks before each
download ([`RECOMMENDATIONS.md`](RECOMMENDATIONS.md), section 3):

```sh
cd ~/git/local-llm
scripts/local-agent.sh starter      # row 1
scripts/local-agent.sh mainline     # row 5
scripts/local-agent.sh lineage      # row 6
```

**Its `fast` stack is not row 3.** `fast` starts Qwen3.8-Flash-Next on the
older ds4 `kimat` fork with a PLE sidecar. The script has no mlx-serve stack
and no ds4 upstream-main stack.
