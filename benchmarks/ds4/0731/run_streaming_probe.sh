#!/bin/sh
# Issue #3, stage 1: is SSD streaming even viable on this machine?
#
# MXFP4 (145.3 GiB) and Q4 (153.3 GiB) both exceed 128 GiB, so they need
# --ssd-streaming. Before spending ~5-8h per model on a full 92-question eval,
# find out what generation speed streaming actually delivers. If it collapses
# to single digits, the eval is not worth running and the answer to #3 is "no".
#
# Baseline to beat: resident mixed q2/q4 at ~32 t/s generation, 76/92 eval.
set -u

ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731/streaming
GGUF=$ROOT/gguf
mkdir -p "$OUT"

MXFP4="$GGUF/DeepSeek-V4-Flash-MXFP4Experts-F16HC-F16Compressor-F16Indexer-Q8Attn-Q8Shared-Q8Out-chat-v2-mxfp4-0731.gguf"
Q4="$GGUF/DeepSeek-V4-Flash-Q4KExperts-F16HC-F16Compressor-F16Indexer-Q8Attn-Q8Shared-Q8Out-chat-v2-imatrix-0731.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Short sweep only -- enough to see the shape of streaming performance without
# committing hours.
probe() {
    model=$1; tag=$2
    shift 2
    log "streaming probe: $tag"
    "$ROOT/ds4-bench" -m "$model" \
        --prompt-file "$ROOT/speed-bench/promessi_sposi.txt" \
        --ctx-start 2048 --ctx-max 8192 --step-incr 2048 --gen-tokens 128 \
        --ssd-streaming "$@" \
        --csv "$OUT/$tag.csv" > "$OUT/$tag.log" 2>&1
    log "done: $tag -- $(tail -1 "$OUT/$tag.csv" 2>/dev/null)"
}

probe "$MXFP4" mxfp4_default
probe "$Q4"    q4_default

# The docs say the default expert cache is 80% of working set minus non-routed
# weights. Worth one variant with a larger explicit routed budget.
probe "$MXFP4" mxfp4_cache100g --ssd-streaming-cache-experts 100GB
probe "$Q4"    q4_cache100g    --ssd-streaming-cache-experts 100GB

log "STREAMING PROBE DONE"
