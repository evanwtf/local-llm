# Session timeline — DeepSeek V4 Flash 0731 benchmarking

All times local (America/New_York), 2026-08-08. Regenerate with `sh bench-0731/build_timeline.sh`.

```
[2026-08-08 01:19:38] waiting for ds4f-q2 0731
[2026-08-08 01:32:08] ds4f-q2 0731 complete (86720111488 bytes)
[2026-08-08 01:32:08] waiting for ds4f-q2-q4 0731
[2026-08-08 01:47:08] ds4f-q2-q4 0731 complete (97591747456 bytes)
[2026-08-08 01:47:08] speed sweep: baseline
[2026-08-08 01:51:46] speed sweep done: baseline (exit 0)
[2026-08-08 01:51:46] speed sweep: q2_0731
[2026-08-08 01:56:28] speed sweep done: q2_0731 (exit 0)
[2026-08-08 01:56:28] speed sweep: q2q4_0731
[2026-08-08 02:01:21] perplexity: baseline
[2026-08-08 02:01:21] speed sweep done: q2q4_0731 (exit 0)
[2026-08-08 02:18:39] perplexity done: baseline (exit 0)
[2026-08-08 02:18:39] perplexity: q2_0731
[2026-08-08 02:35:35] perplexity done: q2_0731 (exit 0)
[2026-08-08 02:35:35] perplexity: q2q4_0731
[2026-08-08 02:52:38] eval harness: baseline
[2026-08-08 02:52:38] perplexity done: q2q4_0731 (exit 0)
[2026-08-08 03:05:46] eval done: baseline (exit 1)
[2026-08-08 03:05:46] eval harness: q2_0731
[2026-08-08 03:15:11] eval done: q2_0731 (exit 1)
[2026-08-08 03:15:11] eval harness: q2q4_0731
[2026-08-08 03:24:07] ALL DONE
[2026-08-08 03:24:07] ds4flash.gguf restored to baseline
[2026-08-08 03:24:07] eval done: q2q4_0731 (exit 1)
[2026-08-08 03:24:31] eval8k: baseline
[2026-08-08 03:50:07] eval8k done: baseline (exit 1)
[2026-08-08 03:50:07] eval8k: q2_0731
[2026-08-08 04:05:49] eval8k done: q2_0731 (exit 1)
[2026-08-08 04:05:49] eval8k: q2q4_0731
[2026-08-08 04:18:32] EVAL8K ALL DONE
[2026-08-08 04:18:32] eval8k done: q2q4_0731 (exit 0)
[2026-08-08 07:21:35] waiting for DSpark support GGUF
[2026-08-08 07:36:35] DSpark support GGUF complete
[2026-08-08 07:36:35] q2q4 p1: off
[2026-08-08 07:36:50] q2q4 p1: dspark draft1
[2026-08-08 07:37:12] q2q4 p1: dspark draft2
[2026-08-08 07:37:32] q2q4 p1: dspark draft3
[2026-08-08 07:37:53] q2q4 p1: dspark draft4
[2026-08-08 07:38:14] q2q4 p2: off
[2026-08-08 07:38:29] q2q4 p2: dspark draft1
[2026-08-08 07:38:50] q2q4 p2: dspark draft2
[2026-08-08 07:39:12] q2q4 p2: dspark draft3
[2026-08-08 07:39:33] q2q4 p2: dspark draft4
[2026-08-08 07:39:55] q2q4 p3: off
[2026-08-08 07:40:10] q2q4 p3: dspark draft1
[2026-08-08 07:40:32] q2q4 p3: dspark draft2
[2026-08-08 07:40:53] q2q4 p3: dspark draft3
[2026-08-08 07:41:14] q2q4 p3: dspark draft4
[2026-08-08 07:41:36] q2 p1: off
[2026-08-08 07:42:00] q2 p1: dspark draft1
[2026-08-08 07:42:44] p1: off
[2026-08-08 07:43:08] p1: strict (capture cost only)
[2026-08-08 07:43:25] p1: conf default 0.7
[2026-08-08 07:43:45] p1: conf 0.5
[2026-08-08 07:44:08] p1: conf 0.3
[2026-08-08 07:44:32] p1: conf 0 (forced 5-token blocks)
[2026-08-08 07:45:00] p2: off
[2026-08-08 07:45:15] p2: strict (capture cost only)
[2026-08-08 07:45:32] p2: conf default 0.7
[2026-08-08 07:45:53] p2: conf 0.5
[2026-08-08 07:46:16] p2: conf 0.3
[2026-08-08 07:46:42] p2: conf 0 (forced 5-token blocks)
[2026-08-08 07:47:10] p3: off
[2026-08-08 07:47:26] p3: strict (capture cost only)
[2026-08-08 07:47:43] p3: conf default 0.7
[2026-08-08 07:48:05] p3: conf 0.5
[2026-08-08 07:48:28] p3: conf 0.3
[2026-08-08 07:48:54] p3: conf 0 (forced 5-token blocks)
[2026-08-08 07:49:22] DSPARK2 ALL DONE
[2026-08-08 09:40:43] cooldown 90s before cold_default
[2026-08-08 09:42:13] probe: cold_default
[2026-08-08 09:42:28] cooldown 90s before cold_warm_weights
[2026-08-08 09:42:28] probe done: cold_default
[2026-08-08 09:43:58] probe: cold_warm_weights
[2026-08-08 09:44:11] cooldown 90s before cold_chunk8192
[2026-08-08 09:44:11] probe done: cold_warm_weights
[2026-08-08 09:45:41] probe: cold_chunk8192
[2026-08-08 09:45:48] cooldown 90s before cold_chunk2048
[2026-08-08 09:45:48] probe done: cold_chunk8192
[2026-08-08 09:47:18] probe: cold_chunk2048
[2026-08-08 09:47:25] cooldown 90s before cold_quality
[2026-08-08 09:47:25] probe done: cold_chunk2048
[2026-08-08 09:48:55] probe: cold_quality
[2026-08-08 09:49:05] probe done: cold_quality
[2026-08-08 09:49:05] probe: hot_default (no cooldown)
[2026-08-08 09:49:12] PREFILL PROBE DONE
[2026-08-08 09:49:12] probe done: hot_default
[2026-08-08 09:50:09] === repeat 1 ===
[2026-08-08 09:51:21] === repeat 2 ===
[2026-08-08 09:52:46] === repeat 3 ===
[2026-08-08 09:54:10] PREFILL PROBE2 DONE
[2026-08-08 10:27:24] === repeat 1 ===
[2026-08-08 10:28:44] === repeat 2 ===
[2026-08-08 10:30:05] === repeat 3 ===
[2026-08-08 10:31:21] PREFILL PROBE3 DONE
[2026-08-08 10:38:19] evalfull start: q2q4_0731
[2026-08-08 12:58:38] evalfull done: q2q4_0731 -- ds4-eval: 76/92 passed, 16 failed, runtime 02h:20m
[2026-08-08 12:58:38] evalfull start: q2_0731
[2026-08-08 15:23:49] evalfull done: q2_0731 -- ds4-eval: 68/92 passed, 24 failed, runtime 02h:25m
[2026-08-08 15:23:49] evalfull start: baseline
[2026-08-08 18:31:33] EVALFULL ALL DONE
[2026-08-08 18:31:33] evalfull done: baseline -- ds4-eval: 68/92 passed, 24 failed, runtime 03h:07m
[2026-08-08 18:33:41] streaming probe: mxfp4_default
[2026-08-08 18:35:33] done: mxfp4_default -- 8192,2048,115.66,128,13.38,2505.415,127,18.12,0
[2026-08-08 18:35:33] streaming probe: q4_default
[2026-08-08 18:37:29] done: q4_default -- 8192,2048,113.27,128,12.10,2613.734,127,16.05,0
[2026-08-08 18:37:29] streaming probe: mxfp4_cache100g
[2026-08-08 18:39:53] done: mxfp4_cache100g -- 8192,2048,63.62,128,14.15,2926.569,127,20.93,0
[2026-08-08 18:39:53] streaming probe: q4_cache100g
[2026-08-08 18:41:49] done: q4_cache100g -- 8192,2048,92.62,128,13.41,2821.174,127,19.03,0
[2026-08-08 18:41:49] STREAMING PROBE DONE
[2026-08-08 18:42:24] ppl: mxfp4_stream
[2026-08-08 19:12:20] ppl done: mxfp4_stream -- tokens=94826 scored=32736 nll=49293.841252978 avg_nll=1.505799159 ppl=4.507754602
[2026-08-08 19:12:20] ppl: q4_stream
[2026-08-08 19:46:12] ppl done: q4_stream -- tokens=94826 scored=32736 nll=49691.873684528 avg_nll=1.517958018 ppl=4.562898321
[2026-08-08 19:46:12] STREAMING PPL DONE
[2026-08-08 19:46:41] evalfull start: mxfp4_stream
[2026-08-08 22:49:48] evalfull done: mxfp4_stream -- ds4-eval: 80/92 passed, 12 failed, runtime 03h:03m
[2026-08-08 22:49:48] EVALFULL MXFP4 DONE
[2026-08-08 23:18:05] longctx sweep: q2q4_0731
[2026-08-09 00:08:30] longctx done: q2q4_0731 (exit 0) -- last row: 262144,32768,231.59,128,19.34,211.129,127,19.97,0
[2026-08-09 00:08:30] longctx sweep: q2_0731
[2026-08-09 00:54:05] LONGCTX ALL DONE
[2026-08-09 00:54:05] longctx done: q2_0731 (exit 0) -- last row: 262144,32768,269.71,128,21.58,58.345,127,21.81,0
```
