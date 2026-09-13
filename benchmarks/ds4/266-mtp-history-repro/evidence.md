# #266 reproduction: MTP-history graph stage fails on tool-call-recovery re-prefill

Deterministic minimal reproduction outside the agent harness, with the causal
A/B (MTP head loaded vs omitted). Taken 2026-09-13.

## Setup
- Engine: `ds4-server` at `ba01f5d` (ds4-metal, our pin).
- Base: `Qwen3.8-Flash-Next-Q40RoutedExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf`
  (arch `qwen4-exp` — loads on `ba01f5d`; the `qwen4exp` pleext pack is refused, #279).
- PLE sidecar: `Qwen3.8-Flash-Next-PLE-Q4_1.gguf`.
- Treatment adds: `--mtp-model qwen3.8-flash-next-q4-mtp.gguf --mtp-draft 7` (graph `MTP=Q4_K/Q8_0/BF16`).
- Control: no `--mtp-model` (graph `MTP=off`).

## Trigger
`repro.py`: one `/v1/chat/completions`, ~60 KB passage (ctx ~17.8k tokens), a
defined tool, and an instruction to call an **undefined** function
(`deep_analyze`). `temperature:0`, `max_tokens:600`. The model emits a tool
call the server rejects as invalid, triggering the recovery re-prefill.

`max_tokens` must be large enough to finish the invalid tool call; a short
budget is spent "THINKING" and never reaches a tool call (reproduces nothing).

## Result — 4 requests per arm

| arm | `stage=MTP-history` | `metal Qwen prefill failed` | client |
|---|---:|---:|---|
| MTP head loaded | 4 / 4 | 4 (all `pos=18411 rows=61`) | `finish=error "invalid tool call recovery failed: metal Qwen prefill failed at position 18411"` |
| MTP head omitted | 0 / 4 | 0 | `finish=error "unterminated tool call"` — recovery re-prefill succeeds |

Deterministic at temperature 0: every treatment failure is at the same
`pos=18411 rows=61`. The only difference between arms is the MTP head, so the
MTP-gated history stage (`qwen4_graph_mtp_history_after_target`,
ds4.c:56580-56640 at ds4-metal ba01f5d) is causal, not merely where the failure is
detected.

## Retry / completion
Direct to `ds4-server` the failure surfaces as `finish=error`; the server's
internal retry did not rescue it (all 4 treatment requests errored). In the
agent harness the shim retries the whole request, which usually completes on a
later attempt — which is why the row reads `passed: true` while the log carries
the failure. The re-prefill does not self-heal.

## Reuse
`uv run python repro.py <prompt-file> <chars> <n>` against a server on :8199.
Launch treatment/control as above.
