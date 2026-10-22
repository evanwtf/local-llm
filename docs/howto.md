# Stand up a measured stack yourself

**Written 2026-10-22T08:40:00-0400 for [#1202](https://github.com/evanwtf/local-llm/issues/1202).**
This page is the index. It sends you to a step-by-step procedure for each
stack that a machine's `RECOMMENDATIONS.md` names as a pick. A **stack** is
a model, an engine and a harness. The harness is OpenCode in every procedure.

The procedures assume no context. Each one lists the prerequisites with
install commands, the weights at the measured revision, the engine at the
measured version or image digest, the server command, a real chat request
that proves the server answers, the OpenCode provider block, and the stop
command. Each section names the file its commands come from. Each page ends
with the steps that the repository does not record.

## Pick your hardware

| hardware | stacks covered | procedure |
|---|---|---|
| Apple Silicon Mac, 128 GB (measured on an M5 Max MacBook Pro, macOS 27) | 6: Ollama ×2, mlx-serve, ds4 ×2, llama.cpp | [`hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/howto.md`](../hardware/MacBook-Pro-M5-Max-128GB-Z1MZ0002NLL_A/howto.md) |
| One NVIDIA DGX Spark (GB10, 128 GB) | 9: SGLang ×2, vLLM ×2, llama.cpp ×4, TensorFold | [`hardware/Cortex-X925-128GB-GB10/howto.md`](../hardware/Cortex-X925-128GB-GB10/howto.md) |
| Two DGX Sparks joined by ConnectX-7 | 4: vLLM ×2, TensorFold ×2 | [`hardware/Cortex-X925-128GB-GB10-x2/howto.md`](../hardware/Cortex-X925-128GB-GB10-x2/howto.md) |

The Ryzen 9 + RTX 3080 Ti desktop has no picks, so it has no procedure. Its
engine setup is in
[`hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/setup/README.md`](../hardware/Ryzen9-7900X-32GB-RTX3080Ti-12GB/setup/README.md).

**Numbers do not cross machines.** A pass rate or a median from one machine
says nothing about another. Read the numbers in that machine's
`RECOMMENDATIONS.md`.

## Steps that are the same everywhere

1. Install OpenCode on the machine you will type at:

   ```sh
   curl -fsSL https://opencode.ai/install | bash
   ```

2. Put one provider block in `~/.config/opencode/opencode.json` and set
   `"model"` to `<provider>/<model>`. A model that is not declared there
   makes `opencode run` exit in under a second, and it looks like a model
   failure ([#69](https://github.com/evanwtf/local-llm/issues/69)).
3. Prove the server answers with a real chat completion, not `/health`. A
   two-node server passes `/health` with a dead worker rank.
4. Always pass `--dir` to `opencode run`. Without it OpenCode writes the files
   somewhere else:

   ```sh
   opencode run --dir "$PWD" "your task"
   ```

5. Stop a server with its own stop command, a recorded process, or Ctrl-C.
   Do not use `pkill -f`: it can match the shell that typed it
   ([`docs/dgx-spark-runbook.md`](dgx-spark-runbook.md)).

## Related

- [`docs/stacks.md`](stacks.md): the M5 Max stacks by hand, with the reasons
  behind each flag.
- [`docs/dgx-cluster-howto.md`](dgx-cluster-howto.md): building the two-Spark
  cluster itself.
- `benchmarks/agent/tasks.toml`: every backend's model, port and served name.
