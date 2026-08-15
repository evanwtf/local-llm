#!/bin/sh
# Issue #2: prefill measured ~25% below the README's M5 Max figure (790 t/s @2048).
#
# The full sweep runs short contexts first and long contexts last, so "later
# frontier" and "hotter laptop" are the same axis -- a thermal confound. The
# deficit vanishing by 64k (and generation INVERTING in our favour there) fits
# throttling better than a missing flag, which would cost uniformly.
#
# Each config below measures ONLY the 2048 frontier, from a cold-ish start,
# with a cooldown between runs. That removes the confound: if a cold 2048 run
# hits ~790 t/s, the sweep numbers are a thermal artifact and no flag is
# missing.
set -u

ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731/prefill_probe
GGUF=$ROOT/gguf
mkdir -p "$OUT"

Q2="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-0731.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

probe() {
    tag=$1
    shift
    log "cooldown 90s before $tag"
    sleep 90
    log "probe: $tag"
    "$ROOT/ds4-bench" -m "$Q2" \
        --prompt-file "$ROOT/speed-bench/promessi_sposi.txt" \
        --ctx-start 2048 --ctx-max 2048 --gen-tokens 128 \
        --csv "$OUT/$tag.csv" "$@" > "$OUT/$tag.log" 2>&1
    log "probe done: $tag"
}

probe cold_default
probe cold_warm_weights --warm-weights
probe cold_chunk8192    --prefill-chunk 8192
probe cold_chunk2048    --prefill-chunk 2048
probe cold_quality      --quality

# Control: immediately re-run the default with no cooldown, straight after a
# hot run. If this lands well below cold_default, thermal state is confirmed as
# the dominant variable.
log "probe: hot_default (no cooldown)"
"$ROOT/ds4-bench" -m "$Q2" \
    --prompt-file "$ROOT/speed-bench/promessi_sposi.txt" \
    --ctx-start 2048 --ctx-max 2048 --gen-tokens 128 \
    --csv "$OUT/hot_default.csv" > "$OUT/hot_default.log" 2>&1
log "probe done: hot_default"

log "PREFILL PROBE DONE"
