#!/bin/sh
# Issue #2, round 2.
#
# Round 1 killed the thermal hypothesis (hot 721.4 vs cold 720.2 -- identical)
# but left two things unresolved:
#
#  1. ctx allocation: the sweep reports 593 t/s at the 2048 frontier while a
#     standalone 2048 run reports 720. The sweep allocates KV for its ctx-max
#     (65536) up front. If that is the cause, everyday runs pay it too, since
#     ds4 defaults to ctx=32768.
#  2. --warm-weights, --prefill-chunk 8192 and --prefill-chunk 2048 all landed
#     at ~780 despite 2048/8192 being opposite sides of the 4096 default. Three
#     flags, one number, all measured consecutively after the cold run -- page
#     cache warmth is unseparated from flag effect.
#
# This round: 3 repeats per config, INTERLEAVED (a,b,c,a,b,c,...) rather than
# grouped, so any drift over time hits every config equally instead of being
# confounded with one.
set -u

ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731/prefill_probe3
GGUF=$ROOT/gguf
mkdir -p "$OUT"

Q2="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

run() {
    tag=$1; rep=$2
    shift 2
    "$ROOT/ds4-bench" -m "$Q2" \
        --prompt-file "$ROOT/speed-bench/promessi_sposi.txt" \
        --ctx-start 2048 --ctx-max 2048 --gen-tokens 128 \
        --csv "$OUT/${tag}_r${rep}.csv" "$@" > "$OUT/${tag}_r${rep}.log" 2>&1
}

for rep in 1 2 3; do
    log "=== repeat $rep ==="

    run default        $rep
    run warmweights    $rep --warm-weights
    run chunk8192      $rep --prefill-chunk 8192
    run chunk2048      $rep --prefill-chunk 2048
    run chunk4096      $rep --prefill-chunk 4096

    # ctx allocation isolation: same 2048 frontier, but reserve KV for 32k/64k
    run alloc32k       $rep --ctx-alloc 32768
    run alloc64k       $rep --ctx-alloc 65665

    # best-guess combination
    run combo          $rep --warm-weights --prefill-chunk 2048
done

log "PREFILL PROBE3 DONE"
