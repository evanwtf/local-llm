#!/bin/sh
# Issue #4: GLM 5.2 on a single 128 GiB machine.
#
# GLM-5.2-UD-IQ2_XXS is 196.6 GiB against 128 GiB of RAM -- it streams far
# harder than the 145-153 GiB DeepSeek 4-bit models did (those got 45-59% of
# resident generation). Expectations should be low: the README's GLM-on-128GB
# material targets TWO MacBooks (188 GiB split across a pair).
#
# Staged like #3 so an unusable result costs minutes, not hours:
#   1. viability probe (+ GLM-specific streaming tuning)
#   2. perplexity, if the probe is not hopeless
#   3. full eval, only if quality justifies it  <- run separately
#
# GLM has streaming controls DeepSeek does not use the same way:
#   --ssd-streaming-full-layers N   keep first N routed layers fully resident
#   --ssd-streaming-preload-experts N   GLM demand-fills unless set explicitly
# Defaults are unlikely to be right, so a bad default number is not GLM's verdict.
set -u

# The ds4 engine and its weights still live in the ds4 checkout.
# Results live beside this script, in this repo.
DS4_ROOT=${DS4_ROOT:-/Users/evanhoffman/git/ds4}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT=$HERE/glm
GGUF=$DS4_ROOT/gguf
mkdir -p "$OUT"

GLM="$GGUF/GLM-5.2-UD-IQ2_XXS_RoutedIQ2XXS_blk78Q2K.gguf"
GLM_SIZE=211075856448
BASELINE="$GGUF/DeepSeek-V4-Flash-IQ2XXS-w2Q2K-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "waiting for GLM download"
while :; do
    if [ -f "$GLM" ] && [ "$(stat -f %z "$GLM" 2>/dev/null)" = "$GLM_SIZE" ] \
       && ! pgrep -f "download_model.sh" >/dev/null; then break; fi
    sleep 30
done
log "GLM download complete ($(stat -f %z "$GLM") bytes)"

# download_model.sh repoints ds4flash.gguf; put it back immediately.
ln -sfn "$BASELINE" "$DS4_ROOT/ds4flash.gguf"
log "ds4flash.gguf restored to DeepSeek baseline"

probe() {
    tag=$1; shift
    log "glm probe: $tag"
    "$DS4_ROOT/ds4-bench" -m "$GLM" \
        --prompt-file "$DS4_ROOT/speed-bench/promessi_sposi.txt" \
        --ctx-start 2048 --ctx-max 8192 --step-incr 2048 --gen-tokens 128 \
        --ssd-streaming "$@" \
        --csv "$OUT/$tag.csv" > "$OUT/$tag.log" 2>&1
    log "glm probe done: $tag (exit $?) -- $(tail -1 "$OUT/$tag.csv" 2>/dev/null)"
}

probe default
probe full8      --ssd-streaming-full-layers 8
probe preload256 --ssd-streaming-preload-experts 256
probe cache80g   --ssd-streaming-cache-experts 80GB

log "GLM PROBE DONE"
