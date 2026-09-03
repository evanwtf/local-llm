#!/bin/bash
# #112 disk-KV mechanism test (2026-09-03).
#
# Arm A without restart-between-trials was 13/13/10 (36/45); with restart it is
# 14/14/14 (42/45). Server state is the confirmed cause; which piece is not.
# The leading hypothesis is the disk KV budget: --kv-disk-space-mb 8192 is
# sized for DeepSeek (~560 MiB entries) but Qwen's KV entries are larger, so
# the default may evict every turn and re-prefill.
#
# This test raises the budget to 32768 and re-runs arm A on a SINGLE
# continuous server (no restart between trials). If the trial-3 decline
# reappears, the effect is elsewhere; if it does not, disk KV is confirmed and
# the fix is a single flag.
#
# Prerequisites (refuses if missing):
# - qwen38fnds4shim backend, qwen3.8-flash-next-q4 model on ds4-metal
# - the tool-format shim on :8101 (this script does NOT stop or start it)
# - a clean checkout of ~/git/gmail-archive and ~/git/monitor at their pinned
#   commits (run.py refuses otherwise, which is the guard we rely on)
#
# Usage:
#   scripts/disk_kv_mechanism_test.sh
#
# One run.py invocation, --trials 3, no restart between trials. ~90 minutes.

set -eu

LOGDIR="${LOGDIR:-$(mktemp -d)}"
BENCH_LOGS="${BENCH_LOGS:-$HOME/bench-logs}"

DS4_MODEL="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
DS4_PLE="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_KV="$HOME/.ds4/server-kv"
KV_MB=32768

# Refuse the whole run if the shim is not up. The upstream is otherwise
# indistinguishable from a working ds4 server, and OpenCode would talk to the
# wrong thing.
if ! pgrep -f qwen_tool_shim >/dev/null; then
    echo "REFUSING: ds4_qwen_tool_shim.py is not running on :8101" >&2
    echo "Start it first: uv run python ds4_qwen_tool_shim.py --port 8101 --upstream http://127.0.0.1:8000" >&2
    exit 1
fi

restart_ds4() {
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

    echo "[$(date +%H:%M:%S)] starting ds4-server fresh, kv-disk-space-mb=$KV_MB..."
    (cd "$HOME/git/ds4-metal" && \
        ./ds4-server --metal \
            -m "$DS4_MODEL" --ple "$DS4_PLE" \
            --ctx 100000 --warm-weights \
            --kv-disk-dir "$DS4_KV" --kv-disk-space-mb "$KV_MB" \
            --host 127.0.0.1 --port 8000 \
            > "$LOGDIR/ds4server.log" 2>&1 &)

    (cd "$(dirname "$0")/.." && \
        uv run python benchmarks/agent/wait_ready.py \
            --base-url http://127.0.0.1:8000 \
            --model qwen3.8-flash-next-q4 | tail -2)
}

echo "logs in: $LOGDIR"
restart_ds4

echo "[$(date +%H:%M:%S)] starting arm A, 3 trials, single continuous server..."
(cd "$(dirname "$0")/.." && \
    uv run python benchmarks/agent/run.py \
        --backend qwen38fnds4shim --trials 3 --client opencode \
        > "$LOGDIR/armA-kv32768.log" 2>&1)
echo "[$(date +%H:%M:%S)] run done"

mkdir -p "$BENCH_LOGS/112-kv32768"
mv "$BENCH_LOGS"/*qwen38fnds4shim-opencode-* "$BENCH_LOGS/112-kv32768/" 2>/dev/null || true
echo "[$(date +%H:%M:%S)] collected $(ls "$BENCH_LOGS/112-kv32768" | wc -l | tr -d ' ') transcripts to 112-kv32768/"

echo "[$(date +%H:%M:%S)] test complete"
echo "Compare per-trial pass rates against the arm A baselines:"
echo "  no restart, kv 8192:  13/13/10 (36/45)"
echo "  restart, kv 8192:    14/14/14 (42/45)"
echo "  no restart, kv 32768: <this run>"
