#!/bin/sh
# Issue #3, stage 2: quality signal before committing to a ~5h streamed eval.
# Same held-out slice and method as REPORT.md section 2, so numbers compare
# directly. Baseline to beat: mixed q2/q4 resident = 5.9082.
set -u
ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731
GGUF=$ROOT/gguf
MXFP4="$GGUF/DeepSeek-V4-Flash-MXFP4Experts-F16HC-F16Compressor-F16Indexer-Q8Attn-Q8Shared-Q8Out-chat-v2-mxfp4-0731.gguf"
Q4="$GGUF/DeepSeek-V4-Flash-Q4KExperts-F16HC-F16Compressor-F16Indexer-Q8Attn-Q8Shared-Q8Out-chat-v2-imatrix-0731.gguf"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
ppl() {
    model=$1; tag=$2
    log "ppl: $tag"
    "$ROOT/ds4" -m "$model" --ssd-streaming --perplexity-file "$OUT/ppl_heldout.txt" \
        > "$OUT/ppl_$tag.log" 2>&1
    log "ppl done: $tag -- $(tr '\r' '\n' < "$OUT/ppl_$tag.log" | tail -1)"
}
ppl "$MXFP4" mxfp4_stream
ppl "$Q4"    q4_stream
log "STREAMING PPL DONE"
