#!/bin/sh
# Second eval pass with a generous token budget so reasoning chains finish.
# The first pass capped at 3000 tokens and truncated 4/4/1 runs respectively,
# which conflates "ran out of budget" with "got it wrong".
set -u

ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731
GGUF=$ROOT/gguf

BASELINE="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix.gguf"
Q2_0731="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf"
Q2Q4_0731="$GGUF/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

evalrun() {
    model=$1; tag=$2
    log "eval8k: $tag"
    "$ROOT/ds4-eval" -m "$model" --questions 15 -n 8000 \
        --trace "$OUT/eval8k_$tag.trace" > "$OUT/eval8k_$tag.log" 2>&1
    log "eval8k done: $tag (exit $?)"
}

evalrun "$BASELINE"  "baseline"
evalrun "$Q2_0731"   "q2_0731"
evalrun "$Q2Q4_0731" "q2q4_0731"

ln -sfn "$BASELINE" "$ROOT/ds4flash.gguf"
log "EVAL8K ALL DONE"
