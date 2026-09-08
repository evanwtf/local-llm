#!/bin/bash
# Prove the MTP refusal fires, then take rows that carry the treatment (#210).
#
# The issue's `Done when` is a pair, and the pair is the point:
#
#   1. a real batch writes `draft` on every row of an MTP backend, and
#   2. one deliberately broken arm is shown to be refused.
#
# Half 2 without half 1 is a gate nobody has run in anger. Half 1 without
# half 2 is a green light from a gate that may never fire. #210 exists
# because 119 MTP rows were taken with neither.
#
# THE BROKEN ARM IS NOT A BROKEN FLAG. Omitting `--mtp-model` is the exact
# failure the runbook records: `--mtp-draft 7 --mtp-timing` are accepted
# without complaint, the argv reads as an MTP arm, and the only place the
# difference shows is the server's own `Qwen graph allocated` line
# (`MTP=off verifier=off` against `MTP=Q4_K/Q8_0/BF16 verifier=block/max16`).
# That is what makes it worth demonstrating: `counters_on()` reads the argv
# and PASSES on this configuration. Only the per-trial counters catch it.
#
# Usage:
#   scripts/mtp_treatment_gate.sh bypass    # stage 1, ~5 min, expects REFUSAL
#   scripts/mtp_treatment_gate.sh treated   # stage 2, ~50 min per trial-sweep
#   scripts/mtp_treatment_gate.sh silent    # stage 3, ONLY if stage 2 refuses
#
# Stage 3 is not a retry. It is the escape the refusal message itself names
# ("Re-run without --require-draft to measure it deliberately"), and it is how
# #151's zero-counter case gets rows instead of an exit code. Do not run it to
# make stage 2 go green.

set -eu

# shellcheck source=lib/ds4_server.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/ds4_server.sh"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="${1:-}"
TRIALS="${TRIALS:-1}"
LOGDIR="${LOGDIR:-$HOME/bench-logs/210-treatment-gate-$(date +%Y%m%d-%H%M%S)}"

DS4_MODEL="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
DS4_PLE="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_MTP="$HOME/models/qwen3.8-flash-next-ds4-q4/qwen3.8-flash-next-q4-mtp.gguf"

# The MTP and non-MTP KV formats are incompatible and ds4 rejects the other
# one's checkpoints. The bypass arm therefore gets its own directory: pointing
# it at ~/.ds4/server-kv-mtp would leave rejected checkpoints behind and make
# the next real arm re-prefill, whose only symptom is that it looks slower.
KV_TREATED="$HOME/.ds4/server-kv-mtp"
KV_BYPASS="$HOME/.ds4/server-kv-210-bypass"

# mbox-scan is the cheapest task in the corpus (median 10.8 s over 8 rows).
# The bypass arm is a demonstration that an exit code fires, not a
# measurement, so it buys the shortest trial that still drives real traffic.
BYPASS_TASK="${BYPASS_TASK:-mbox-scan}"

case "$STAGE" in
  bypass|treated|silent) ;;
  *) echo "usage: $0 {bypass|treated|silent}" >&2; exit 2 ;;
esac

if ! pgrep -f qwen_tool_shim >/dev/null; then
    echo "REFUSING: ds4_qwen_tool_shim.py is not running on :8101" >&2
    echo "Start it: uv run python ds4_qwen_tool_shim.py --port 8101 --upstream http://127.0.0.1:8000" >&2
    exit 1
fi

mkdir -p "$LOGDIR"
SERVER_LOG="$LOGDIR/ds4server-$STAGE.log"

