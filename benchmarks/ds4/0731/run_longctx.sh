#!/bin/sh
# Issue #5: long-context behaviour beyond 64k.
#
# Everything so far stopped at 65536. Flash trains to 1048576. This matters for
# agent use (claude_code_recommendations.md suggests --ctx 100000 without having
# validated it) and it is the one scenario where q2_0731 could beat the mixed
# build: the mixed build uses 90.9 of 128 GiB resident vs 80.8, so it has ~10 GiB
# less KV headroom.
#
# Extrapolating from the 32k sweeps (~14 KB/token) 262144 tokens should need
# ~3.7 GiB of KV -- both models should fit. Measuring rather than trusting that.
#
# No --warm-weights: keeps conditions identical to the earlier sweeps these
# numbers extend.
set -u

ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731
GGUF=$ROOT/gguf

Q2="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf"
Q2Q4="$GGUF/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

sweep() {
    model=$1; tag=$2
    log "longctx sweep: $tag"
    "$ROOT/ds4-bench" -m "$model" \
        --prompt-file "$ROOT/speed-bench/promessi_sposi.txt" \
        --ctx-start 65536 --ctx-max 262144 --step-incr 32768 --gen-tokens 128 \
        --csv "$OUT/longctx_$tag.csv" > "$OUT/longctx_$tag.log" 2>&1
    rc=$?
    log "longctx done: $tag (exit $rc) -- last row: $(tail -1 "$OUT/longctx_$tag.csv" 2>/dev/null)"
}

sweep "$Q2Q4" q2q4_0731
sweep "$Q2"   q2_0731

log "LONGCTX ALL DONE"
