#!/bin/sh
# Issue #3, stage 2: quality signal before committing to a ~5h streamed eval.
# Same held-out slice and method as REPORT.md section 2, so numbers compare
# directly. Baseline to beat: mixed q2/q4 resident = 5.9082.
set -u
# The ds4 engine and its weights still live in the ds4 checkout.
# Results live beside this script, in this repo.
DS4_ROOT=${DS4_ROOT:-/Users/evanhoffman/git/ds4}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT=$HERE
GGUF=$DS4_ROOT/gguf
MXFP4="$GGUF/DeepSeek-V4-Flash-MXFP4Experts-F16HC-F16Compressor-F16Indexer-Q8Attn-Q8Shared-Q8Out-chat-v2-mxfp4-0731.gguf"
Q4="$GGUF/DeepSeek-V4-Flash-Q4KExperts-F16HC-F16Compressor-F16Indexer-Q8Attn-Q8Shared-Q8Out-chat-v2-imatrix-0731.gguf"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Same held-out slice as run_bench.sh: the LAST 300 KB of promessi_sposi.txt,
# disjoint from the speed-sweep prompt. Use tail, not head.
[ -f "$OUT/ppl_heldout.txt" ] || \
    tail -c 300000 "$DS4_ROOT/speed-bench/promessi_sposi.txt" > "$OUT/ppl_heldout.txt"

ppl() {
    model=$1; tag=$2
    log "ppl: $tag"
    "$DS4_ROOT/ds4" -m "$model" --ssd-streaming --perplexity-file "$OUT/ppl_heldout.txt" \
        > "$OUT/ppl_$tag.log" 2>&1
    log "ppl done: $tag -- $(tr '\r' '\n' < "$OUT/ppl_$tag.log" | tail -1)"
}
ppl "$MXFP4" mxfp4_stream
ppl "$Q4"    q4_stream
log "STREAMING PPL DONE"
