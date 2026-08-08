#!/bin/sh
# Issue #1: full eval sweep. The 15/15 recommendation rests on 15 questions;
# the embedded set is ~86. Ordered so the recommended model reports first, in
# case the run is interrupted.
#
# -n 8000 matches the primary pass in REPORT.md. Do NOT use 3000: at that cap
# the ranking inverted purely from truncation.
#
# No --warm-weights: keeps conditions identical to the 15-question runs these
# numbers are compared against.
set -u

ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731
GGUF=$ROOT/gguf

BASELINE="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix.gguf"
Q2_0731="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf"
Q2Q4_0731="$GGUF/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

evalfull() {
    model=$1; tag=$2
    log "evalfull start: $tag"
    "$ROOT/ds4-eval" -m "$model" -n 8000 \
        --trace "$OUT/evalfull_$tag.trace" > "$OUT/evalfull_$tag.log" 2>&1
    log "evalfull done: $tag -- $(grep -o 'ds4-eval: .*passed.*' "$OUT/evalfull_$tag.log" | tail -1)"
}

evalfull "$Q2Q4_0731" "q2q4_0731"
evalfull "$Q2_0731"   "q2_0731"
evalfull "$BASELINE"  "baseline"

log "EVALFULL ALL DONE"
