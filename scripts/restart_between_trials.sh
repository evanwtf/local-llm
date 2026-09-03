#!/bin/bash
# Restart-between-trials experiment for #112.
#
# Both arms of #77 degrade monotonically across their session -- A 13/13/10 and
# B 10/9/6 -- with a fresh conversation each trial. That rules out model
# context. The candidates are server-side state and machine state, and this
# script separates them by restarting the model server between trials.
#
# Design:
# - Three run.py invocations, --trials 1 each, restarting ds4-server between.
# - Same argv as the original arm A (MTP off, ~/.ds4/server-kv).
# - Each run's transcripts move to ~/bench-logs/112-run{1,2,3}/ so the next
#   run does not clobber ~/bench-logs/<task>-<backend>-opencode-1.jsonl.
# - Rows all carry trial=1 in results.jsonl; distinguish them by started time.
#
# Prerequisites, and it refuses if any is missing:
# - qwen38fnds4shim backend, qwen3.8-flash-next-q4 model on ds4-metal
# - the tool-format shim on :8101 (this script does NOT stop or start it)
# - a clean checkout of ~/git/gmail-archive and ~/git/monitor at their pinned
#   commits (run.py refuses otherwise, which is the guard we rely on)
#
# Usage:
#   scripts/restart_between_trials.sh
#
# The script waits for each run to finish (recognises `restored monitor`) before
# starting the next. About 90-120 minutes end-to-end.

set -eu

LOGDIR="${LOGDIR:-$(mktemp -d)}"
BENCH_LOGS="${BENCH_LOGS:-$HOME/bench-logs}"

DS4_MODEL="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
DS4_PLE="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_KV="$HOME/.ds4/server-kv"

# Refuse the whole run if the shim is not up. The upstream is otherwise
# indistinguishable from a working ds4 server, and OpenCode would talk to the
# wrong thing.
if ! pgrep -f qwen_tool_shim >/dev/null; then
    echo "REFUSING: ds4_qwen_tool_shim.py is not running on :8101" >&2
    echo "Start it first: uv run python ds4_qwen_tool_shim.py --port 8101 --upstream http://127.0.0.1:8000" >&2
    exit 1
fi

restart_ds4() {
    local tag="$1"
    echo "[$(date +%H:%M:%S)] stopping ds4-server..."
    pkill -f 'ds4-server --metal' 2>/dev/null || true
    sleep 3
    if pgrep -f 'ds4-server --metal' >/dev/null; then
        pkill -9 -f 'ds4-server --metal'
        sleep 2
    fi
    if pgrep -f 'ds4-server --metal' >/dev/null; then
        echo "REFUSING: ds4-server would not stop" >&2
        exit 1
    fi

    echo "[$(date +%H:%M:%S)] starting ds4-server fresh for $tag..."
    (cd "$HOME/git/ds4-metal" && \
        ./ds4-server --metal \
            -m "$DS4_MODEL" --ple "$DS4_PLE" \
            --ctx 100000 --warm-weights \
            --kv-disk-dir "$DS4_KV" --kv-disk-space-mb 8192 \
            --host 127.0.0.1 --port 8000 \
            > "$LOGDIR/ds4server-$tag.log" 2>&1 &)

    (cd "$(dirname "$0")/.." && \
        uv run python benchmarks/agent/wait_ready.py \
            --base-url http://127.0.0.1:8000 \
            --model qwen3.8-flash-next-q4 | tail -2)
}

collect_transcripts() {
    local n="$1"
    mkdir -p "$BENCH_LOGS/112-run$n"
    # `mv` with no matching glob is not fatal here -- if the trial produced no
    # transcripts (a --no-client-log run, say) there is nothing to move and it
    # is a valid state, not a failure.
    mv "$BENCH_LOGS"/*qwen38fnds4shim-opencode-1* "$BENCH_LOGS/112-run$n/" 2>/dev/null || true
    echo "[$(date +%H:%M:%S)] collected $(ls "$BENCH_LOGS/112-run$n" | wc -l | tr -d ' ') transcripts to 112-run$n/"
}

run_trial() {
    local n="$1"
    echo "[$(date +%H:%M:%S)] starting run $n..."
    (cd "$(dirname "$0")/.." && \
        uv run python benchmarks/agent/run.py \
            --backend qwen38fnds4shim --trials 1 --client opencode \
            > "$LOGDIR/armA-restart-run$n.log" 2>&1)
    echo "[$(date +%H:%M:%S)] run $n done"
    collect_transcripts "$n"
}

echo "logs in: $LOGDIR"
restart_ds4 trial1; run_trial 1
restart_ds4 trial2; run_trial 2
restart_ds4 trial3; run_trial 3

echo "[$(date +%H:%M:%S)] cycle complete"
echo "Compare per-trial pass rates against the arm A 13/13/10 baseline"
echo "  (original run without restarts between trials)."
