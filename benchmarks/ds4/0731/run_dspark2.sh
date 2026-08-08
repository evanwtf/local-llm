#!/bin/sh
# DSpark benchmark, corrected.
#
# First attempt swept --mtp-draft, which belongs to the legacy one-stage MTP
# path that DSpark REPLACES -- results were flat across depth because the flag
# was inert. Per README, DSpark proposes fixed blocks of up to 5 tokens and the
# real knob is --dspark-confidence (default 0.7, 0 = force full 5-token blocks).
#
# Also measures --dspark-strict, which loads DSpark but keeps target-only
# decode: the difference between "off" and "strict" isolates the fixed cost of
# hidden-state capture from the speculation itself.
set -u

ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731/dspark2
GGUF=$ROOT/gguf
mkdir -p "$OUT"

Q2Q4="$GGUF/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"
DSPARK="$GGUF/DeepSeek-V4-Flash-DSpark-support-0731.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Longer, highly predictable code prompt: README says code benefits most, and
# 512 tokens of boilerplate is the best case for block acceptance.
P1="Write a detailed technical explanation of how B-tree indexes work in a relational database, including page splits."
P2="Write a complete Python implementation of an LRU cache with a doubly linked list and a dict. Include docstrings and type hints for every method."
P3="Write a Python dataclass module with 8 dataclasses modelling a blog: User, Post, Comment, Tag, Category, Media, Session, Notification. Give every field a type hint and a default where sensible."

run_one() {
    tag=$1; pnum=$2; prompt=$3
    shift 3
    "$ROOT/ds4" -m "$Q2Q4" --temp 0 -n 512 -p "$prompt" "$@" \
        > "$OUT/${tag}_p${pnum}.log" 2>&1
}

i=1
for p in "$P1" "$P2" "$P3"; do
    log "p$i: off"
    run_one "off" "$i" "$p"

    log "p$i: strict (capture cost only)"
    run_one "strict" "$i" "$p" --dspark-strict --mtp "$DSPARK"

    log "p$i: conf default 0.7"
    run_one "conf07" "$i" "$p" --dspark --mtp "$DSPARK"

    log "p$i: conf 0.5"
    run_one "conf05" "$i" "$p" --dspark --mtp "$DSPARK" --dspark-confidence 0.5

    log "p$i: conf 0.3"
    run_one "conf03" "$i" "$p" --dspark --mtp "$DSPARK" --dspark-confidence 0.3

    log "p$i: conf 0 (forced 5-token blocks)"
    run_one "conf00" "$i" "$p" --dspark --mtp "$DSPARK" --dspark-confidence 0

    i=$((i + 1))
done

log "DSPARK2 ALL DONE"
