# Where the models live

**For the current listing, run the census:**

```sh
uv run python benchmarks/agent/model_inventory.py          # full paths
uv run python benchmarks/agent/model_inventory.py --sizes  # and du per pack (slow)
```

`preflight.py` prints the same listing before every run, so a run's log holds
the state of the disk at that time. This page explains the layout.

**Every runtime keeps its models in its own tree. A search of the wrong tree is
a false negative:** a confident "the pack is not on disk" that looked in the
wrong place. Before you say a model is missing, run the census or the runtime's
**own list command**, never a bare `ls ~/models`.

| runtime | root | list it with | notes |
|---|---|---|---|
| **ds4 / llama.cpp / vLLM / TensorFold** | `~/models/` (on the M5 Max, also `~/git/ds4/gguf/`) | `ls ~/models` · `du -sh ~/models/*/` | GGUF, EXL3 and safetensors packs, downloaded by hand (`hf download --local-dir`, `download_model.sh`). On the two-Spark cluster each pack sits at the same path on both nodes. |
| **mlx-serve** | `~/.mlx-serve/models/<org>/<pack>` | **`mlx-serve list`** | Managed by `mlx-serve pull <org/repo>`. **Not under `~/models`.** |
| **Ollama** | `~/.ollama/models` | **`ollama list`** | Content-addressed blobs and manifests; do not read them by file name. Honors `$OLLAMA_MODELS`. |
| **LM Studio** | `~/.lmstudio/models` | the LM Studio app, or `ls ~/.lmstudio/models` | |
| **HF hub cache** | `~/.cache/huggingface/hub` | `hf cache scan` | Where `hf download` (no `--local-dir`), `transformers` and `mlx_lm` cache. Shared across tools. |

On the M5 Max these directories are symlinks onto the `Models` volume
([CONVENTIONS.md](../CONVENTIONS.md#model-weights-stay-out-of-time-machine-2026-09-06)).
Get new weights from the NAS archive first
([AGENTS.md](../AGENTS.md), "Get weights in this order").

## One model, several copies

**A model can exist as different files in several trees at once, and the
copies are not interchangeable.** On the M5 Max, Qwen3.8-Flash-Next is:

- a ds4 GGUF at `~/models/qwen3.8-flash-next-ds4-q4k-imatrix/`,
- an mlx-serve MLX pack at `~/.mlx-serve/models/ddalcu/Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit/`,
- an Ollama tag (`ollama list`).

So "do we have Qwen3.8-Flash-Next?" has no single answer. Ask **which engine's
copy**, and list that engine's tree.

Per-pack notes for the M5 Max are in
[`docs/m5max-runbook.md`](m5max-runbook.md#weights-on-disk).
