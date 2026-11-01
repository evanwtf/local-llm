# What to run on two DGX Sparks

**This page is for you if you have two NVIDIA DGX Sparks joined by their
ConnectX-7 cable and want a coding agent that runs on them, not in the cloud.**
It names the model and server to run, gives the commands, and shows the tests
behind each choice. With one Spark, read
[the single-Spark page](../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md).

**The results are from 2026-10-19 to 2026-10-26**, and the standard sets at
the 1,900 MHz clock cap from 2026-11-01. Each row in section 2 gives its own
dates. Models and servers change every week, so a row more than a few
weeks old may be out of date.

---

## How we test

We give a coding agent real programming work, and we let the project's own
tests decide whether it did the work. No model grades another model.

**The agent.** [OpenCode](https://opencode.ai), on a separate laptop, sends
its requests to the Sparks over the network. That is how you would use it.

**The project.** [gmail-archive](https://github.com/evanwtf/gmail-archive) is
a real Python program that archives Gmail and serves it in a web page. Its
tests were written before this benchmark existed.

**The tasks.** There are three sets:

- **The standard set (10 tasks).** In 8 tasks we delete the body of one or two
  functions. We keep the name and the types, and usually the description, and
  ask the agent to write the body again. In 2 tasks the agent writes a small
  command-line script from a description.
- **Replay (7 tasks).** We take a real commit that added a feature, undo it,
  describe the feature in plain words, and ask the agent to build it again.
  The features change 37 to 583 lines across 1 to 9 files.
- **Hard (7 tasks).** Five replay features again, but we hide some of the
  tests from the agent. Two more tasks undo five commits at once: a rebuild of
  the web interface, 1,643 added lines across 17 files. One of the two does not
  tell the agent which tests to run.

**Pass or fail.** A trial passes when the tests the agent could see pass. A
trial fails if the agent edits a test, or if it does not finish in 30 minutes.
Every task runs 3 times.

**The hidden re-grade.** After a hard task, we put the hidden tests back and
run them ([#726][i726]). An agent can pass the visible tests and still fail the hidden ones:
it wrote code to fit the tests it saw, not the feature. That second check is
what separates the best stacks.

**Time.** We take each task's median time over its 3 trials, and add them up
over the 14 replay and hard tasks. That total is how long you wait.

**What this does not show.** It shows whether the agent finishes the job, not
whether the code is good. gmail-archive is public, so a model may have seen
these commits in training; section 3 says how much that matters. The full
method is in [`METHODOLOGY.md`](../../benchmarks/agent/METHODOLOGY.md).

---

## 1. Run the best all-around choice

**DeepSeek-V4.1-Flash-1017, quantized to 2.7 bits per weight (EXL3), served by
vLLM with ExLlamaV3** ([#1160][i1160]). These are the steps we ran. Do steps 1
and 3 on both Sparks, and the rest on the first one (the head).

```sh
# 1. The server recipe, at the commit we tested (a later one measured worse: section 4)
git clone https://github.com/sudoingX/dgx-spark-deepseek-v41 ~/dsv41_recipe
cd ~/dsv41_recipe && git checkout cc59c360

# 2. Build the images, with no other GPU container running
PEER=<worker> ./install.sh

# 3. The model, plus two files from DeepSeek, at the same path on both Sparks
hf download 0xSero/DeepSeek-V4.1-Flash-1017-EXL3-2.7bpw \
  --revision e34ef55bda7c37d17d8dcc86a8a64e7cb875481e --local-dir ~/models/dsv41-1017-exl3
hf download deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  model-00047-of-00048.safetensors model-00048-of-00048.safetensors \
  --revision 1bae0e1088993b9b82de08f59de5be5a34e42170 --local-dir ~/models/dsv41-1017-exl3

# 4. Start the server on both Sparks, with reasoning effort low (the default is high)
HEAD=<head> WORKER=<worker> PORT=8888 SERVED=DeepSeek-V4.1-Flash-1017-EXL3 \
  MODEL_DIR=$HOME/models/dsv41-1017-exl3 \
  EXTRA='--default-chat-template-kwargs {"reasoning_effort":"low"}' \
  ./scripts/launch/dsv41_tp2.sh up          # stop: ./scripts/launch/dsv41_tp2.sh down
```

The first start took 9 minutes. On the laptop, point an OpenCode provider at
`http://<head>:8888/v1` with model `DeepSeek-V4.1-Flash-1017-EXL3` and
`"limit": { "context": 262144, "output": 16384 }`. Do not set a reasoning
effort in OpenCode; the server sets it. Every row in section 2, step by step
from two clean Sparks, is in [`howto.md`](howto.md).

---

## 2. Or choose by what you need

Each row answers one need. "Replay + hard" is 14 tasks × 3 trials. Times are
the sum of task medians; a percentage compares it with the best all-around
choice.

| choose this if | model and server | tested | replay + hard | hidden tests passed | standard set | standard set at 1,900 MHz |
|---|---|---|---|---|---|---|
| **you want the best all-around agent** | DeepSeek-V4.1-Flash-1017, EXL3 2.7 bpw ([0xSero][w-sero1017]), on vLLM + ExLlamaV3 ([sudoingX recipe][r-sudoingx] @`cc59c360`) · [#1160][i1160] | 2026-10-19 to 10-26, 3 runs | **41/41**, 4,782.4 s | **369 of 380 (97.1%)**; 17 of 20 tasks whole | 30/30, 504.4 s | 10/10, 545.1 s, stopped early |
| **you want the fastest agent, and your tests cover the job** | GLM-5.3-Flash, EXL3 4 bpw ([Mia-AiLab][w-tfq]), on TensorFold v0.6.0 ([MiaAI-Lab recipe][r-tf-mia]) · [#1095][i1095] | 2026-10-19; standard set again 2026-10-24 | 42/42, 4,358.7 s (91.1%) | 284 of 390 (72.8%); 1 of 21 tasks whole | 30/30, **266.6 s (52.9%)** | 30/30, **256.6 s** |
| **hidden tests matter most, and you can retry a stalled task** | DeepSeek-V4.1-Flash-1017, EXL3-EAQ-v1 2.9 bpw ([Mia-AiLab][w-eaq1017]), on TensorFold ([MiaAI-Lab recipe][r-dsv41-1017-mia]) · [#1168][i1168] | 2026-10-19; again 2026-10-25 | 38/42, 6,226.4 s (130.2%) | **361 of 361 (100.0%)**; 19 of 21 tasks whole | 10/10, 660.4 s, stopped early | 10/10, 641.6 s, stopped early |
| **you need image input** | DeepSeek-V4-Flash-Vision-Exp on vLLM ([MiaAI-Lab recipe][r-dsv4v]) · [#1097][i1097] | 2026-10-24 | not run | — | 10/10, 871.2 s (332.0% of GLM's 262.4 s that day), stopped early | 10/10, 970.0 s, stopped early |
| **you have one Spark** | [the single-Spark page](../Cortex-X925-128GB-GB10/RECOMMENDATIONS.md) | | | | | |

"Tasks whole" counts hard tasks where every hidden test passed. **Context:**
the best all-around choice serves 262,144 tokens. DeepSeek on TensorFold
serves up to 1,048,576, but the agent sent at most 262,144, so we have not
measured long context. Only the vision row takes images.

**The 1,900 MHz cap.** Since 2026-10-31 both Sparks cap the GPU clock at
1,900 MHz ([#1319][i1319]). On 2026-11-01 we ran each standard set again
under the cap ([#1334][i1334]). Every trial passed, and the order did not
change. GLM lost no time. The two DeepSeek-1017 rows took up to about 10%
longer, and the vision row 11% to 22% longer. Three rows stopped after one
round, and one round of the same stack can vary by 15%, so treat those
three as rough.

---

## 3. Why this order

The operator's rule: "Pure correctness has to win, but saving ~50% on time is
also very valuable." So we rank on correctness first, then time.

- **The visible tests do not separate the top stacks.** They pass 41 of 41,
  42 of 42 and 38 of 42, and the statistical margins overlap.
- **The hidden tests do.** The two DeepSeek rows pass 97.1% and 100.0% of the
  hidden tests. GLM passes 72.8%: its code often fits the visible tests and
  misses the feature. A fourth GLM run on an older client agreed (73.8%,
  [#1018][i1018]).
- **Why not the 100% row?** It stalled. In 4 of 42 trials it failed: twice it
  stopped on its first turn without doing anything ([#1177][i1177]), once it
  ran out of time and once it edited a test. A second run did the same: 35 of
  39 visible, 281 of 281 hidden ([#1246][i1246]). The best all-around choice
  did not fail once in 41 trials.
- **The best all-around result held three times.** Its three runs took
  4,782.4 s, 4,098.8 s and 4,252.3 s, and passed 97.1%, 97.6% and 98.3% of the
  hidden tests ([#1255][i1255], [#1262][i1262]).
- **Keep the claim small.** Both DeepSeek rows rewrote about 43% of the replay
  files byte for byte as the original author did; GLM did that for 1%. Part
  of DeepSeek's lead may be memory of the public repository, not skill. The
  DeepSeek rows also use quantizations by different people, so they compare
  the servers only up to the quantization.
- **Set reasoning effort to `low`.** DeepSeek's default is high, and GLM's
  falls back to max. On one GLM stack, `high` took 171% of `low`'s time on 12
  tasks and passed none of the 6 web-interface trials ([#840][i840]).

---

## 4. Tested and not chosen

| model and server | tested | result | issue |
|---|---|---|---|
| DeepSeek-1017, sudoingX recipe v2.0, sudoingx 2.8 bpw | 2026-10-25 | 41/41, 5,348.7 s (111.8%); hidden 345 of 380 (90.8%). This is why section 1 pins the recipe at `cc59c360` | [#1244][i1244] |
| DeepSeek-1017, EXL3 2.5 bpw (bertholomus), on bertholomus TensorFold TP2 | 2026-10-27 to 10-28, 3 runs, OpenCode 2.2.0 | 118/119, 4,153.4 s pooled (95.5%); hidden 94.9%, 95.2% and 93.3%, below the best all-around choice in every run | [#930][i930] |
| DeepSeek-1017, EXL3 2.8 bpw (jayleaton), on TensorFold Zig | 2026-10-18 to 10-19 | 34/35, with 7 trials not counted; the server crashed (`PoolExhausted`) after 5 h 7 min | [#1142][i1142] |
| GLM-5.3-Flash FP4-Local on vLLM | 2026-10-21 | 41/42, 6,704.7 s (153.8% of GLM); hidden 72.6% | [#1128][i1128] |
| GLM on TensorFold's own engine 1.1.3, no recipe | 2026-10-24 | standard set 30/30, 425.4 s (162.1% of the recipe) | [#1095][i1095] |
| Nemotron-3.5-Super-VL NVFP4; MiMo-V2.6-Flash-RL | 2026-10-24 | dropped after the standard set at 209.4% and 478.7% of GLM's time | [#1231][i1231], [#1032][i1032] |
| GLM TR3 4 bpw, same recipe as GLM | 2026-10-06, older client | 122/126, 4,271.0 s, against the chosen GLM quant's 124/126 and 4,111.1 s | [#970][i970] |
| Qwen3.8-Flash-Next, every server | 2026-10-07 to 10-12, older client | passed nearly every trial, at 188% to 265% of GLM's time: it spent 14,000 to 16,800 reasoning tokens a trial, against GLM's 2,800 to 3,500 | [#865][i865], [#897][i897], [#977][i977] |

"Older client" means OpenCode before 2.0.32. Its times do not compare with the
rows above. Stacks dropped before any test, and results from before the client
fix of 2026-10-06 ([#968][i968]), are in the archive below.

---

## Where the rest went

| for | see |
|---|---|
| this page before 2026-10-27: every stack table, tool versions, open questions | [`recommendations-archive-2026-10-27.md`](recommendations-archive-2026-10-27.md) (frozen) |
| every row, step by step from two clean Sparks | [`howto.md`](howto.md) |
| the cluster: hardware, cable, RoCE, NCCL | [`README.md`](README.md) · [`docs/dgx-cluster-setup.md`](../../docs/dgx-cluster-setup.md) |
| every trial, one JSON line each | [`results.jsonl`](results.jsonl) |
| the numbers on this page, again | `uv run python scripts/screen_stacks.py --results hardware/Cortex-X925-128GB-GB10-x2/results.jsonl --since 2026-10-18T00:00:00-0400` (add `--standard` for the standard set) |
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
[i930]: https://github.com/evanwtf/local-llm/issues/930
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
[i1319]: https://github.com/evanwtf/local-llm/issues/1319
[i1334]: https://github.com/evanwtf/local-llm/issues/1334
[i1177]: https://github.com/evanwtf/local-llm/issues/1177
[i1231]: https://github.com/evanwtf/local-llm/issues/1231
[i1244]: https://github.com/evanwtf/local-llm/issues/1244
[i1246]: https://github.com/evanwtf/local-llm/issues/1246
[i1255]: https://github.com/evanwtf/local-llm/issues/1255
[i1262]: https://github.com/evanwtf/local-llm/issues/1262
