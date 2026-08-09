#!/bin/sh
# Real thermal telemetry via powermetrics (passwordless sudoers rule installed
# 2026-08-08 20:20).
#
# Supersedes thermal_watch.sh, whose no-sudo proxies were misleading: pmset
# recorded no warnings and throughput was flat, which I read as "no throttling".
# powermetrics shows thermal pressure "Heavy" and the GPU clamped ~21% below its
# 1620 MHz ceiling while requesting max P-state 100% of the time. It throttles
# fast, then holds steady -- indistinguishable from not throttling unless you
# measure clocks directly.
#
# Columns:
#   pressure      - Nominal / Moderate / Heavy / Trapping / Sleeping
#   gpu_mhz       - GPU HW active frequency (ceiling on this machine: 1620)
#   gpu_mw        - GPU power draw
#   gpu_active    - GPU HW active residency %
#   tok_per_s     - sustained generation rate from the live eval log
set -u

ROOT=/Users/evanhoffman/git/ds4
OUT=$ROOT/bench-0731/thermal_watch2.csv

[ -f "$OUT" ] || echo "timestamp,pressure,gpu_mhz,gpu_mw,gpu_active_pct,tok_per_s,questions_done" > "$OUT"

while :; do
    ts=$(date '+%Y-%m-%d %H:%M:%S')

    pm=$(sudo -n powermetrics --samplers thermal,gpu_power -i 400 -n 1 2>/dev/null)
    pressure=$(printf '%s' "$pm" | grep -i "Current pressure level:" | awk -F: '{gsub(/ /,"",$2); print $2}')
    gpu_mhz=$(printf '%s' "$pm" | grep -i "GPU HW active frequency:" | awk '{print $5}')
    gpu_mw=$(printf '%s'  "$pm" | grep -i "GPU Power:" | awk '{print $3}')
    gpu_act=$(printf '%s' "$pm" | grep -i "GPU HW active residency:" | awk '{print $5}' | tr -d '%')

    live=$(ls -t "$ROOT"/bench-0731/evalfull_*.log 2>/dev/null | grep -v orchestrator | head -1)
    if [ -n "${live:-}" ]; then
        rate=$(grep -oE '\([0-9.]+s, [0-9]+ tokens\)' "$live" 2>/dev/null | tail -5 | \
            awk -F'[(,s ]+' '{sec+=$2; tok+=$3} END {if (sec>0) printf "%.2f", tok/sec; else print "na"}')
        done_n=$(grep -cE "^(PASSED|FAILED)" "$live" 2>/dev/null || echo 0)
    else
        rate="na"; done_n=0
    fi

    echo "$ts,${pressure:-na},${gpu_mhz:-na},${gpu_mw:-na},${gpu_act:-na},${rate:-na},$done_n" >> "$OUT"
    sleep 120
done
