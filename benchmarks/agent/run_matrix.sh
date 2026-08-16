#!/bin/sh
# Run the full matrix with only one model resident at a time.
#
# ds4 is ~91 GiB and Qwen is ~18 GB. Both fit on a 128 GiB machine, but not
# with headroom -- whichever server is idle gets paged out and pays for it on
# its first request. So: run every ds4 trial, free ds4, then run every Qwen
# trial. Neither model is ever measured while paged out.
#
#   sh run_matrix.sh [trials]      default 3
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TRIALS=${1:-3}
DS4_UP=$HERE/../ds4/0731/agent/ds4-up
LOG=$HERE/matrix.log

say() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG"; }

say "=== phase 1: ds4 ($TRIALS trials) ==="
# Unload Qwen so ds4 gets the machine to itself.
ollama stop qwen3.8:27b-mlx 2>/dev/null || true
# Restart so the weights are warm rather than paged out.
"$DS4_UP" restart >>"$LOG" 2>&1 || { say "ds4-up restart failed"; exit 1; }
"$DS4_UP" status >>"$LOG" 2>&1

python3 "$HERE/run.py" --backend ds4 --trials "$TRIALS" --timeout 2400 2>&1 | tee -a "$LOG"

say "=== freeing ds4 (~91 GiB) ==="
"$DS4_UP" stop >>"$LOG" 2>&1
sleep 5

say "=== phase 2: qwen ($TRIALS trials) ==="
# Preload so trial 1 does not pay the load cost the others avoid.
curl -s -m 300 http://127.0.0.1:11434/api/generate \
    -d '{"model":"qwen3.8:27b-mlx","prompt":"hi","stream":false,"options":{"num_predict":1}}' \
    >/dev/null 2>&1

python3 "$HERE/run.py" --backend qwen --trials "$TRIALS" --timeout 2400 2>&1 | tee -a "$LOG"

say "=== done; summarising ==="
python3 "$HERE/summarize.py" 2>&1 | tee -a "$LOG"
