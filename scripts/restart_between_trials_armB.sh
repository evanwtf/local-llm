#!/bin/bash
# #77 arm B re-run under restart-between-trials (2026-09-03).
#
# Arm B (MTP on) measured 25/45 (10/9/6) on a single continuous server, but
# that was MTP-plus-server-state: the trial-3 collapse was worse than arm A's
# in the same cycle. Under restart the arm A baseline is 42/45 (14/14/14); a
# clean arm B against that decides whether MTP helps our workload.
#
# This is restart_between_trials.sh with the arm B engine configuration:
# MTP on (--mtp-draft 7 --mtp-timing) and the dedicated MTP KV directory
# (~/.ds4/server-kv-mtp). The two KV dirs are not interchangeable -- ds4
# rejects the other's checkpoints -- so arm B must use its own.
#
# Prerequisites (refuses if missing):
# - qwen38fnds4mtp7shim backend, qwen3.8-flash-next-q4 model on ds4-metal
# - the tool-format shim on :8101 (this script does NOT stop or start it)
# - a clean checkout of ~/git/gmail-archive and ~/git/monitor at their pinned
#   commits (run.py refuses otherwise, which is the guard we rely on)
#
# Usage:
#   scripts/restart_between_trials_armB.sh
#
# Three run.py invocations, --trials 1 each, restarting ds4-server between.
# ~90-120 minutes end-to-end.

set -eu

LOGDIR="${LOGDIR:-$(mktemp -d)}"
BENCH_LOGS="${BENCH_LOGS:-$HOME/bench-logs}"

DS4_MODEL="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
DS4_PLE="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_MTP="$HOME/models/qwen3.8-flash-next-ds4-q4/qwen3.8-flash-next-q4-mtp.gguf"
DS4_KV="$HOME/.ds4/server-kv-mtp"

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

    echo "[$(date +%H:%M:%S)] starting ds4-server fresh for $tag (MTP on)..."
    (cd "$HOME/git/ds4-metal" && \
        ./ds4-server --metal \
            -m "$DS4_MODEL" --ple "$DS4_PLE" \
            --ctx 100000 --warm-weights \
            --kv-disk-dir "$DS4_KV" --kv-disk-space-mb 8192 \
            --mtp-model "$DS4_MTP" --mtp-draft 7 --mtp-timing \
            --host 127.0.0.1 --port 8000 \
            > "$LOGDIR/ds4server-$tag.log" 2>&1 &)

    (cd "$(dirname "$0")/.." && \
        uv run python benchmarks/agent/wait_ready.py \
            --base-url http://127.0.0.1:8000 \
            --model qwen3.8-flash-next-q4 | tail -2)
}

collect_transcripts() {
    local n="$1"
    mkdir -p "$BENCH_LOGS/77-armB-run$n"
    # `mv` with no matching glob is not fatal here -- if the trial produced no
    # transcripts (a --no-client-log run, say) there is nothing to move and it
    # is a valid state, not a failure.
    mv "$BENCH_LOGS"/*qwen38fnds4mtp7shim-opencode-1* "$BENCH_LOGS/77-armB-run$n/" 2>/dev/null || true
    echo "[$(date +%H:%M:%S)] collected $(ls "$BENCH_LOGS/77-armB-run$n" | wc -l | tr -d ' ') transcripts to 77-armB-run$n/"
}

run_trial() {
    local n="$1"
    echo "[$(date +%H:%M:%S)] starting run $n..."
    (cd "$(dirname "$0")/.." && \
        uv run python benchmarks/agent/run.py \
            --backend qwen38fnds4mtp7shim --trials 1 --client opencode --no-lock \
            > "$LOGDIR/armB-restart-run$n.log" 2>&1)
    echo "[$(date +%H:%M:%S)] run $n done"
    collect_transcripts "$n"
}

# #133: hold the lock across the whole cycle, not per run.py call. The
# window this lock exists for is precisely the gap BETWEEN these runs, where
# ds4-server is deliberately down and a process scan truthfully reports "all
# clear" while the machine is committed for hours. run.py inside is told
# --no-lock because this script already holds it.
PREFLIGHT="$(dirname "$0")/../benchmarks/agent/preflight.py"
if ! uv run python "$PREFLIGHT" --acquire-lock "restart_between_trials_armB.sh (#77 arm B, 3 cycles)" --owner-pid $$; then
  echo "refusing to start: the machine is claimed by another run" >&2
  exit 1
fi
trap 'uv run python "$PREFLIGHT" --release-lock --owner-pid $$ >/dev/null 2>&1' EXIT

echo "logs in: $LOGDIR"
restart_ds4 trial1; run_trial 1
restart_ds4 trial2; run_trial 2
restart_ds4 trial3; run_trial 3

echo "[$(date +%H:%M:%S)] cycle complete"

# #64: the server logs a line every time the live KV prefix misses, and a
# stalled prefix costs re-prefill on every turn -- 443,974 tokens across the
# four logs we happened to keep. Audit them here, while the logs are in hand,
# rather than hoping someone runs it later on a log that has been cleaned up.
if ls "$LOGDIR"/ds4server-*.log >/dev/null 2>&1; then
  echo
  uv run python "$(dirname "$0")/kv_prefix_audit.py" "$LOGDIR"/ds4server-*.log \
    | tee "$LOGDIR/kv-prefix-audit.txt" || true
fi

echo "Compare per-trial pass rates against the arm A restart baseline 14/14/14 (42/45)."
