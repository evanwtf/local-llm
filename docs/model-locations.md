# Where the models live

> **For the current listing, run the census — do not trust this page's snapshot:**
>
> ```sh
> uv run python benchmarks/agent/model_inventory.py          # full paths
> uv run python benchmarks/agent/model_inventory.py --sizes  # + du per pack (slow)
> ```
>
> `preflight.py` prints this automatically before every run, so any run's log
> already holds the current truth. This page explains the *why*; the census is
> the *what*.

**Every runtime keeps its models in its own tree. Searching the wrong one is a
false negative** — a confident "the pack is not on disk" that is simply looking
in the wrong place. This has cost real time. Before you conclude a model is
missing, run the census above, or check the runtime's **own list command** —
never a raw `ls` of `~/models`.

| runtime | root | list it with | notes |
|---|---|---|---|
| **ds4 / llama.cpp** | `~/models/` and `~/git/ds4/gguf/` | `ls ~/models` · `du -sh ~/models/*/` | GGUF and ds4 packs, downloaded by hand (`hf download`, `download_model.sh`). This is what `--dir` / `--model` point at. |
| **mlx-serve** | `~/.mlx-serve/models/<org>/<pack>` | **`mlx-serve list`** | Managed by `mlx-serve pull <org/repo>`. **NOT under `~/models`.** A raw `ls ~/models` will never show these. |
| **Ollama** | `~/.ollama/models` | **`ollama list`** | Content-addressed blobs + manifests — opaque, do not read by filename. Honors `$OLLAMA_MODELS` if set. |
| **LM Studio** | `~/.lmstudio/models` | LM Studio app, or `ls ~/.lmstudio/models` | Present but empty as of 2026-09-10. |
| **HF hub cache** | `~/.cache/huggingface/hub` | `hf cache scan` | Where `hf download` (no `--local-dir`), `transformers`, and `mlx_lm` cache. Shared across tools. |

## The rule

**A model can exist as several different files in several of these trees at
once, and they are not interchangeable.** Qwen3.8-Flash-Next is on this machine
as *all* of:

- a ds4 GGUF at `~/models/qwen3.8-flash-next-ds4-q4k-imatrix/` (the frontier ds4 pack),
- an mlx-serve MLX pack at `~/.mlx-serve/models/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit/`,
- an Ollama tag (`ollama list`).

So "do we have Qwen3.8-Flash-Next?" has no single answer — ask **which engine's
copy**, and list *that* engine's tree.

## Snapshot (read 2026-09-10 — sizes drift, re-read before quoting)

- `~/models/` ≈ 593 GB across the ds4/GGUF packs (see `docs/m5max-runbook.md` → "Weights on disk" for the per-pack table).
- `~/.mlx-serve/models/` ≈ 105 GB: `ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit` (100 GB), `mlx-community/gemma-4-e4b-it-4bit` (4.8 GB).
- `~/.ollama/models` ≈ 184 GB, 14 tags (`ollama list`).
- `~/.cache/huggingface/hub` ≈ 15 GB.
- `~/.lmstudio/models` empty.

Related: [`docs/m5max-runbook.md`](m5max-runbook.md) has the per-pack notes and
the engine-tree table; [[feedback_search_right_domain]] is the memory that
records why this doc exists.
