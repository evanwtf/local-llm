# scripts/

One line per script, taken from its own docstring so this file cannot drift into describing something the script no longer does. The **platform** column is the machine a script must run on -- `any` unless it drives one machine's engine or sensors. Regenerate with:

    uv run python scripts/make_scripts_readme.py

Each script explains itself in full at the top of its own file -- what it computes, and why that and not a neighbouring thing. This is an index, not documentation. **Check it before writing analysis code**: the tool you need may already be here (`gguf_meta.py` reads GGUF metadata without loading the model; a heredoc that re-derives it is the drift this index exists to prevent).

| script | platform | what it does |
|---|---|---|
| `ab_driver.py` | any | The arm-alternation loop every A/B driver in this repo re-implements. #235 stage 3. |
| `ab_status.py` | any | One status line for a set of decode-A/B run directories. |
| `archive_pre_dir_rows.py` | any | Move every pre---dir OpenCode row out of results.jsonl into the archive. |
| `arm_order_effect.py` | any | How much does running second inside a rep cost? (#130) |
| `backfill_client_version.py` | any | Fill `client_version` on rows that predate it, and only where it is known. |
| `backfill_iso8601.py` | any | Convert existing timestamps to ISO 8601 with an explicit offset. |
| `backfill_prompt_meta.py` | any | Write an inferred prompt sidecar for runs measured before #140. |
| `bitexact_ab.py` | mac | Bit-exact A/B for two ds4 engine trees: the output-equality check #143 lacked. |
| `bonsai_quant_report.py` | any | Ternary-Bonsai on the 3080 Ti: is it the quantization, or the engine? #269 |
| `calibrate_settle.py` | mac | What slope does an already-settled die actually show? #276 |
| `check_metal_equivalence.py` | mac | Run ds4's Metal tensor-route equivalence test and cache the verdict (#149). |
| `check_release_version.py` | any | Refuse a release whose tag disagrees with the declared version. |
| `client_version_split.py` | any | Which client version took which rows, and what that confounds (#137, #131). |
| `client_versions.py` | any | Read the recorded agent client versions, and say which have moved (#131). |
| `coherence_check.py` | mac | Greedy coherence check before trusting any new GGUF (#25, #48). |
| `cohort_split.py` | any | Split one backend's rows at a moment in time and compare the halves. |
| `decode_ab.py` | mac | Paired decode-rate A/B for two GGUFs of the same model (#48). #235 stage 4. |
| `decode_ab_engine.py` | mac | Paired decode-rate A/B for two ENGINE BUILDS of the same GGUF. #118 |
| `decode_ab_repeat.py` | mac | Run the same decode A/B N times, into numbered directories (#136). |
| `decode_ab_report.py` | any | Summarize a paired decode A/B produced by scripts/decode_ab.py (#48). |
| `decode_ab_stack.py` | mac | Paired decode-rate A/B for two whole STACKS -- engine tree and weights together. #138 |
| `degeneration_cascade.py` | any | Measure the #112 tool-call degeneration from captured OpenCode transcripts. |
| `degeneration_cascade_run.py` | mac | A fresh, transcript-capturing run of the `qwen38fnds4shim` cell for #112. |
| `dgx_metrics.py` | any | DGX Spark vLLM app-level metrics snapshot from Prometheus, via gcx. |
| `dgx_server.py` | any | Control every long-lived DGX Spark server by recorded systemd scope. #429 |
| `disk_baseline.py` | any | Measure the internal NVMe: read bandwidth and random-read latency. |
| `disk_kv_mechanism.py` | mac | Is the disk-KV budget what makes arm A decline by trial 3? #112 |
| `ds4_serve.py` | mac | Start ds4-server on one of two Metal kernel routes, and prove which one ran. |
| `engine_timing.py` | any | Per-request engine timing from a llama.cpp server log, for #346. |
| `equiv_five_report.py` | any | Report the argv/env equivalence diff for the five non-route_agent ports. #235, #264, #149 |
| `eval_trace.py` | mac | Read a `ds4-eval` trace: pass rate, and tokens spent reaching each answer. |
| `evidence.py` | any | Re-run a finding's claims and say whether they still hold. #160 |
| `exclude_rows.py` | any | Mark rows from an aborted or void run as excluded, so they cannot publish. |
| `fan_ab.py` | mac | Fans auto vs fans max, interleaved, on one build. #276 (parent #116) |
| `fan_ab_collate.py` | mac | One tidy CSV for a fan A/B run: every datapoint, on every row. #276 |
| `fan_ab_report.py` | mac | Read a `fan_ab.py` run: does forced cooling change anything? #276 (parent #116) |
| `gen_equiv_fixtures.py` | any | Generate the argv/env equivalence fixtures for the #235 port. #235, #264, #149 |
| `gguf_meta.py` | any | Print a GGUF file's metadata without loading the model. |
| `gpu_utilization.py` | nvidia | Record what the GPU is actually doing, and say whether the box is earning it. |
| `greedy_mtp_ab.py` | mac | The first ds4 MTP arm that can actually draft, against its own control. |
| `hardware_id.py` | any | Derive a machine's results-directory name from the machine itself. |
| `hf_sweep.py` | any | Watch Hugging Face for new quants of the models we actually run. |
| `kv_prefix_audit.py` | mac | Measure how much prefill a stalled KV prefix costs (#64, #50). |
| `kv_prefix_reuse.py` | mac | Measure how much of a prompt ds4 reuses from its prefix cache (#190). |
| `load_matrix.py` | mac | Load each of a set of gguf files with the PLE sidecar, serially, and |
| `local_agent.py` | any | Start a recommended local stack and drop into a coding agent. #235 |
| `mac_dash.py` | any | M5 Max GPU / thermal / power snapshot from Prometheus, via gcx. |
| `machine_claim.py` | any | Claim the machine with intent, and make contention impossible to miss. #160 |
| `machine_health.py` | any | Is this machine in a state to start work, and did the work actually start? |
| `machine_state.py` | any | Is the machine busy, and who says so? One answer, for every agent. |
| `machines.py` | any | The hardware this project manages -- the single source of truth (#302). |
| `make_next.py` | any | Print what to do next, live from the open issues' labels. |
| `make_scripts_readme.py` | any | Generate scripts/README.md from each script's own first docstring line. |
| `memory_gate.py` | nvidia | Wait for memory to be safe before starting the next trial. |
| `metal_knob_ab.py` | mac | Paired decode-rate A/B for one Metal knob env var within one tree. #162 |
| `model_probe.py` | any | Check a served model's answers in code, never by reading them. |
| `moe_tile_ab.py` | mac | Paired prefill/decode A/B for the ds4 MoE tensor-tile level, one tree. #328 |
| `mtp_draft_audit.py` | mac | Audit an MTP arm's drafting counters: the server log beside the ledger. |
| `mtp_engagement.py` | mac | Does the engine engage MTP on the traffic we actually send it? (#148, #151) |
| `mtp_log_split.py` | mac | Did MTP draft during the batch, or only before it? (#39, #151, #210) |
| `mtp_recovery_attribution.py` | mac | Attribute the #39 recovery-failed events to trials, one row per trial (#39). |
| `mtp_replay_probe.py` | mac | Replay a captured agent request and bisect what switches ds4's MTP off (#151). |
| `mtp_treatment_gate.py` | mac | Prove the MTP refusal fires, then take rows that carry the treatment (#210). |
| `oom_watchdog.py` | any | Notice an OOM kill and put the box's reachability back. #459 |
| `os_compare.py` | any | Build the macOS 26-vs-27 dataset for the M5 Max (#499). |
| `paired_ab_report.py` | any | Read out a paired two-arm A/B from the ledger. #240 |
| `peer_brief.py` | any | Generate the state half of a handoff as Markdown. #160 |
| `peer_status.py` | any | One deterministic status line for the peer work. #160 |
| `post_ab_run.py` | any | Post one completed decode-A/B run to a GitHub issue, once. |
| `prefill_chunk_ab.py` | mac | Paired A/B for one ds4-bench --prefill-chunk value within one tree. #267 |
| `prefix_stability.py` | mac | Find which cached prefix block changes between two requests (#50, #64). |
| `prefix_stall.py` | mac | Measure the live-KV prefix stall across a corpus of ds4-server logs (#64). |
| `prompt_meta.py` | any | Which prompt a decode/prefill A/B was measured on (#140). |
| `prune_models.py` | any | Delete local model weights that are superseded and re-downloadable (#111). |
| `qwen38_metal_suites.py` | mac | Run ds4#990's model-free Qwen3.8-Flash-Next Metal suites and report (#170). |
| `reasoning_budget_signature.py` | any | Does an empty agent solution coincide with a spent reasoning budget? (#349) |
| `reasoning_budget_sweep.py` | any | Does a short max_tokens with thinking ON return empty content? (#349) |
| `reco_rows.py` | any | The numbers a RECOMMENDATIONS row quotes, regenerated from a ledger. #524 |
| `refuse_commit_during_benchmark.py` | any | Refuse a commit while a benchmark holds the run lock (#227, #237). |
| `release_notes.py` | any | Print the changelog section for a release, or refuse. |
| `relevance_score.py` | any | Score how relevant an outside claim is to THIS project, procedurally (#230). |
| `report.py` | any | Summarize and compare measured cells, with the resolution rule applied. |
| `restart_between_trials.py` | any | Restart-between-trials: does server state degrade a session? #112, #77. |
| `route_ab_report.py` | mac | Attribute #149 route-A/B rows to sweep windows and evaluate the screens. |
| `route_agent_ab.py` | mac | ds4's Metal 4 TensorOps route against the withheld one, on the agent bench. |
| `sensor_windows.py` | mac | Join a monitord sensor series to benchmark sweep windows. |
| `session_decay.py` | any | Does a session get worse the longer the server runs? (#120) |
| `setup_earlyoom.py` | any | Put earlyoom's configuration in the repo, and check the box still matches. #458 |
| `shell_debt.py` | any | How much shell is left, and how much of it can still misidentify a process. |
| `stack_agent_ab.py` | mac | Interleaved agent-suite A/B for two whole STACKS -- engine + weights. #138 |
| `stack_agent_report.py` | mac | Read out for the #138 stack A/B: two whole stacks, four sweeps, one screen. |
| `stack_agent_report_191.py` | mac | Read out for the #191 stack A/B: mlx-serve against ds4, one screen. |
| `strip_ab_report.py` | any | Read out the #112 strip-toggle A/B. |
| `strip_toggle_ab.py` | mac | Does echoing the shim's own scaffolding back carry the tool-call loop? #112 |
| `sync_sandbox_targets.py` | any | Clone the harness's own copies of the task repositories into `sandbox/`. |
| `tail_events.py` | any | Count num_turns > 20 events across the ledger, by task and by backend (#191). |
| `targets_ab.py` | any | Does the sandbox target layout change the pass rate? #146 |
| `thermals.py` | mac | Read this Mac's die temperatures, with a timestamp, without sudo. |
| `tool_error_conditional.py` | any | Does a tool error make the NEXT tool call more likely to fail? (#112) |
| `tool_retry_count.py` | any | Count tool-call outcomes from an OpenCode client transcript. |
| `ttft_probe.py` | any | Time-to-first-token against a vLLM endpoint, client-observed vs engine (#346). |
| `unitctl.py` | any | start / stop / status for the processes this repo runs. #234 |
| `upstream_sweep.py` | any | Sweep the repositories this project depends on, in one command. |
| `validate_ledgers.py` | any | Validate EVERY committed hardware ledger, independent of the runner (#394). |
| `verify_posts.py` | any | Verify X posts against the source, for the claims that earned an issue. |
| `verify_push.py` | any | Verify a push landed, instead of trusting that `git push` reported success (#255). |
| `vllm_compat_proxy.py` | any | Strip non-standard fields from a vLLM fork's chat responses so a strict |
| `vllm_load.py` | nvidia | Aggregate throughput against a vLLM server at a given concurrency (#334). |
| `ds4-fast.sh` | mac | Metal 4 TensorOps: ~21% faster, not bit-exact. See scripts/ds4_serve.py. |
| `ds4-vanilla.sh` | mac | Reference kernels: bit-exact, slower. See scripts/ds4_serve.py. |
| `install-metal-ceiling.sh` | mac | Persist the Metal wired limit across reboots. |
| `local-agent.sh` | any | Start a recommended local stack and drop into a coding agent (see RECOMMENDATIONS.md). |

