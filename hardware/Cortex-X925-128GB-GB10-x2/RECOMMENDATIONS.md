# What to run on the dual DGX Spark cluster

**A local coding agent served by two NVIDIA DGX Sparks (GB10, 128 GB each,
tensor-parallel over their ConnectX-7 link), driven from a laptop on the LAN.**
Every number here was measured by `benchmarks/agent/` on this cluster, with
OpenCode on a separate client. Nothing is from a model card. Run section 1,
pick a row in section 2, or read why in section 3; the full evidence moved
([where the rest went](#where-the-rest-went)). With one Spark, read
[the single-Spark page](../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md).

**Ledger last read 2026-10-26** ([`results.jsonl`](results.jsonl): 3,288 rows,
the last finished at 2026-10-26T03:11:44-0400). Rows from 2026-10-18 on ran
OpenCode 2.0.32 or 2.1.x and compare only with each other. A read date more
than two weeks old means re-check the ledger before you trust a row.

---

## 1. Run the pick

**DeepSeek-V4.1-Flash-1017, EXL3 2.7 bpw, on vLLM + ExLlamaV3**
([#1160][i1160]). These are the steps #1160 ran. Do steps 1 and 3 on both
Sparks; run the rest on the head.

```sh
# 1. The stack, at the pinned commit (v2.0 measured worse: section 4)
git clone https://github.com/sudoingX/dgx-spark-deepseek-v41 ~/dsv41_recipe
cd ~/dsv41_recipe && git checkout cc59c360

# 2. Build the images locally, with no other GPU container running
PEER=<worker> ./install.sh

# 3. The weights, plus DeepSeek's own Engram shards, at the same path on both
hf download 0xSero/DeepSeek-V4.1-Flash-1017-EXL3-2.7bpw \
  --revision e34ef55bda7c37d17d8dcc86a8a64e7cb875481e --local-dir ~/models/dsv41-1017-exl3
hf download deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  model-00047-of-00048.safetensors model-00048-of-00048.safetensors \
  --revision 1bae0e1088993b9b82de08f59de5be5a34e42170 --local-dir ~/models/dsv41-1017-exl3

# 4. Both ranks, reasoning effort low on the server (the template defaults to high)
HEAD=<head> WORKER=<worker> PORT=8888 SERVED=DeepSeek-V4.1-Flash-1017-EXL3 \
  MODEL_DIR=$HOME/models/dsv41-1017-exl3 \
  EXTRA='--default-chat-template-kwargs {"reasoning_effort":"low"}' \
  ./scripts/launch/dsv41_tp2.sh up          # stop: ./scripts/launch/dsv41_tp2.sh down
```

The first boot took 537 s. On the client, point an OpenCode provider at
`http://<head-node>:8888/v1` with model `DeepSeek-V4.1-Flash-1017-EXL3` and
`"limit": { "context": 262144, "output": 16384 }`. Send no reasoning effort:
the server sets it. Every row of section 2, step by step from two clean Sparks,
is in [`howto.md`](howto.md).

---

## 2. Or pick a row

**Not a ranking.** Each row is here for the reason in its first column.
Replay + hard is 14 real-commit tasks × 3 trials. The hidden re-grade
([#726][i726]) checks each answer against tests the agent never saw. Times are
sums of task medians; percentages are of the pick's.

| pick this if | stack | replay + hard | hidden re-grade | standard set |
|---|---|---|---|---|
| **you want the most correct agent (the pick)** | DeepSeek-1017 EXL3 2.7 bpw ([0xSero][w-sero1017]) on vLLM + ExLlamaV3, [sudoingX stack][r-sudoingx] @`cc59c360` ([#1160][i1160]) | **41/41**, 4,782.4 s | **369/380 (97.1%)**, 17/20 whole tasks | 30/30, 504.4 s |
| **you want it fast and the visible tests cover the job** | GLM-5.3-Flash 4bpw TensorFold quant ([Mia-AiLab][w-tfq]) on TensorFold v0.6.0, [MiaAI-Lab recipe][r-tf-mia] ([#1095][i1095]) | 42/42, 4,358.7 s (91.1%) | 284/390 (72.8%), 1/21 whole tasks | 30/30, **266.6 s (52.9%)** |
| **you want the most held-out tests passed** | DeepSeek-1017 EXL3-EAQ-v1 2.9 bpw ([Mia-AiLab][w-eaq1017]) on TensorFold, [MiaAI-Lab recipe][r-dsv41-1017-mia] ([#1168][i1168]) | 38/42, 6,226.4 s (130.2%) | **361/361 (100.0%)**, 19/21 whole tasks | 10/10, 660.4 s, stopped early |
| **you need image input** | DeepSeek-V4-Flash-Vision-Exp on vLLM, [MiaAI-Lab DSpark recipe][r-dsv4v] ([#1097][i1097]) | not run on the fixed client | — | 10/10, 871.2 s (332.0% of GLM's 262.4 s), stopped early |
| **you have one Spark** | [the single-Spark page](../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md) | | | |

**Context.** The pick serves 262,144 tokens. DeepSeek-1017 on TensorFold serves
up to 1,048,576, but the client sends at most 262,144, so long context is not
measured. DeepSeek-1017 does not take images.

---

## 3. Why this order

The operator's rule: "Pure correctness has to win, but saving ~50% on time is
also very valuable." So the order is the visible verdict and the hidden
re-grade first, then wall time.

- **Visible verdicts do not separate the top stacks.** Every 95% Wilson
  interval overlaps the others.
- **The hidden re-grade does.** The DeepSeek-1017 arms pass 97.1% to 100.0% of
  the held-out tests; GLM passes 72.8%. Four GLM runs agree on that gap: its
  126 rows on OpenCode 1.18.35 ([#1018][i1018]) re-grade to 73.8%.
- **The pick over #1168.** No visible failure in 41 counted trials. #1168 lost
  4 of 42: two first-turn stops (`finish=stop`, no tool call,
  [#1177][i1177]), one 1,800 s timeout and one test edit. Its second run
  ([#1246][i1246]) repeated the pattern: 35 of 39 visible, 281 of 281 hidden.
- **The pick is firm.** Three runs agree ([#1255][i1255], [#1262][i1262]):
  replay + hard took 4,782.4 s, 4,098.8 s and 4,252.3 s, and the hidden
  re-grade was 97.1%, 97.6% and 98.3%. No trial failed, timed out or stopped on
  its first turn.
- **Keep the claim small.** The gmail-archive commits are public. The pick and
  #1168 wrote back 43.0% and 43.5% of the replay files verbatim, against 1.0%
  for GLM, so part of the hidden lead may be recall. The three DeepSeek-1017
  quants are by three authors (2.7, 2.8 and 2.9 bpw), so they separate the
  engines only up to the quant.
- **Set reasoning effort `low`.** The 1017 template defaults to high, and GLM's
  falls back to max. On jayleaton's GLM TensorFold stack, `high` took 171% of
  `low`'s time on the 12 non-span tasks and passed 0 of 6 span trials
  ([#840][i840]).

---

## 4. Measured and not picked

| stack | result | issue |
|---|---|---|
| DeepSeek-1017 on the sudoingX stack v2.0, sudoingx 2.8 bpw | 41/41, 5,348.7 s (111.8%), hidden 345/380 (90.8%). The pick's recipe is pinned at `cc59c36` because of this run | [#1244][i1244] |
| DeepSeek-1017 EXL3 2.8 bpw on TensorFold Zig (jayleaton) | 34/35 with 7 rows excluded; the server crashed with `PoolExhausted` after 5 h 7 min | [#1142][i1142] |
| GLM-5.3-Flash FP4-Local on vLLM | 41/42, 6,704.7 s (153.8% of GLM), hidden 72.6% | [#1128][i1128] |
| GLM on TensorFold's native engine 1.1.3, no recipe | standard set 30/30, 425.4 s (162.1% of the recipe stack) | [#1095][i1095] |
| GLM TR3 4bpw on the same recipe | 122/126, 4,271.0 s, against the quant's 124/126 and 4,111.1 s: "not worse", not "faster" | [#970][i970] |
| Qwen3.8-Flash-Next, every stack | passes nearly every trial, but 188% to 265% of GLM's time: 14,000 to 16,800 median reasoning tokens a trial, against GLM's 2,800 to 3,500 | [#865][i865], [#897][i897], [#977][i977] |
| Nemotron-3.5-Super-VL NVFP4; MiMo-V2.6-Flash-RL | screened out on the standard set at 209.4% and 478.7% of GLM | [#1231][i1231], [#1032][i1032] |

Stacks dropped before a screen (MiMo-V2.6, Ling-3.0, Atlas, Flare and others)
and every result from before the client fix of 2026-10-06 ([#968][i968]) are
in the archive.

---

## Where the rest went

| for | see |
|---|---|
| this page before the 2,000-word budget: test environment, every stack table, the broken-client results, tool versions, open questions | [`recommendations-archive-2026-10-27.md`](recommendations-archive-2026-10-27.md) (frozen) |
| every row, step by step from two clean Sparks | [`howto.md`](howto.md) |
| the cluster: hardware, fabric, RoCE, NCCL | [`README.md`](README.md) · [`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md) |
| any number here, again | `uv run python scripts/screen_stacks.py --results hardware/Cortex-X925-128GB-GB10-x2/results.jsonl --since 2026-10-18T00:00:00-0400` (add `--standard` for the standard set) |
| what the tasks are | [`benchmarks/agent/METHODOLOGY.md`](../../benchmarks/agent/METHODOLOGY.md) |
| what runs next | `uv run python scripts/make_next.py --platform nvidia` |

This page stays at or under 2,000 words: `tests/test_recommendations_budget.py`
fails CI past that, and its docstring says where new evidence goes.

[r-sudoingx]: https://github.com/sudoingX/dgx-spark-deepseek-v41
[r-tf-mia]: https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks-TensorFold
[r-dsv41-1017-mia]: https://github.com/MiaAI-Lab/DeepSeek-v4.1-Flash-1017-2x-DGX-Sparks
[r-dsv4v]: https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark
[w-sero1017]: https://huggingface.co/0xSero/DeepSeek-V4.1-Flash-1017-EXL3-2.7bpw
[w-eaq1017]: https://huggingface.co/Mia-AiLab/DeepSeek-V4.1-Flash-1017-EXL3-EAQ-v1-2.9-bpw
[w-tfq]: https://huggingface.co/Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold
[i726]: https://github.com/evanwtf/local-llm/issues/726
[i840]: https://github.com/evanwtf/local-llm/issues/840
[i865]: https://github.com/evanwtf/local-llm/issues/865
[i897]: https://github.com/evanwtf/local-llm/issues/897
[i968]: https://github.com/evanwtf/local-llm/issues/968
[i970]: https://github.com/evanwtf/local-llm/issues/970
[i977]: https://github.com/evanwtf/local-llm/issues/977
[i1018]: https://github.com/evanwtf/local-llm/issues/1018
[i1032]: https://github.com/evanwtf/local-llm/issues/1032
[i1095]: https://github.com/evanwtf/local-llm/issues/1095
[i1097]: https://github.com/evanwtf/local-llm/issues/1097
[i1128]: https://github.com/evanwtf/local-llm/issues/1128
[i1142]: https://github.com/evanwtf/local-llm/issues/1142
[i1160]: https://github.com/evanwtf/local-llm/issues/1160
[i1168]: https://github.com/evanwtf/local-llm/issues/1168
[i1177]: https://github.com/evanwtf/local-llm/issues/1177
[i1231]: https://github.com/evanwtf/local-llm/issues/1231
[i1244]: https://github.com/evanwtf/local-llm/issues/1244
[i1246]: https://github.com/evanwtf/local-llm/issues/1246
[i1255]: https://github.com/evanwtf/local-llm/issues/1255
[i1262]: https://github.com/evanwtf/local-llm/issues/1262
