# Stand up a two-Spark pick, step by step

**Written 2026-10-22T08:40:00-0400 for [#1202](https://github.com/evanwtf/local-llm/issues/1202).**
This page covers the four stacks in "The short answer" of
[`RECOMMENDATIONS.md`](RECOMMENDATIONS.md): the pick, the fast option, the
stack with the most hidden tests passed, and the image-input stack. Each
section is a numbered procedure for two NVIDIA DGX Sparks joined by their
ConnectX-7 ports. The shared steps are in
[`docs/howto.md`](../../docs/howto.md).

A **stack** is a model, an engine and a harness. The harness is OpenCode for
every stack here. The two Sparks run one model server, tensor-parallel across
both (TP=2). OpenCode runs on a third machine (a laptop) and reaches the head
node over the network.

Each command comes from this repository, from the server command line that the
benchmark recorded on each result row (`results.jsonl` → `env.server_argv`),
or from the recipe's own README at the commit we ran. The source is named
under each section. Where the repository does not record a step, the page
says so.

**Names used on this page:**

- `<head-node>`: the Spark that serves the API (rank 0). You run every
  command on it unless a step says otherwise.
- `<worker-node>`: the second Spark (rank 1).
- `10.0.0.1` and `10.0.0.2`: the head's and the worker's addresses on the
  ConnectX-7 link. Use your own.
- `<user>`: your account name. It must be the same on both Sparks.

**Start order.** A two-node server starts the worker rank first, then the
head rank ([`docs/dgx-cluster-howto.md`](../../docs/dgx-cluster-howto.md),
section 10). Every recipe below does this for you from the head. You do not
start the worker by hand.

## Before any stack

1. Build the cluster with [`docs/dgx-cluster-howto.md`](../../docs/dgx-cluster-howto.md),
   sections 0 to 7. It covers the cable, the fabric addresses, the same uid on
   both nodes, key-based SSH in both directions, and the NCCL check. Do not go
   on until its section 7 passes.
2. On **both** nodes, do steps 1 to 6 of
   [the single-Spark setup](../Cortex-X925-128GB-GB10/howto.md#on-the-spark):
   GPU check, Docker without `sudo`, earlyoom, `uv` and `hf`, this repo, and
   the earlyoom configuration. Keep earlyoom running at its 1.0 / 0.5 GiB
   lines on both nodes ([#700](https://github.com/evanwtf/local-llm/issues/700)).
3. On the head, check that SSH to the worker needs no password:

   ```sh
   ssh -o BatchMode=yes <user>@10.0.0.2 true && echo ok
   ```

4. Check free disk on both nodes. Each stack lists what it needs:

   ```sh
   df -h ~
   ```

5. On the laptop, install OpenCode:

   ```sh
   curl -fsSL https://opencode.ai/install | bash
   ```

**Run one model server at a time.** Stop the old one with its own stop
command. Do not use `pkill`.

### The check that every stack must pass

`/health` passes with a dead worker rank. Send one real chat request from the
laptop. Replace `MODEL` with the stack's served name:

```sh
curl -s http://<head-node>:8888/v1/chat/completions \
  -H 'Content-Type: application/json' -H 'Authorization: Bearer local' \
  -d '{"model":"MODEL","max_tokens":2048,"reasoning_effort":"low",
       "messages":[{"role":"user","content":"What is 2+2? Reply with the number only."}]}'
```

`choices[0].message.content` must contain `4`. These models reason first.
If `content` is empty and `finish_reason` is `length`, send a larger
`max_tokens`. Then compare `MemAvailable` on both nodes
(`grep MemAvailable /proc/meminfo`). If one node holds everything and the
other is near empty, the split is wrong.

### Run the agent

On the laptop. **`--dir` is not optional.** Without it, OpenCode writes the
files somewhere else:

```sh
cd ~/some/project
opencode run --dir "$PWD" "add a --verbose flag to the CLI and a test for it"
```

---

## The pick: DeepSeek-V4.1-Flash-1017 EXL3 2.7 bpw on vLLM + ExLlamaV3

Disk: about 430 GB on each node, at the same path. Measured: 41/41 on replay
+ hard, 30/30 on the standard set, 369 of 380 hidden tests
([#1160](https://github.com/evanwtf/local-llm/issues/1160)).
Source: [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) section 6; `tasks.toml` →
`[backend.dsv41f1017exl3serodual2xrc]`; the recorded argv; the stack's README
and `scripts/launch/dsv41_tp2.sh` at `cc59c360`.

The engine is [`sudoingX/dgx-spark-deepseek-v41`](https://github.com/sudoingX/dgx-spark-deepseek-v41):
vLLM `6e517b15` for sm_121, exllamav3 1.6.0 and its patch series. Its
`install.sh` builds the image `dsv41-serve:v4` locally.

1. On **both** nodes, clone the stack at the measured commit. Its scripts
   expect `~/dsv41_recipe`:

   ```sh
   git clone https://github.com/sudoingX/dgx-spark-deepseek-v41 ~/dsv41_recipe
   cd ~/dsv41_recipe && git checkout cc59c360
   ```

2. On the head, build the images. Nothing else heavy may run on either node.
   `install.sh` refuses while any GPU container runs. It copies the serving
   image to the worker:

   ```sh
   cd ~/dsv41_recipe && PEER=<worker-node> ./install.sh
   HEAD=<head-node> WORKER=<worker-node> ./check.sh     # read-only; PASS / WARN / FAIL per check
   ```

3. On **both** nodes, download the weights to the same absolute path. The
   Engram tables stay in DeepSeek's own shards 47 and 48:

   ```sh
   hf download 0xSero/DeepSeek-V4.1-Flash-1017-EXL3-2.7bpw \
     --revision e34ef55bda7c37d17d8dcc86a8a64e7cb875481e --local-dir ~/models/dsv41-1017-exl3
   hf download deepseek-ai/DeepSeek-V4.1-Flash-1017 \
     model-00047-of-00048.safetensors model-00048-of-00048.safetensors \
     --revision 1bae0e1088993b9b82de08f59de5be5a34e42170 --local-dir ~/models/dsv41-1017-exl3
   ```

   Instead of a second download, you can copy the folder to the worker over
   the link with `rsync` (the stack's README).
4. On the head, start both ranks. The launcher starts rank 1 on the worker,
   then rank 0 on the head, and waits for `/v1/models`. Set reasoning effort
   `low` on the server, because the 1017 template defaults to high.
   `FAB_IF` and `FAB_HCA` name the fabric interface and its RoCE device; the
   defaults are `enp1s0f0np0` and `rocep1s0f0`. Set them if your cable is on
   the other port:

   ```sh
   cd ~/dsv41_recipe
   HEAD=<head-node> WORKER=<worker-node> PORT=8888 SERVED=DeepSeek-V4.1-Flash-1017-EXL3 \
     MODEL_DIR=$HOME/models/dsv41-1017-exl3 \
     EXTRA='--default-chat-template-kwargs {"reasoning_effort":"low"}' \
     ./scripts/launch/dsv41_tp2.sh up
   ```

   The first boot took 537 s. The launcher drops the page cache with
   `sudo -n`; without passwordless `sudo` it prints a warning and goes on.
5. Check the recorded head command line if something differs. It was:
   `vllm serve <model dir> --served-model-name DeepSeek-V4.1-Flash-1017-EXL3
   --tensor-parallel-size 2 --nnodes 2 --gpu-memory-utilization 0.88
   --max-model-len 262144 --max-num-seqs 2 --max-num-batched-tokens 2048
   --speculative-config {"method":"dspark","num_speculative_tokens":5}
   --kv-cache-memory-bytes 2684354560 --limit-mm-per-prompt {"image":0}
   --tool-call-parser deepseek_v41 --reasoning-parser deepseek_v41
   --default-chat-template-kwargs {"reasoning_effort":"low"}`, plus the
   launcher's Engram, graph and kernel settings.
6. Do [the check](#the-check-that-every-stack-must-pass) with
   `MODEL=DeepSeek-V4.1-Flash-1017-EXL3`.
7. On the laptop, write `~/.config/opencode/opencode.json`. Leave out
   per-model `options`: the server sets the effort:

   ```json
   {
     "$schema": "https://opencode.ai/config.json",
     "model": "spark2x/DeepSeek-V4.1-Flash-1017-EXL3",
     "provider": {
       "spark2x": {
         "npm": "@ai-sdk/openai-compatible",
         "options": { "baseURL": "http://<head-node>:8888/v1", "apiKey": "local" },
         "models": {
           "DeepSeek-V4.1-Flash-1017-EXL3": {
             "tool_call": true,
             "limit": { "context": 262144, "output": 16384 }
           }
         }
       }
     }
   }
   ```

8. [Run the agent](#run-the-agent).
9. Stop, on the head: `cd ~/dsv41_recipe && ./scripts/launch/dsv41_tp2.sh down`.

## The fast option: GLM-5.3-Flash EXL3 4bpw on TensorFold v0.6.0

Disk: about 176 GB on each node. Measured: 42/42 on replay + hard, 30/30 on
the standard set, but only 284 of 390 hidden tests
([#1095](https://github.com/evanwtf/local-llm/issues/1095)). Use it when the
visible tests cover the whole job.
Source: [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) section 6; `tasks.toml` →
`[backend.glm53fexl3tfqmiaai20261010dual2xrc]`; the recorded argv.

The recipe is
[`MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold`](https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold).
It builds TensorFold v0.6.0 plus its own patches into an image based on
`nvcr.io/nvidia/pytorch:26.07-py3`.

1. On the head, clone the recipe. GLM was last measured at `3fd60df`:

   ```sh
   git clone https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold ~/miaai-tf-glm
   cd ~/miaai-tf-glm && git checkout 3fd60df
   ```

2. Create `scripts/local.sh`. It names the worker's SSH target on the link,
   builds the image locally, copies the weights to the worker, and pins the
   measured checkpoint:

   ```sh
   cat > scripts/local.sh <<'SH'
   WORKER=<user>@10.0.0.2
   PULL=0
   WORKER_WEIGHTS=copy
   MODEL_REVISION=76c0b5173166d2795dd48860f45d8224817f894c
   SH
   ```

   Do not set `MODEL_ID`. The recipe's default checkpoint is the measured one,
   `Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold`.
3. Prepare both nodes. This downloads the checkpoint and the drafter
   (`incoai/GLM-5.3-Flash-DFlash2` at `bf582e4`) on both nodes, builds the
   image, and checks that both nodes hold the same image:

   ```sh
   ./scripts/prepare.sh
   ```

4. Start both ranks. The first start compiles CUDA kernels for GB10, about 6
   minutes:

   ```sh
   ./start.sh
   ```

   The recorded head command was `tensorfold serve <snapshot> --tp 2 --rank 0
   --name GLM-5.3-Flash-EXL3 --port 8888 --drafter <DFlash2 snapshot>
   --context 1048576 --parallel 4 --max-tokens 32768 --thinking --vision`.
5. Do the check with `MODEL=GLM-5.3-Flash-EXL3`.
6. On the laptop, write the OpenCode config. The recipe has no server default
   for reasoning effort, so the client sends `low`:

   ```json
   {
     "$schema": "https://opencode.ai/config.json",
     "model": "tfmia8888/GLM-5.3-Flash-EXL3",
     "provider": {
       "tfmia8888": {
         "npm": "@ai-sdk/openai-compatible",
         "options": {
           "baseURL": "http://<head-node>:8888/v1",
           "apiKey": "local",
           "chunkTimeout": 900000,
           "timeout": false
         },
         "models": {
           "GLM-5.3-Flash-EXL3": {
             "tool_call": true,
             "reasoning": false,
             "temperature": false,
             "options": { "reasoningEffort": "low" },
             "limit": { "context": 262144, "output": 32768 }
           }
         }
       }
     }
   }
   ```

7. [Run the agent](#run-the-agent).
8. Stop, on the head: `cd ~/miaai-tf-glm && ./stop.sh`. It stops both ranks.

## The most hidden tests passed: DeepSeek-V4.1-Flash-1017 EXL3-EAQ-v1 2.9 bpw on TensorFold

Disk: about 530 GB on the head and 335 GB on the worker. Measured: 38/42 on
replay + hard, 361 of 361 hidden tests
([#1168](https://github.com/evanwtf/local-llm/issues/1168)). It took 130.2% of
the pick's time.
Source: [`RECOMMENDATIONS.md`](RECOMMENDATIONS.md) sections 2 and 3;
`tasks.toml` → `[backend.dsv41f1017eaqtfmiaaidual2xrc]`; the recorded argv;
the recipe README at `e65785c`.

The recipe is
[`MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks`](https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks).
It builds TensorFold's native engine into an image, and the worker reads the
checkpoint from the head over NFS.

**Which recipe commit.** The replay + hard run used `2b906fd` and the
standard run used `e65785c` ([`RECOMMENDATIONS.md`](RECOMMENDATIONS.md),
section 2). `tasks.toml` names `1afc891`. This page uses `e65785c`.

1. On the head, export the Hugging Face cache to the worker over NFS
   (recipe README, "Requirements"). The worker needs nothing installed:

   ```sh
   sudo apt install -y nfs-kernel-server
   echo "$HOME/.cache/huggingface 10.0.0.2(ro,no_subtree_check)" | sudo tee -a /etc/exports
   sudo exportfs -ra
   ```

2. On both nodes, set the same GPU clock ceiling. With TP=2 the slower GPU
   sets the pace:

   ```sh
   sudo nvidia-smi -lgc 0,2200
   ```

3. On the head, clone the recipe:

   ```sh
   git clone https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks ~/miaai-dsv41-1017
   cd ~/miaai-dsv41-1017 && git checkout e65785c
   ```

4. Create `scripts/local.sh`. `PULL=0` builds the image locally, as the
   measured run did:

   ```sh
   cp scripts/local.sh.example scripts/local.sh
   cat >> scripts/local.sh <<'SH'
   WORKER=<user>@10.0.0.2
   PULL=0
   SH
   ```

   The default checkpoint is the measured one,
   `Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw` at `6d54be4f`.
5. See what `start.sh` will do, then start. The first run builds the image on
   each node, downloads 196 GiB of checkpoint and about 203 GB of Engram
   tables, writes each rank's weight file, starts rank 1 on the worker and
   rank 0 on the head, and runs a smoke test:

   ```sh
   DRY_RUN=1 ./start.sh
   ./start.sh
   ```

   It prints `DeepSeek-V4.1-Flash-1017 is now LIVE! on port 8888` when ready.
   It refuses to start with less than 109 GiB available on either node.
6. Do the check with `MODEL=DeepSeek-V4.1-Flash-1017`.
7. On the laptop, use the fast option's provider block with these changes:
   provider key `tf8888`, model `DeepSeek-V4.1-Flash-1017`, and
   `"limit": { "context": 262144, "output": 32768 }`. Keep
   `"options": { "reasoningEffort": "low" }`. The recipe sets no server
   default for effort.
8. [Run the agent](#run-the-agent). Two of its 42 trials stopped on the first
   turn with no tool call. Run a task again if the agent stops at once.
9. Stop, on the head: `cd ~/miaai-dsv41-1017 && ./stop.sh`.

## Image input: DeepSeek-V4-Flash-Vision-Exp on vLLM with DSpark

Disk: about 157 GiB on each node (or on the head only, with NFS). Measured:
63/63 replay trials, on the earlier client that had broken `grep` and `glob`
tools ([`RECOMMENDATIONS.md`](RECOMMENDATIONS.md), section 4). It is the only
stack here that takes images, and it serves a 1,048,576-token window.
Source: `tasks.toml` → `[backend.dsv4flashvisiondspark2xrc]`; the recorded
argv; the recipe README.

The recipe is
[`MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark`](https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark).
It runs the image `ghcr.io/anemll/dspark-vllm-gx10:0.1.1`, pinned by digest.

**Which recipe commit.** The repo does not pin the commit that served the rows
(2026-09-29). Use the latest, and record the commit you run.

1. On **both** nodes, clone the recipe at the same path. The head starts the
   worker's rank from that path:

   ```sh
   git clone https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark ~/dspark-2x
   ```

2. On **both** nodes, pull the image at the measured digest:

   ```sh
   docker pull ghcr.io/anemll/dspark-vllm-gx10:0.1.1@sha256:a83948492cf13df455170fb42885f5ef4db54fefe0feff0f841ecbff464ac9d8
   ```

3. On the head, create `.env.dspark` from the example. Then set these values.
   They are the measured ones; change the interface names to your fabric:

   ```sh
   cd ~/dspark-2x
   cp .env.dspark.example .env.dspark
   ```

   ```env
   WORKER_HOST=10.0.0.2
   MASTER_ADDR=10.0.0.1
   VLLM_HOST_IP=10.0.0.1
   WORKER_VLLM_HOST_IP=10.0.0.2
   NCCL_IB_HCA=rocep1s0f1
   NCCL_SOCKET_IFNAME=enp1s0f1np1
   TP_SOCKET_IFNAME=enp1s0f1np1
   GLOO_SOCKET_IFNAME=enp1s0f1np1
   HF_CACHE=/home/<user>/.cache/huggingface
   WORKER_HF_CACHE=/home/<user>/.cache/huggingface
   DSPARK_MODEL_OFFICIAL=deepseek-ai/DeepSeek-V4-Flash-Vision-Exp
   DSPARK_REVISION=86f746b36186f0e567729a5c06a8c918caba82a9
   SERVED_MODEL_NAME=deepseek-v4-flash-vision-exp
   VLLM_HOST=0.0.0.0
   VLLM_PORT=8888
   DSPARK_VLLM_IMAGE=ghcr.io/anemll/dspark-vllm-gx10:0.1.1@sha256:a83948492cf13df455170fb42885f5ef4db54fefe0feff0f841ecbff464ac9d8
   MAX_MODEL_LEN=1048576
   MAX_NUM_SEQS=6
   MAX_NUM_BATCHED_TOKENS=8192
   ABLITERATED=0
   ```

4. On the head, download the official weights. By default this also copies
   them to the worker:

   ```sh
   ./prepare-dspark-model-cache.sh --official
   ```

5. On the head, start both ranks. The script starts the worker first, then
   the head:

   ```sh
   ./start-deepseek-v4-flash-dspark.sh
   ./smoke-deepseek-v4-flash-dspark.sh
   ```

   The recorded head command used `--tensor-parallel-size 2 --nnodes 2
   --kv-cache-dtype nvfp4_ds_mla --max-model-len 1048576 --max-num-seqs 6
   --gpu-memory-utilization 0.835 --speculative-config
   {"method":"dspark","num_speculative_tokens":6,"draft_sample_method":"probabilistic"}
   --tool-call-parser deepseek_v4 --reasoning-parser deepseek_v4
   --default-chat-template-kwargs {"thinking":true,"reasoning_effort":"low"}`.
6. Do the check with `MODEL=deepseek-v4-flash-vision-exp`.
7. On the laptop, use the pick's provider block with model
   `deepseek-v4-flash-vision-exp`. The server sets the effort. Raise
   `limit.context` up to 1048576 if your client needs it.
8. [Run the agent](#run-the-agent).
9. Stop, on the head: `./stop-deepseek-v4-flash-dspark.sh`.

**earlyoom.** The recipe README says to disable earlyoom on both nodes. This
project keeps it on at 1.0 / 0.5 GiB instead. At that line it fires only below
the recipes' own memory guards
([`docs/dgx-cluster-howto.md`](../../docs/dgx-cluster-howto.md), section 10).

---

## What this page could not confirm from the repo

- The recipe commit that served the image-input stack's rows, and the exact
  `.env.dspark` beyond the values the row's argv shows.
- Which of `1afc891`, `2b906fd` and `e65785c` to treat as the #1168 recipe:
  `tasks.toml` and `RECOMMENDATIONS.md` disagree.
- The role of the sybil-solutions recipe in the pick. `RECOMMENDATIONS.md`
  names it, but the steps it records use only the sudoingX stack.
- Disk sizes for the fast option and the image stack come from the recipes'
  own downloads as `RECOMMENDATIONS.md` reports them, not from a measurement
  here.
