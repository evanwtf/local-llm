#!/bin/sh
# Issue #3, stage 3: full 92-question eval on streamed MXFP4.
#
# Justified by perplexity 4.5078 vs 5.9082 for the resident mixed q2/q4 build
# -- a 23.7% gain, vs the 6.8% gain that was worth +8 questions earlier.
#
# MXFP4 chosen over Q4: better perplexity (4.5078 vs 4.5629), faster
# (20.93 vs 19.03 steady gen), and 8 GiB smaller.
#
# Uses --ssd-streaming-cache-experts 100GB: the eval is decode-dominated (short
# prompts, long reasoning chains), and the larger expert cache buys +16%
# generation at the cost of prefill, which barely matters here.
#
# Target to beat: resident mixed q2/q4 = 76/92 in 2h20m.
set -u
ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731
GGUF=$ROOT/gguf
MXFP4="$GGUF/DeepSeek-V4-Flash-MXFP4Experts-F16HC-F16Compressor-F16Indexer-Q8Attn-Q8Shared-Q8Out-chat-v2-mxfp4-0731.gguf"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
log "evalfull start: mxfp4_stream"
"$ROOT/ds4-eval" -m "$MXFP4" -n 8000 \
    --ssd-streaming --ssd-streaming-cache-experts 100GB \
    --trace "$OUT/evalfull_mxfp4_stream.trace" > "$OUT/evalfull_mxfp4_stream.log" 2>&1
log "evalfull done: mxfp4_stream -- $(grep -o 'ds4-eval: .*passed.*' "$OUT/evalfull_mxfp4_stream.log" | tail -1)"
log "EVALFULL MXFP4 DONE"
