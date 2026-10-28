# External repositories and directory layout

The harness measures engines that live **outside** this checkout. The engine
binaries build from separate repositories, and the weights sit in model trees
of their own ([`docs/model-locations.md`](model-locations.md)). A machine that
clones only `local-llm` cannot reproduce a published stack until it has them.

This page lists every engine tree that `benchmarks/agent/tasks.toml` names,
where to get it, and how a path resolves.
`benchmarks/agent/test_external_repos.py` asserts that every `engine_tree` in
`tasks.toml` is named here.

## How a path resolves

Every checkout defaults to `~/git/<name>`; release archives and virtualenvs sit
under `~/engines/` or `~/venvs/`. `~` is the current user's home.

- **`DS4_ROOT`**: where the default ds4 engine lives (default `~/git/ds4`).
  `run.py` and `decode_ab.py` (via the `DS4` env var) read it.
- **`engine_tree`**: a per-backend field in `tasks.toml`. It records the tree a
  backend's server was built from, feeds engine provenance
  (`engine_identity.identity`), and lets `run.py` confirm that the running
  server matches. On a different layout, edit the `engine_tree` or add a
  symlink from the default path.
- **`recipe_dir`**: most two-Spark backends name a cloned community recipe
  (scripts and Dockerfiles) instead of an engine tree, often with a
  `recipe_pin` commit. The clone and pin for each pick are in that machine's
  [`howto.md`](../hardware/Cortex-X925-128GB-GB10-x2/howto.md).

