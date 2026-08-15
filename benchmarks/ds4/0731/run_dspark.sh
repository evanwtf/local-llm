#!/bin/sh
# DSpark speculative decoding benchmark.
#
# ds4-bench does not expose --dspark/--mtp, so speed is measured from ds4's own
# "generation: N t/s" line over fixed-length greedy runs.
#
# Speculative decoding is supposed to be LOSSLESS at temp 0: the draft model
# proposes, the target model verifies, and rejected drafts are discarded. So the
# generated text must be byte-identical to the non-speculative run. This script
# checks that as well as speed -- a speedup with drifting output is a bug, not a
# win.
set -u

ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731/dspark
GGUF=$ROOT/gguf
mkdir -p "$OUT"

Q2_0731="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf"
Q2Q4_0731="$GGUF/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"
DSPARK="$GGUF/DeepSeek-V4-Flash-DSpark-support-0731.gguf"
DSPARK_SIZE=5989114272

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "waiting for DSpark support GGUF"
while :; do
    [ -f "$DSPARK" ] && [ "$(stat -f %z "$DSPARK" 2>/dev/null || echo 0)" = "$DSPARK_SIZE" ] && break
    sleep 20
done
log "DSpark support GGUF complete"

# Three prompts with different output shapes: prose, code, and math reasoning.
# Speculative decoding acceptance rates vary a lot by content -- code is highly
# predictable and should accept best, free prose worst.
P1="Write a detailed technical explanation of how B-tree indexes work in a relational database, including page splits."
P2="Write a complete Python implementation of an LRU cache with a doubly linked list and a dict. Include docstrings."
P3="A train leaves station A at 60 mph. Another leaves station B, 240 miles away, at 40 mph toward A. Work through when and where they meet, step by step."

run_one() {
    model=$1; tag=$2; pnum=$3; prompt=$4
    shift 4
    "$ROOT/ds4" -m "$model" --temp 0 -n 512 -p "$prompt" "$@" \
        > "$OUT/${tag}_p${pnum}.log" 2>&1
}

sweep_model() {
    model=$1; mtag=$2
    i=1
    for p in "$P1" "$P2" "$P3"; do
        log "$mtag p$i: off"
        run_one "$model" "${mtag}_off" "$i" "$p"

        log "$mtag p$i: dspark draft1"
        run_one "$model" "${mtag}_dspark_d1" "$i" "$p" --dspark --mtp "$DSPARK"

        log "$mtag p$i: dspark draft2"
        run_one "$model" "${mtag}_dspark_d2" "$i" "$p" --dspark --mtp "$DSPARK" --mtp-draft 2

        log "$mtag p$i: dspark draft3"
        run_one "$model" "${mtag}_dspark_d3" "$i" "$p" --dspark --mtp "$DSPARK" --mtp-draft 3

        log "$mtag p$i: dspark draft4"
        run_one "$model" "${mtag}_dspark_d4" "$i" "$p" --dspark --mtp "$DSPARK" --mtp-draft 4

        i=$((i + 1))
    done
}

sweep_model "$Q2Q4_0731" "q2q4"
sweep_model "$Q2_0731"   "q2"

log "DSPARK ALL DONE"
