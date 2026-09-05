# scripts/

One line per script, taken from its own docstring so this file cannot drift
into describing something the script no longer does. Regenerate with:

    uv run python scripts/make_scripts_readme.py

Each script explains itself in full at the top of its own file -- what it
computes, and why that and not a neighbouring thing. This is an index, not
documentation.

| script | what it does |
|---|---|
| `archive_pre_dir_rows.py` | Move every pre---dir OpenCode row out of results.jsonl into the archive. |
| `backfill_client_version.py` | Fill `client_version` on rows that predate it, and only where it is known. |
| `backfill_prompt_meta.py` | Write an inferred prompt sidecar for runs measured before #140. |
| `bitexact_ab.py` | Bit-exact A/B for two ds4 engine trees: the output-equality check #143 lacked. |
| `client_version_split.py` | Which client version took which rows, and what that confounds (#137, #131). |
| `client_versions.py` | Read the recorded agent client versions, and say which have moved (#131). |
| `cohort_split.py` | Split one backend's rows at a moment in time and compare the halves. |
| `decode_ab_report.py` | Summarise a paired decode A/B produced by scripts/decode_ab.sh (#48). |
| `disk_baseline.py` | Measure the internal NVMe: read bandwidth and random-read latency. |
| `eval_trace.py` | Read a `ds4-eval` trace: pass rate, and tokens spent reaching each answer. |
| `exclude_rows.py` | Mark rows from an aborted or void run as excluded, so they cannot publish. |
| `gguf_meta.py` | Print a GGUF file's metadata without loading the model. |
| `hardware_id.py` | Derive a machine's results-directory name from the machine itself. |
| `hf_sweep.py` | Watch Hugging Face for new quants of the models we actually run. |
| `kv_prefix_audit.py` | Measure how much prefill a stalled KV prefix costs (#64, #50). |
| `make_scripts_readme.py` | Generate scripts/README.md from each script's own first docstring line. |
| `post_ab_run.py` | Post one completed decode-A/B run to a GitHub issue, once. |
| `prefix_stability.py` | Find which cached prefix block changes between two requests (#50, #64). |
| `prompt_meta.py` | Which prompt a decode/prefill A/B was measured on (#140). |
| `prune_models.py` | Delete local model weights that are superseded and re-downloadable (#111). |
| `report.py` | Summarise and compare measured cells, with the resolution rule applied. |
| `sensor_windows.py` | Join a monitord sensor series to benchmark sweep windows. |
| `session_decay.py` | Does a session get worse the longer the server runs? (#120) |
| `stack_agent_report.py` | Read out for the #138 stack A/B: two whole stacks, four sweeps, one screen. |
| `sync_sandbox_targets.py` | Clone the harness's own copies of the task repositories into `sandbox/`. |
| `thermals.py` | Read this Mac's die temperatures, with a timestamp, without sudo. |
| `tool_error_conditional.py` | Does a tool error make the NEXT tool call more likely to fail? (#112) |
| `upstream_sweep.py` | Sweep the repositories this project depends on, in one command. |
| `verify_posts.py` | Verify X posts against the source, for the claims that earned an issue. |
| `ab_status.sh` | One status line for a set of decode-A/B run directories. |
| `coherence_check.sh` | Greedy coherence check before trusting any new GGUF (#25, #48). |
| `decode_ab.sh` | Paired decode-rate A/B for two GGUFs of the same model (#48). |
| `decode_ab_engine.sh` | Paired decode-rate A/B for two ENGINE BUILDS of the same GGUF (#118). |
| `decode_ab_repeat.sh` | Run the same decode A/B N times, into numbered directories (#136). |
| `decode_ab_stack.sh` | Paired decode A/B for two whole STACKS -- engine tree + weights together. |
| `disk_kv_mechanism_test.sh` | 112 disk-KV mechanism test (2026-09-03). |
| `install-metal-ceiling.sh` | Persist the Metal wired limit across reboots. |
| `restart_between_trials.sh` | Restart-between-trials experiment for #112. |
| `restart_between_trials_armB.sh` | 77 arm B re-run under restart-between-trials (2026-09-03). |
| `stack_agent_ab.sh` | Interleaved agent-suite A/B for two whole STACKS -- engine + weights (#138). |