A path is **provenance and a launch hint, not an automatic clone**. You clone
the trees yourself. Versions are not pinned here: the policy is always-latest,
`preflight.py` refuses an engine behind its latest release (#1049), and each
result row stamps the exact sha it ran on.

## The repositories

| local path | origin | engine | tracks | for |
|---|---|---|---|---|
| `~/git/ds4` | `evanwtf/ds4` (private fork of `antirez/ds4`) | ds4 | `main` | the default ds4 engine (`DS4_ROOT`): DeepSeek-V4, Qwen3.8-Flash-Next, GLM |
| `~/git/ds4-main` | `evanwtf/ds4` | ds4 | `main` | a second ds4 worktree at the tip of main, for A/B baselines |
| `~/git/ds4-ivan-qwen38fn` | `evanwtf/ds4` | ds4 | PR #991 head | Qwen3.8-Flash-Next upstreaming (antirez/ds4#991); the level-2 tensor-tile builds (#328) |
| `~/git/ds4-mainline-0aaea5a2` | `antirez/ds4` | ds4 | `0aaea5a2` (main) | upstream main as of 2026-09-29; the `qwen38fnds4main` tree (#834) |
| `~/git/ds4-metal` | `ivanfioravanti/ds4-metal` | ds4 | `qwen3.8-flash-next` | the Metal-optimized ds4 fork; the older engine tree for the qwen fast-packs |
| `~/git/ds4-metal-head` | `ivanfioravanti/ds4-metal` | ds4 | fork head | the current ds4-metal head, for the stale-tree re-measures (#228) |
| `~/git/ds4-metal-228` | `ivanfioravanti/ds4-metal` | ds4 | `6c1e8367` line | the #228 tree that dispatches only `qwen4exp` (#279) |
| `~/git/llama.cpp` | `ggml-org/llama.cpp` | llama.cpp | `master` | the GGUF baseline engine (Metal) |
| `~/git/llama.cpp-upstream` | `ggml-org/llama.cpp` | llama.cpp | `master` | a second worktree for a PR-vs-master A/B |
| `~/git/mlx-serve` | `ddalcu/mlx-serve` | mlx-serve | `main` | the MLX serving engine (Apple Silicon) |
| `~/git/mlx-serve-main` | `ddalcu/mlx-serve` | mlx-serve | `3a9181f` | main with the Responses content-parts fix (`92d1c1a`), before a release; the `qwen38fnmlxservenopld-main` tree (#707) |
| `~/git/mlx-serve-6dea4241` | `ddalcu/mlx-serve` | mlx-serve | `6dea4241` | main with Prism Bonsai 2 support (`89eeb249`), before a release; the `bonsai2mlxserve` tree (#479) |
| `~/git/sushi` | `beamivalice/sushi` | sushi | `v1.0.2` (`1a5332d`) | a detached mlx-serve fork for Qwen3.8-Flash-Next EXL3 packs, built with the repo's pinned Zig 0.17 nightly and MLX submodules; the `qwen38fnsushi4` tree (#749) |
| `~/venvs/vllm` | pip (`vllm`) | vllm | release | the vLLM engine on the DGX Spark: a virtualenv, **not a git checkout** |
| `~/venvs/vllm-0.32.0` | pip (`vllm==0.32.0`) | vllm | `0.32.0` | vLLM 0.32.0 on the worker Spark (#1088), on a uv-managed Python 3.12: the worker has no `python3-dev`, and Triton compiles against `Python.h` |
| `~/engines/tensorfold-dl-1.0.9/tensorfold-1.0.9-linux-aarch64` | release archive (`ashhart/TensorFold`) | tensorfold | `v1.0.9` | the `nemotron35ltf109rcdgx` tree (#1066) |
| `~/engines/tensorfold-dl-1.0.10/tensorfold-1.0.10-linux-aarch64` | release archive (`ashhart/TensorFold`) | tensorfold | `v1.0.10` | the `nemotron35ltf1010rcdgx` and `nemotron35ltf1010c262rcdgx` trees (#1066) |
| `~/engines/tensorfold-dl-1.0.11/tensorfold-1.0.11-linux-aarch64` | release archive (`ashhart/TensorFold`) | tensorfold | `v1.0.11` | the `nemotron35ltf1011c262rcdgx` tree (#1066) |
| `~/tensorfold-native/tensorfold-1.1.3-linux-aarch64` | release archive (`ashhart/TensorFold`) | tensorfold | `v1.1.3` | GLM-5.3-Flash on two CUDA ranks via `scripts/tensorfold_native_two_node.py`, with `libnccl.so.2` from `vllm/vllm-openai:v0.32.0` in `~/tensorfold-native/nccl` (the archive ships none) (#1095) |
| `~/engines/tandemllm-0.8.3/src` | `0xBakeer/TandemLLM` | tandemllm | `v0.8.3` (`792087c`) | set up by its own `install.sh --ref v0.8.3 --dir ~/engines/tandemllm-0.8.3 --no-start`; currency reads `src/VERSION`; the `qwen3827btandem083nvfp4nothinkrcdgx` tree (#1067) |
| `~/engines/llama.cpp-b11642` | `ggml-org/llama.cpp` | llama.cpp | `b11642` (`2639a3b`) | the #1086 refresh (#1089, #1090, #1094) |
| `~/engines/llama.cpp-b11669` | `ggml-org/llama.cpp` | llama.cpp | `b11669` (`620d222`) | the first tag with Nemotron 3.5 Super VL / Bolt support (ggml-org/llama.cpp#30520), for the #1103 Bolt screen |
| `~/engines/llama.cpp-b11700` | `ggml-org/llama.cpp` | llama.cpp | `b11700` (`23f7aab`) | the #1090 refresh, 2026-10-16 |
| `~/engines/llama.cpp-b11827` | `ggml-org/llama.cpp` | llama.cpp | `b11827` (`970b51c`) | the #1090 refresh, 2026-10-22, on both nodes; served from the head |

Notes:

- **The TensorFold archives are not git checkouts.** Each is sha256-checked
  against its release, and currency reads its `VERSION` file.
- **The `~/engines/llama.cpp-b*` trees are CUDA builds for GB10.** Clone, check
  out the tag, and build with
  `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=121 -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF --target llama-server`.
  `currency.tag_engine_item` checks each against the newest release tag.
- **ds4 is one repository, several worktrees.** `~/git/ds4`, `~/git/ds4-main`
  and `~/git/ds4-ivan-qwen38fn` are the same `evanwtf/ds4` fork at different
  commits, so an A/B can keep two ds4 builds resident. PR worktrees
  (`~/git/ds4-pr<N>`) are made as needed; only the ones in `tasks.toml` appear
  above. `ds4-metal` is a different repository, not a worktree.
- **No machine needs every tree.** `tasks.toml` is shared across the fleet
  ([`hardware/MACHINES.md`](../hardware/MACHINES.md)), so it names trees that
  only another machine builds. The test checks this page against the config,
  never against the disk.

## Getting them

```sh
mkdir -p ~/git && cd ~/git
git clone https://github.com/antirez/ds4.git                # the evanwtf/ds4 fork is private
git clone https://github.com/ivanfioravanti/ds4-metal.git
git clone https://github.com/ggml-org/llama.cpp.git
git clone https://github.com/ddalcu/mlx-serve.git
```

Build each per its own README, point `DS4_ROOT` at your ds4 checkout, and
confirm that the `engine_tree` paths in `tasks.toml` resolve. `preflight.py`
reports which trees it can see and whether any is behind its upstream.
