#!/bin/sh
# Thermal follow-ups, now that powermetrics is available passwordless.
#
# Answers two questions raised during the session:
#
#  A. COLD-START RAMP. How long does a cool GPU hold boost clocks before
#     settling into the Heavy-pressure clamp (~1274-1295 MHz vs a 1620 ceiling)?
#     This is why single-shot probes read faster than sweeps, and it bounds how
#     much a "cool down first" strategy could ever buy. Idle 15 min, then sample
#     every second through the first 4 minutes of load.
#
#  B. ENERGY PER UNIT WORK vs --power. GPUs are usually more efficient at lower
#     clocks (power scales superlinearly with voltage/frequency), so capping the
#     duty cycle may cost little throughput while cutting total joules -- cooler
#     AND cheaper, paying only in latency. That would inverse the usual
#     assumption that throttling is pure loss. Integrate GPU watts over an
#     identical workload at several --power settings.
#
# Metric for B is joules per unit of work, not watts: a slower run at lower
# power can still burn more total energy.
set -u

# The ds4 engine and its weights still live in the ds4 checkout.
# Results live beside this script, in this repo.
DS4_ROOT=${DS4_ROOT:-/Users/evanhoffman/git/ds4}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT=$HERE/thermal
GGUF=$DS4_ROOT/gguf
mkdir -p "$OUT"

MODEL="$GGUF/DeepSeek-V4-Flash-Layers37-42Q4KExperts-OtherExpertLayersIQ2XXSGateUp-Q2KDown-AProjQ8-SExpQ8-OutQ8-chat-v2-imatrix-fixed-0731.gguf"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# --- A: cold-start ramp -------------------------------------------------
log "cooling for 15 minutes to reach thermal baseline"
sleep 900
log "baseline reached; sampling ramp at 1 Hz"

sudo -n powermetrics --samplers thermal,gpu_power -i 1000 -n 240 2>/dev/null \
  | awk '
    /Current pressure level:/ {p=$4}
    /GPU HW active frequency:/ {mhz=$5}
    /GPU Power:/ {print systime(), p, mhz, $3; fflush()}
  ' > "$OUT/ramp.txt" &
PMPID=$!

sleep 2
"$DS4_ROOT/ds4-bench" -m "$MODEL" \
    --prompt-file "$DS4_ROOT/speed-bench/promessi_sposi.txt" \
    --ctx-start 2048 --ctx-max 16384 --step-incr 2048 --gen-tokens 128 \
    --csv "$OUT/ramp_bench.csv" > "$OUT/ramp_bench.log" 2>&1
log "ramp workload done"
wait $PMPID 2>/dev/null
log "ramp sampling done"

# --- B: energy vs --power ----------------------------------------------
energy_run() {
    pw=$1
    log "cooldown 180s before power=$pw"
    sleep 180
    log "energy run: --power $pw"

    sudo -n powermetrics --samplers gpu_power -i 1000 -n 600 2>/dev/null \
      | awk '/GPU Power:/ {print $3; fflush()}' > "$OUT/power_${pw}.txt" &
    PM=$!

    start=$(date +%s)
    "$DS4_ROOT/ds4-bench" -m "$MODEL" \
        --prompt-file "$DS4_ROOT/speed-bench/promessi_sposi.txt" \
        --ctx-start 2048 --ctx-max 16384 --step-incr 2048 --gen-tokens 128 \
        --power "$pw" \
        --csv "$OUT/power_${pw}.csv" > "$OUT/power_${pw}.log" 2>&1
    end=$(date +%s)

    kill $PM 2>/dev/null
    # mean mW over samples taken during the run; joules = mean_W * seconds
    j=$(awk -v s=$((end-start)) '{n++; t+=$1} END {if(n) printf "%.0f", (t/n/1000)*s}' "$OUT/power_${pw}.txt")
    log "power=$pw done: ${#j} wall=$((end-start))s energy=${j}J"
    echo "$pw,$((end-start)),$j" >> "$OUT/energy_summary.csv"
}

echo "power_pct,wall_s,energy_j" > "$OUT/energy_summary.csv"
energy_run 100
energy_run 85
energy_run 70
energy_run 50

log "THERMAL EXPERIMENTS DONE"
