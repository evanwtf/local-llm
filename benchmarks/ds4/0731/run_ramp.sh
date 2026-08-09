#!/bin/sh
# Experiment A retry: cold-start GPU clock ramp.
#
# The first attempt produced an empty file: the awk filter used systime(), a GNU
# extension absent from macOS BSD awk, so the filter died silently. Here the
# sample counter stands in for elapsed seconds (powermetrics emits one
# "GPU Power:" line per sample at 1 Hz).
#
# Question: how long does a cool GPU hold boost clocks before settling into the
# Heavy-pressure clamp (~1274-1295 MHz against a 1620 MHz ceiling)? This bounds
# what any "cool down first" strategy could buy, and explains why single-shot
# probes read faster than sweeps.
set -u

ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731/thermal
MODEL="$ROOT/gguf/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "waiting for the --power sweep to finish"
while ! grep -q "THERMAL EXPERIMENTS DONE" "$ROOT/bench-0731/thermal_experiments.log" 2>/dev/null; do
    sleep 30
done

log "cooling 15 minutes to thermal baseline"
sleep 900
log "baseline reached; sampling at 1 Hz for 300 s"

sudo -n powermetrics --samplers thermal,gpu_power -i 1000 -n 300 2>/dev/null \
  | awk '
    /Current pressure level:/ {p=$4}
    /GPU HW active frequency:/ {mhz=$5}
    /GPU Power:/ {n++; print n, p, mhz, $3; fflush()}
  ' > "$OUT/ramp2.txt" &
PM=$!

sleep 2
# Long single sweep so load is continuous for the whole sampling window.
"$ROOT/ds4-bench" -m "$MODEL" \
    --prompt-file "$ROOT/speed-bench/promessi_sposi.txt" \
    --ctx-start 2048 --ctx-max 65536 --step-incr 2048 --gen-tokens 128 \
    --csv "$OUT/ramp2_bench.csv" > "$OUT/ramp2_bench.log" 2>&1
log "ramp workload done"

wait $PM 2>/dev/null
log "RAMP DONE ($(wc -l < "$OUT/ramp2.txt" 2>/dev/null) samples)"
