#!/bin/sh
# Second eval pass with a generous token budget so reasoning chains finish.
# The first pass capped at 3000 tokens and truncated 4/4/1 runs respectively,
# which conflates "ran out of budget" with "got it wrong".
set -u

# The ds4 engine and its weights still live in the ds4 checkout.
# Results live beside this script, in this repo.
DS4_ROOT=${DS4_ROOT:-/Users/evanhoffman/git/ds4}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT=$HERE
GGUF=$DS4_ROOT/gguf

BASELINE="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix.gguf"
Q2_0731="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf"
Q2Q4_0731="$GGUF/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

evalrun() {
    model=$1; tag=$2
    log "eval8k: $tag"
    "$DS4_ROOT/ds4-eval" -m "$model" --questions 15 -n 8000 \
        --trace "$OUT/eval8k_$tag.trace" > "$OUT/eval8k_$tag.log" 2>&1
    log "eval8k done: $tag (exit $?)"
}

evalrun "$BASELINE"  "baseline"
evalrun "$Q2_0731"   "q2_0731"
evalrun "$Q2Q4_0731" "q2q4_0731"

ln -sfn "$BASELINE" "$DS4_ROOT/ds4flash.gguf"
log "EVAL8K ALL DONE"
