# scripts/

One line per script, taken from its own docstring so this file cannot drift
into describing something the script no longer does. Regenerate with:

    uv run python scripts/make_scripts_readme.py

Each script explains itself in full at the top of its own file -- what it
computes, and why that and not a neighbouring thing. This is an index, not
documentation.

| script | what it does |
|---|---|
| `ab_driver.py` | The arm-alternation loop every A/B driver in this repo re-implements. #235 stage 3. |
| `ab_status.py` | One status line for a set of decode-A/B run directories. |
| `archive_pre_dir_rows.py` | Move every pre---dir OpenCode row out of results.jsonl into the archive. |
| `arm_order_effect.py` | How much does running second inside a rep cost? (#130) |
| `backfill_client_version.py` | Fill `client_version` on rows that predate it, and only where it is known. |
| `backfill_iso8601.py` | Convert existing timestamps to ISO 8601 with an explicit offset. |
| `backfill_prompt_meta.py` | Write an inferred prompt sidecar for runs measured before #140. |
| `bitexact_ab.py` | Bit-exact A/B for two ds4 engine trees: the output-equality check #143 lacked. |
| `calibrate_settle.py` | What slope does an already-settled die actually show? #276 |
| `check_metal_equivalence.py` | Run ds4's Metal tensor-route equivalence test and cache the verdict (#149). |
| `check_release_version.py` | Refuse a release whose tag disagrees with the declared version. |
| `client_version_split.py` | Which client version took which rows, and what that confounds (#137, #131). |
| `client_versions.py` | Read the recorded agent client versions, and say which have moved (#131). |
| `coherence_check.py` | Greedy coherence check before trusting any new GGUF (#25, #48). |
| `cohort_split.py` | Split one backend's rows at a moment in time and compare the halves. |
| `decode_ab.py` | Paired decode-rate A/B for two GGUFs of the same model (#48). #235 stage 4. |
| `decode_ab_engine.py` | Paired decode-rate A/B for two ENGINE BUILDS of the same GGUF. #118 |
| `decode_ab_repeat.py` | Run the same decode A/B N times, into numbered directories (#136). |
| `decode_ab_report.py` | Summarize a paired decode A/B produced by scripts/decode_ab.py (#48). |
| `decode_ab_stack.py` | Paired decode-rate A/B for two whole STACKS -- engine tree and weights together. #138 |
| `disk_baseline.py` | Measure the internal NVMe: read bandwidth and random-read latency. |
| `disk_kv_mechanism.py` | Is the disk-KV budget what makes arm A decline by trial 3? #112 |
| `ds4_serve.py` | Start ds4-server on one of two Metal kernel routes, and prove which one ran. |
| `equiv_five_report.py` | Report the argv/env equivalence diff for the five non-route_agent ports. #235, #264, #149 |
| `eval_trace.py` | Read a `ds4-eval` trace: pass rate, and tokens spent reaching each answer. |
| `evidence.py` | Re-run a finding's claims and say whether they still hold. #160 |
| `exclude_rows.py` | Mark rows from an aborted or void run as excluded, so they cannot publish. |
| `fan_ab.py` | Fans auto vs fans max, interleaved, on one build. #276 (parent #116) |
| `fan_ab_collate.py` | One tidy CSV for a fan A/B run: every datapoint, on every row. #276 |
| `fan_ab_report.py` | Read a `fan_ab.py` run: does forced cooling change anything? #276 (parent #116) |
| `gen_equiv_fixtures.py` | Generate the argv/env equivalence fixtures for the #235 port. #235, #264, #149 |
| `gguf_meta.py` | Print a GGUF file's metadata without loading the model. |
| `greedy_mtp_ab.py` | The first ds4 MTP arm that can actually draft, against its own control. |
| `hardware_id.py` | Derive a machine's results-directory name from the machine itself. |
| `hf_sweep.py` | Watch Hugging Face for new quants of the models we actually run. |
| `kv_prefix_audit.py` | Measure how much prefill a stalled KV prefix costs (#64, #50). |
| `kv_prefix_reuse.py` | Measure how much of a prompt ds4 reuses from its prefix cache (#190). |
| `load_matrix.py` | Load each of a set of gguf files with the PLE sidecar, serially, and |
| `local_agent.py` | Start a recommended local stack and drop into a coding agent. #235 |
| `machine_claim.py` | Claim the machine with intent, and make contention impossible to miss. #160 |
| `machine_state.py` | Is the machine busy, and who says so? One answer, for every agent. |
| `make_next.py` | Generate NEXT.md from the open issues, so the queue cannot drift or bloat. |
| `make_scripts_readme.py` | Generate scripts/README.md from each script's own first docstring line. |
| `metal_knob_ab.py` | Paired decode-rate A/B for one Metal knob env var within one tree. #162 |
| `mtp_draft_audit.py` | Audit an MTP arm's drafting counters: the server log beside the ledger. |
| `mtp_engagement.py` | Does the engine engage MTP on the traffic we actually send it? (#148, #151) |
| `mtp_log_split.py` | Did MTP draft during the batch, or only before it? (#39, #151, #210) |
| `mtp_recovery_attribution.py` | Attribute the #39 recovery-failed events to trials, one row per trial (#39). |
| `mtp_replay_probe.py` | Replay a captured agent request and bisect what switches ds4's MTP off (#151). |
| `mtp_treatment_gate.py` | Prove the MTP refusal fires, then take rows that carry the treatment (#210). |
| `paired_ab_report.py` | Read out a paired two-arm A/B from the ledger. #240 |
| `peer_brief.py` | Generate the state half of a handoff as Markdown. #160 |
| `peer_status.py` | One deterministic status line for the peer work. #160 |
| `post_ab_run.py` | Post one completed decode-A/B run to a GitHub issue, once. |
| `prefill_chunk_ab.py` | Paired A/B for one ds4-bench --prefill-chunk value within one tree. #267 |
| `prefix_stability.py` | Find which cached prefix block changes between two requests (#50, #64). |
| `prefix_stall.py` | Measure the live-KV prefix stall across a corpus of ds4-server logs (#64). |
| `prompt_meta.py` | Which prompt a decode/prefill A/B was measured on (#140). |
| `prune_models.py` | Delete local model weights that are superseded and re-downloadable (#111). |
| `qwen38_metal_suites.py` | Run ds4#990's model-free Qwen3.8-Flash-Next Metal suites and report (#170). |
| `refuse_commit_during_benchmark.py` | Refuse a commit while a benchmark holds the run lock (#227, #237). |
| `release_notes.py` | Print the changelog section for a release, or refuse. |
| `relevance_score.py` | Score how relevant an outside claim is to THIS project, procedurally (#230). |
| `report.py` | Summarize and compare measured cells, with the resolution rule applied. |
| `restart_between_trials.py` | Restart-between-trials: does server state degrade a session? #112, #77. |
| `route_ab_report.py` | Attribute #149 route-A/B rows to sweep windows and evaluate the screens. |
| `route_agent_ab.py` | ds4's Metal 4 TensorOps route against the withheld one, on the agent bench. |
| `sensor_windows.py` | Join a monitord sensor series to benchmark sweep windows. |
| `session_decay.py` | Does a session get worse the longer the server runs? (#120) |
| `shell_debt.py` | How much shell is left, and how much of it can still misidentify a process. |
| `stack_agent_ab.py` | Interleaved agent-suite A/B for two whole STACKS -- engine + weights. #138 |
| `stack_agent_report.py` | Read out for the #138 stack A/B: two whole stacks, four sweeps, one screen. |
| `stack_agent_report_191.py` | Read out for the #191 stack A/B: mlx-serve against ds4, one screen. |
| `strip_ab_report.py` | Read out the #112 strip-toggle A/B. |
| `strip_toggle_ab.py` | Does echoing the shim's own scaffolding back carry the tool-call loop? #112 |
| `sync_sandbox_targets.py` | Clone the harness's own copies of the task repositories into `sandbox/`. |
| `tail_events.py` | Count num_turns > 20 events across the ledger, by task and by backend (#191). |
| `targets_ab.py` | Does the sandbox target layout change the pass rate? #146 |
| `thermals.py` | Read this Mac's die temperatures, with a timestamp, without sudo. |
| `tool_error_conditional.py` | Does a tool error make the NEXT tool call more likely to fail? (#112) |
| `tool_retry_count.py` | Count tool-call outcomes from an OpenCode client transcript. |
| `unitctl.py` | start / stop / status for the processes this repo runs. #234 |
| `upstream_sweep.py` | Sweep the repositories this project depends on, in one command. |
| `verify_posts.py` | Verify X posts against the source, for the claims that earned an issue. |
| `ds4-fast.sh` | Metal 4 TensorOps: ~21% faster, not bit-exact. See scripts/ds4_serve.py. |
| `ds4-vanilla.sh` | Reference kernels: bit-exact, slower. See scripts/ds4_serve.py. |
| `install-metal-ceiling.sh` | Persist the Metal wired limit across reboots. |
| `local-agent.sh` | Start a recommended local stack and drop into a coding agent (see RECOMMENDATIONS.md). |