start_server() {
    local want_mtp="$1" kvdir="$2"
    ds4_stop_server || exit 1
    mkdir -p "$kvdir"
    echo "[$(date +%H:%M:%S)] starting ds4-server (mtp-model=$want_mtp, kv=$kvdir)"
    if [ "$want_mtp" = yes ]; then
        (cd "$HOME/git/ds4-metal" && \
            ./ds4-server --metal -m "$DS4_MODEL" --ple "$DS4_PLE" \
                --ctx 100000 --warm-weights \
                --kv-disk-dir "$kvdir" --kv-disk-space-mb 8192 \
                --mtp-model "$DS4_MTP" --mtp-draft 7 --mtp-timing \
                --host 127.0.0.1 --port 8000 > "$SERVER_LOG" 2>&1 &)
    else
        # Same flags minus --mtp-model. This is the broken arm.
        (cd "$HOME/git/ds4-metal" && \
            ./ds4-server --metal -m "$DS4_MODEL" --ple "$DS4_PLE" \
                --ctx 100000 --warm-weights \
                --kv-disk-dir "$kvdir" --kv-disk-space-mb 8192 \
                --mtp-draft 7 --mtp-timing \
                --host 127.0.0.1 --port 8000 > "$SERVER_LOG" 2>&1 &)
    fi
    (cd "$REPO" && uv run python benchmarks/agent/wait_ready.py \
        --base-url http://127.0.0.1:8000 --model qwen3.8-flash-next-q4 | tail -2)
    ds4_record_route "$SERVER_LOG" 8000
}

# Assert the arm is the arm before spending a trial on it. The runbook says to
# read this line before trusting an MTP row; a bypass arm that quietly loaded
# the sidecar would produce a passing run and prove nothing, and a treated arm
# that quietly did not is the whole of #210.
assert_graph() {
    local want="$1" line
    line=$(grep -m1 'Qwen graph allocated' "$SERVER_LOG" || true)
    if [ -z "$line" ]; then
        echo "REFUSING: no 'Qwen graph allocated' line in $SERVER_LOG" >&2
        exit 1
    fi
    echo "graph: $line"
    if [ "$want" = off ]; then
        case "$line" in
            *"MTP=off"*) ;;
            *) echo "REFUSING: bypass arm loaded an MTP head -- not the broken arm" >&2; exit 1 ;;
        esac
    else
        case "$line" in
            *"MTP=off"*) echo "REFUSING: treated arm reports MTP=off -- the sidecar did not load" >&2; exit 1 ;;
        esac
        grep -m1 'MTP sidecar loaded' "$SERVER_LOG" || {
            echo "REFUSING: no 'MTP sidecar loaded' line" >&2; exit 1; }
    fi
}

PREFLIGHT="$REPO/benchmarks/agent/preflight.py"
if ! (cd "$REPO" && uv run python "$PREFLIGHT" --acquire-lock "mtp_treatment_gate.sh $STAGE (#210)" --owner-pid $$); then
  echo "refusing to start: the machine is claimed by another run" >&2
  exit 1
fi
trap '(cd "$REPO" && uv run python "$PREFLIGHT" --release-lock --owner-pid '$$' >/dev/null 2>&1)' EXIT
ds4_arm_stop_trap

echo "logs in: $LOGDIR"

case "$STAGE" in
bypass)
    start_server no "$KV_BYPASS"
    assert_graph off
    # A refused trial raises before write_row, so no row reaches the corpus.
    # --results points at a scratch file anyway: the claim "the broken arm
    # contaminated nothing" should not rest on reading the caller correctly.
    set +e
    (cd "$REPO" && uv run python benchmarks/agent/run.py \
        --backend qwen38fnds4mtp7shim --client opencode --trials 1 \
        --task "$BYPASS_TASK" --no-lock \
        --results "$LOGDIR/bypass-scratch.jsonl" \
        --batch 210-bypass-demo \
        --server-log "$SERVER_LOG" --draft-log-engine ds4 --require-draft) \
        2>&1 | tee "$LOGDIR/run-bypass.log"
    rc=${PIPESTATUS[0]}
    set -e
    echo "=== bypass arm exit=$rc (0 would mean the gate did NOT fire) ==="
    if [ "$rc" -eq 0 ]; then
        echo "FAILED: the broken arm ran to completion. The gate is not binding." >&2
        exit 1
    fi
    grep -E 'refusing to continue|MTP (drafted|emitted|accepted)' "$LOGDIR/run-bypass.log" || true
    echo "rows written by the refused arm: $(wc -l < "$LOGDIR/bypass-scratch.jsonl" 2>/dev/null || echo 0)"
    ;;
treated)
    start_server yes "$KV_TREATED"
    assert_graph on
    (cd "$REPO" && uv run python benchmarks/agent/run.py \
        --backend qwen38fnds4mtp7shim --client opencode --trials "$TRIALS" \
        --no-lock --batch 210-treated \
        --server-log "$SERVER_LOG" --draft-log-engine ds4 --require-draft) \
        2>&1 | tee "$LOGDIR/run-treated.log"
    ;;
silent)
    start_server yes "$KV_TREATED"
    assert_graph on
    (cd "$REPO" && uv run python benchmarks/agent/run.py \
        --backend qwen38fnds4mtp7shim --client opencode --trials "$TRIALS" \
        --no-lock --batch 210-silent-arm \
        --server-log "$SERVER_LOG" --draft-log-engine ds4 --no-require-draft) \
        2>&1 | tee "$LOGDIR/run-silent.log"
    ;;
esac

echo "[$(date +%H:%M:%S)] stage $STAGE complete -- $LOGDIR"
