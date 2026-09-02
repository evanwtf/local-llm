#!/bin/sh
# Thermal record for the HumanEval runs. Same columns as ../0731/thermal_watch2.sh
# so the two are directly comparable, but it tracks progress from the generation
# log instead of the eval log.
#
# Usage: thermal.sh <label> <generation-log>
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LABEL="${1:-run}"
GENLOG="${2:-}"
OUT="$HERE/thermal_${LABEL}.csv"

[ -f "$OUT" ] || echo "timestamp,pressure,gpu_mhz,gpu_mw,gpu_active_pct,problems_done" > "$OUT"

while :; do
    ts=$(date '+%Y-%m-%d %H:%M:%S')

    pm=$(sudo -n powermetrics --samplers thermal,gpu_power -i 400 -n 1 2>/dev/null)
    pressure=$(printf '%s' "$pm" | grep -i "Current pressure level:" | awk -F: '{gsub(/ /,"",$2); print $2}')
    gpu_mhz=$(printf '%s' "$pm" | grep -i "GPU HW active frequency:" | awk '{print $5}')
    gpu_mw=$(printf '%s' "$pm" | grep -i "GPU Power:" | awk '{print $3}')
    gpu_act=$(printf '%s' "$pm" | grep -i "GPU HW active residency:" | awk '{print $5}')

    done_n=0
    if [ -n "$GENLOG" ] && [ -f "$GENLOG" ]; then
        done_n=$(grep -c "tok  finish=" "$GENLOG" 2>/dev/null)
    fi

    echo "$ts,${pressure:-?},${gpu_mhz:-?},${gpu_mw:-?},${gpu_act:-?},$done_n" >> "$OUT"
    sleep 60
done
