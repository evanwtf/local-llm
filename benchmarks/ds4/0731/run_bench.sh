#!/bin/sh
# Benchmark DeepSeek V4 Flash: current local build vs the two 0731 quants.
# All three runs use the same binary (built from main @ b030961) and the same
# sweep grid, so differences are attributable to the weights/quant alone.
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

Q2_SIZE=86720111488
Q2Q4_SIZE=97591747456

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# --- wait for downloads -------------------------------------------------
wait_for() {
    path=$1; want=$2; name=$3
    log "waiting for $name"
    while :; do
        if [ -f "$path" ]; then
            have=$(stat -f %z "$path" 2>/dev/null || echo 0)
            [ "$have" = "$want" ] && { log "$name complete ($have bytes)"; return 0; }
        fi
        # give up if the downloader died and left no .part making progress
        sleep 30
    done
}

wait_for "$Q2_0731" "$Q2_SIZE" "ds4f-q2 0731"
wait_for "$Q2Q4_0731" "$Q2Q4_SIZE" "ds4f-q2-q4 0731"

# --- speed sweeps -------------------------------------------------------
# Grid is a superset of speed-bench/local-runs/m5_max_128gb_resident.csv (2048..32768 by
# 2048), extended to 65536 as the speed-bench README recommends.
sweep() {
    model=$1; tag=$2
    log "speed sweep: $tag"
    "$DS4_ROOT/ds4-bench" \
        -m "$model" \
        --prompt-file "$DS4_ROOT/speed-bench/promessi_sposi.txt" \
        --ctx-start 2048 \
        --ctx-max 65536 \
        --step-incr 2048 \
        --gen-tokens 128 \
        --csv "$OUT/speed_$tag.csv" > "$OUT/speed_$tag.log" 2>&1
    log "speed sweep done: $tag (exit $?)"
}

sweep "$BASELINE"  "baseline"
sweep "$Q2_0731"   "q2_0731"
sweep "$Q2Q4_0731" "q2q4_0731"

# --- perplexity (identical held-out text for all three) -----------------
# The held-out slice is the LAST 300 KB of promessi_sposi.txt. The speed sweep
# above prompts from the START of the same file, so the two do not overlap.
# Use tail, not head, or the perplexity numbers are contaminated.
[ -f "$OUT/ppl_heldout.txt" ] || \
    tail -c 300000 "$DS4_ROOT/speed-bench/promessi_sposi.txt" > "$OUT/ppl_heldout.txt"

ppl() {
    model=$1; tag=$2
    log "perplexity: $tag"
    "$DS4_ROOT/ds4" -m "$model" --perplexity-file "$OUT/ppl_heldout.txt" \
        > "$OUT/ppl_$tag.log" 2>&1
    log "perplexity done: $tag (exit $?)"
}

ppl "$BASELINE"  "baseline"
ppl "$Q2_0731"   "q2_0731"
ppl "$Q2Q4_0731" "q2q4_0731"

# --- quality eval harness ----------------------------------------------
evalrun() {
    model=$1; tag=$2
    log "eval harness: $tag"
    "$DS4_ROOT/ds4-eval" -m "$model" --questions 15 -n 3000 \
        --trace "$OUT/eval_$tag.trace" > "$OUT/eval_$tag.log" 2>&1
    log "eval done: $tag (exit $?)"
}

evalrun "$BASELINE"  "baseline"
evalrun "$Q2_0731"   "q2_0731"
evalrun "$Q2Q4_0731" "q2q4_0731"

# --- restore the symlink the downloader repointed ----------------------
ln -sfn "$BASELINE" "$DS4_ROOT/ds4flash.gguf"
log "ds4flash.gguf restored to baseline"

log "ALL DONE"
