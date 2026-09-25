# External repositories and directory layout

This harness measures engines that live **outside** this checkout. The Python
here drives them, but it does not contain them: the engine binaries build from
separate git repositories, and the weights sit in a model tree of their own
(see [`docs/model-locations.md`](model-locations.md)). A machine that clones
only `local-llm` cannot reproduce a published stack — the engine paths do not
exist on it yet.

This document lists every external repository the harness references, where to
get it, what each is for, and how a path is resolved. A test
(`benchmarks/agent/test_external_repos.py`) asserts that every `engine_tree`
in `tasks.toml` is named here, so the list cannot drift silently.

## The default layout: `~/git/<name>`

Every external checkout is expected under `~/git/`. `benchmarks/agent/tasks.toml`
names each engine's tree with an `engine_tree = "~/git/<name>"` field, and
`DS4_ROOT` defaults to `~/git/ds4`. The `~` expands to the current user's home,
so the convention is per-user, not per-path.

Nothing forces this layout — the two knobs below let a different one work — but
`~/git` is the assumed default across the docs and the config, and the least
surprising place to clone.

### The two resolution knobs

- **`DS4_ROOT`** — where the default ds4 engine lives. `run.py`
  (`benchmarks/agent/run.py`) and `decode_ab.py` (via the `DS4` env var) read
  it; it defaults to `~/git/ds4`. Set it to point at your own ds4 checkout.
- **`engine_tree`** — a per-backend field in `tasks.toml`. It records which
  tree a backend's server was built from, feeds engine provenance
  (`engine_identity.identity`), and lets `run.py` confirm the running server
  matches. A backend on a non-default layout needs its `engine_tree` edited to
  the real path (or a symlink from `~/git/<name>` to wherever the checkout is).

A path here is **provenance and a launch hint, not an automatic clone**: the
harness reads these trees, it does not fetch them. You clone them yourself, and
`preflight.py` reports when one has drifted from its upstream.

## The repositories

Versions are **not pinned**. This project's policy is always-latest: `preflight`
enforces currency before a batch rather than freezing a sha (see
[`docs/measurement-discipline.md`](measurement-discipline.md)), and each result
row stamps the exact engine sha it ran on. So the "tracks" column below names a
branch, and the sha lives on the row, not in this doc.

| local path | origin | upstream | engine | tracks | for |
|---|---|---|---|---|---|
| `~/git/ds4` | `evanwtf/ds4` | `antirez/ds4` | ds4 | `main` | the default ds4 engine; `DS4_ROOT` points here. DeepSeek-V4, Qwen3.8-Flash-Next, GLM |
| `~/git/ds4-main` | `evanwtf/ds4` | `antirez/ds4` | ds4 | `main` | a second ds4 worktree pinned to the tip of main for A/B baselines |
| `~/git/ds4-ivan-qwen38fn` | `evanwtf/ds4` | `antirez/ds4` | ds4 | PR #991 head | Qwen3.8-Flash-Next upstreaming (antirez/ds4#991); the level-2 tensor-tile builds (#328) |
| `~/git/ds4-mainline-8db1d1d1` | `antirez/ds4` | — | ds4 | `8db1d1d1` (main) | upstream main with native Qwen3.8-Flash-Next (ds4#991); the `qwen38fnds4main` tree (#158, #306) |
| `~/git/ds4-metal` | `ivanfioravanti/ds4-metal` | — | ds4 | `qwen3.8-flash-next` | the Metal-optimized ds4 fork; the historical engine tree for the qwen fast-packs |
| `~/git/ds4-metal-head` | `ivanfioravanti/ds4-metal` | — | ds4 | fork head | the current ds4-metal head, for the stale-tree re-measures (#228) |
| `~/git/ds4-metal-228` | `ivanfioravanti/ds4-metal` | — | ds4 | `6c1e8367` line | the #228 tree that dispatches only `qwen4exp` (#279) |
| `~/git/llama.cpp` | `ggml-org/llama.cpp` | — | llama.cpp | `master` | the GGUF baseline engine (Metal) |
| `~/git/llama.cpp-upstream` | `ggml-org/llama.cpp` | — | llama.cpp | `master` | a second llama.cpp worktree for a PR-vs-master A/B; not present on every machine |
| `~/git/mlx-serve` | `ddalcu/mlx-serve` | — | mlx-serve | `main` | the MLX serving engine (Apple Silicon) |
| `~/git/mlx-serve-main` | `ddalcu/mlx-serve` | — | mlx-serve | `3a9181f` (origin/main) | main with the Responses content-parts fix (`92d1c1a`), no release yet; the `qwen38fnmlxservenopld-main` tree (#707) |
| `~/git/mlx-serve-6dea4241` | `ddalcu/mlx-serve` | — | mlx-serve | `6dea4241` (main) | main with Prism Bonsai 2 support (`89eeb249`), no release yet; the `bonsai2mlxserve` tree (#479) |
| `~/git/sushi` | `beamivalice/sushi` | — | sushi | `v1.0.2` (`1a5332d`) | Sushi, a detached mlx-serve fork for Qwen3.8-Flash-Next EXL3 packs, built with the repo's pinned Zig 0.17 nightly and MLX submodules; the `qwen38fnsushi4` tree (#749) |
| `~/venvs/vllm` | pip (`vllm`) | — | vllm | release | the vLLM engine — a Python virtualenv, **not a git checkout**; the DGX Spark lane |

Notes on the shape of this list:

- **ds4 is one repository, several worktrees.** `~/git/ds4`, `~/git/ds4-main`
  and `~/git/ds4-ivan-qwen38fn` are checkouts of the same `evanwtf/ds4` fork
  (upstream `antirez/ds4`) at different commits, so an A/B can hold two ds4
  builds resident at once. The PR worktrees (`~/git/ds4-pr952-head`,
  `~/git/ds4-pr964`, …) are the same idea for a specific pull request and are
  created as needed; only the ones named in `tasks.toml` appear above.
- **ds4-metal is a different repository** (`ivanfioravanti/ds4-metal`), the
  Metal-optimization fork, not a worktree of `antirez/ds4`.
- **`~/git/llama.cpp-upstream` and `~/venvs/vllm` need not exist on this Mac.**
  They back backends measured on other machines; `tasks.toml` is shared across
  the fleet ([`hardware/MACHINES.md`](../hardware/MACHINES.md)), so it names
  trees this machine does not build. That is expected, not drift — the test
  below checks the doc against the config, never against this disk.

## Getting them

```sh
mkdir -p ~/git && cd ~/git
git clone git@github.com:evanwtf/ds4.git                 # then: git -C ds4 remote add upstream git@github.com:antirez/ds4.git
git clone https://github.com/ivanfioravanti/ds4-metal.git
git clone https://github.com/ggml-org/llama.cpp.git
git clone https://github.com/ddalcu/mlx-serve.git
```

Build each per its own README, then point `DS4_ROOT` at your ds4 checkout and
confirm the `engine_tree` paths in `tasks.toml` resolve. `preflight.py` will
tell you which trees it can see and whether any has fallen behind its upstream.
