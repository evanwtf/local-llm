#!/bin/sh
# Second series: the two Qwen3.6 builds, against the Qwen3.8 numbers already
# in RESULTS.md.
#
# Backends are run one after the other, not interleaved. run.py loops
# backend-innermost, so passing both at once would swap a 19 GB and a 31 GB
# model in and out on every single trial. Sequential runs pay the load cost
# once per phase instead of once per run.
#
#   sh run_qwen36.sh [trials]      default 3
set -u

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TRIALS=${1:-3}
LOG=$HERE/matrix36.log

say() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*" | tee -a "$LOG"; }

for backend in qwen36 qwen36coding; do
    say "=== phase: $backend ($TRIALS trials) ==="
    # Preload so trial 1 does not pay a load cost the others avoid.
    model=$(python3 -c "
import tomllib,pathlib
cfg = tomllib.loads(pathlib.Path('$HERE/tasks.toml').read_text())
print(cfg['backend']['$backend']['model'])")
    say "preloading $model"
    curl -s -m 900 http://127.0.0.1:11434/api/generate \
        -d "{\"model\":\"$model\",\"prompt\":\"hi\",\"stream\":false,\"options\":{\"num_predict\":1}}" \
        >/dev/null 2>&1

    python3 "$HERE/run.py" --backend "$backend" --trials "$TRIALS" --timeout 2400 2>&1 | tee -a "$LOG"

    say "unloading $model"
    ollama stop "$model" >/dev/null 2>&1 || true
    sleep 5
done

say "=== done ==="
python3 "$HERE/summarize.py" 2>&1 | tee -a "$LOG"
