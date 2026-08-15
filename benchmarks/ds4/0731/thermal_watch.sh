#!/bin/sh
# Thermal record without sudo.
#
# powermetrics needs root, so absolute temperatures are unavailable. These are
# the two signals reachable as a normal user:
#
#  1. pmset -g therm -- macOS records a thermal/performance warning level if
#     the system ever applies thermal limiting. "No ... has been recorded"
#     means it never happened.
#  2. Sustained throughput -- the symptom that actually matters. Throttling
#     shows up as tokens/sec decaying over hours at constant workload. The eval
#     logs per-question duration and token count, so a trend there is direct
#     evidence, independent of any temperature reading.
set -u

# The ds4 engine and its weights still live in the ds4 checkout.
# Results live beside this script, in this repo.
DS4_ROOT=${DS4_ROOT:-/Users/evanhoffman/git/ds4}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
OUT=$OUT/thermal_watch.csv

[ -f "$OUT" ] || echo "timestamp,thermal_warning,perf_warning,recent_q_tokens_per_s,questions_done" > "$OUT"

while :; do
    ts=$(date '+%Y-%m-%d %H:%M:%S')

    therm=$(pmset -g therm 2>/dev/null | grep -ci "thermal warning level has been recorded" || echo "?")
    [ "$therm" = "1" ] && therm="none" || therm="RECORDED"

    perf=$(pmset -g therm 2>/dev/null | grep -ci "performance warning level has been recorded" || echo "?")
    [ "$perf" = "1" ] && perf="none" || perf="RECORDED"

    # Most recent graded question: "(83.8s, 3000 tokens)" -> tokens/sec
    live=$(ls -t "$OUT"/evalfull_*.log 2>/dev/null | grep -v orchestrator | head -1)
    if [ -n "${live:-}" ]; then
        rate=$(grep -oE '\([0-9.]+s, [0-9]+ tokens\)' "$live" 2>/dev/null | tail -5 | \
            awk -F'[(,s ]+' '{sec+=$2; tok+=$3} END {if (sec>0) printf "%.2f", tok/sec; else print "na"}')
        done_n=$(grep -cE "^(PASSED|FAILED)" "$live" 2>/dev/null || echo 0)
    else
        rate="na"; done_n=0
    fi

    echo "$ts,$therm,$perf,${rate:-na},$done_n" >> "$OUT"
    sleep 120
done
