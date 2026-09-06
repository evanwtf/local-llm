#!/bin/bash
# #146: does the sandbox target layout change the pass rate?
#
# `--targets sandbox` builds the agent's export from this repo's own clones and
# never renames anything in ~/git. It also changes what the agent sees when it
# guesses the operator's path: under legacy the guess is satisfied, under
# sandbox it is denied. That is a behavior change, and it lands on the pass
# rate, so the cutover needs a measurement rather than an argument.
#
# Pre-registered, before any run:
#
#   cut over:  the two arms are within 1 task per sweep of 15, across 2 sweeps
#              per arm -- the layout is a change of plumbing, not of measured
#              behavior, and sandbox becomes the default.
#   do not:    sandbox is worse by more than that -- the denied guess is
#              costing real trials, and the cutover is abandoned on the record
#              with the number that killed it.
#   no call:   the arms differ by more than 1 task but in opposite directions
#              across sweeps -- the effect is inside our run-to-run noise and
#              needs more sweeps than this batch has.
#
# Same protocol as scripts/strip_toggle_ab.sh: 15 tasks x 1 trial per run,
# ds4-server restarted before each, arms alternating A B B A, one harness
# commit throughout, rows to their own results file.
#
# The two drivers now share everything but the arm mechanism. That duplication
# is deliberate and temporary: the extraction is worth doing once a third
# experiment of this shape exists, and not before -- generalizing from two
# examples is how the harness got its last set of wrong abstractions.
#
# Usage:
#   scripts/targets_ab.sh [RUNS] [UNTIL_HHMM]
#
# UNTIL_HHMM is optional. When omitted the batch runs all RUNS to completion.
# When given, a run that would start past it VOIDS the whole batch (exit 1,
# VOID row in the manifest) rather than truncating it: a partial batch is no
# result, and a cutoff that silently turns 4 runs into 2 is a check that fails
# quietly.
#
# Read out with:
#   uv run python scripts/strip_ab_report.py \
#       --results benchmarks/agent/results-146-targets-ab.jsonl \
#       --manifest benchmarks/agent/results-146-targets-ab-manifest.jsonl

set -eu

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/ds4_server.sh"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNS="${1:-4}"
UNTIL="${2:-}"
LOGDIR="${LOGDIR:-$(mktemp -d)}"
BENCH_LOGS="${BENCH_LOGS:-$HOME/bench-logs}"
# TARGETS_AB_DRY_RUN=1 exercises the batch loop (the cutoff and the arm order)
# without the machine: no dirty check, no sync, no lock, no shim, no ds4-server,
# no run.py. Each run writes a dry row to the manifest so a test can count the
# loop. The measurement itself is not exercised; the loop is.
DRY_RUN="${TARGETS_AB_DRY_RUN:-0}"
DEFAULT_RESULTS="$REPO/benchmarks/agent/results-146-targets-ab.jsonl"
DEFAULT_MANIFEST="${DEFAULT_RESULTS%.jsonl}-manifest.jsonl"
RESULTS="${RESULTS:-$DEFAULT_RESULTS}"
MANIFEST="${MANIFEST:-${RESULTS%.jsonl}-manifest.jsonl}"
# Dry-run writes rows to the manifest, so it must never touch the real results
# paths. Refuse unless both are overridden away from their defaults; the
# obvious invocation must be impossible, not merely undocumented. Compare
# against the defaults, not merely for non-empty: the comment is what the
# next reader trusts, so the code must match it.
if [ "$DRY_RUN" -eq 1 ] && { [ "$RESULTS" = "$DEFAULT_RESULTS" ] || [ "$MANIFEST" = "$DEFAULT_MANIFEST" ]; }; then
    echo "REFUSING: dry-run must override RESULTS and MANIFEST away from the real paths" >&2
    exit 1
fi
BATCH="${BATCH:-$(date +%m%d-%H%M)}"
SHIM_PORT=8101

DS4_MODEL="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
DS4_PLE="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_KV="$HOME/.ds4/server-kv"

HEAD_SHA="$(git -C "$REPO" rev-parse HEAD)"
if [ "$DRY_RUN" -eq 0 ] && [ -n "$(git -C "$REPO" status --porcelain | grep -v 'results.*\.jsonl' || true)" ]; then
    echo "REFUSING: harness checkout is dirty; commit first" >&2
    exit 1
fi

# The sandbox arm measures nothing if its clones are stale, and a stale clone
# fails as a wrong pass rate rather than as an error.
if [ "$DRY_RUN" -eq 0 ]; then
    (cd "$REPO" && uv run python scripts/sync_sandbox_targets.py)
fi

start_shim() {
    pkill -f qwen_tool_shim >/dev/null 2>&1 || true
    sleep 1
    (cd "$REPO" && env -u SHIM_NO_STRIP uv run python ds4_qwen_tool_shim.py \
        --port "$SHIM_PORT" --upstream http://127.0.0.1:8000 \
        > "$LOGDIR/shim.log" 2>&1 &)
    for _ in $(seq 1 30); do
        grep -q "scaffolding strip:" "$LOGDIR/shim.log" 2>/dev/null && break
        sleep 1
    done
    # The strip is worth 23 points of pass rate (#112), so a shim in the other
    # mode would swamp anything this experiment is trying to see.
    if ! grep -q "scaffolding strip: ON" "$LOGDIR/shim.log"; then
        echo "REFUSING: shim is not in the shipped strip-on mode" >&2
        exit 1
    fi
    echo "[$(date +%H:%M:%S)] shim up, strip ON"
}

restart_ds4() {
    local tag="$1"
    ds4_stop_server || exit 1
    echo "[$(date +%H:%M:%S)] starting ds4-server fresh for $tag..."
    (cd "$HOME/git/ds4-metal" && \
        ./ds4-server --metal \
            -m "$DS4_MODEL" --ple "$DS4_PLE" \
            --ctx 100000 --warm-weights \
            --kv-disk-dir "$DS4_KV" --kv-disk-space-mb 8192 \
            --host 127.0.0.1 --port 8000 \
            > "$LOGDIR/ds4server-$tag.log" 2>&1 &)
    (cd "$REPO" && uv run python benchmarks/agent/wait_ready.py \
        --base-url http://127.0.0.1:8000 \
        --model qwen3.8-flash-next-q4 | tail -2)
    ds4_record_route "$LOGDIR/ds4server-$tag.log" 8000
}

run_one() {
    local n="$1" arm="$2" dir started
    dir="$BENCH_LOGS/146-targets-$arm-$BATCH-run$n"
    mkdir -p "$dir"
    started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[$(date +%H:%M:%S)] run $n, targets=$arm, starting"
    (cd "$REPO" && uv run python benchmarks/agent/run.py \
        --backend qwen38fnds4shim --trials 1 --client opencode --no-lock \
        --targets "$arm" --allow-implausible --results "$RESULTS" \
        --require-harness-head "$HEAD_SHA" \
        > "$LOGDIR/run$n-$arm.log" 2>&1) || \
        echo "[$(date +%H:%M:%S)] run $n exited non-zero; keeping what it wrote"
    mv "$BENCH_LOGS"/*qwen38fnds4shim-opencode-1* "$dir/" 2>/dev/null || true
    printf '{"run":%d,"arm":"%s","started":"%s","ended":"%s","dir":"%s"}\n' \
        "$n" "$arm" "$started" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$dir" >> "$MANIFEST"
    echo "[$(date +%H:%M:%S)] run $n done, $(ls "$dir" | wc -l | tr -d ' ') transcripts"
}

PREFLIGHT="$REPO/benchmarks/agent/preflight.py"
if [ "$DRY_RUN" -eq 0 ]; then
    if ! uv run python "$PREFLIGHT" --acquire-lock "targets_ab.sh (#146, $RUNS runs)" --owner-pid $$; then
        echo "refusing to start: the machine is claimed by another run" >&2
        exit 1
    fi
    trap 'uv run python "$PREFLIGHT" --release-lock --owner-pid $$ >/dev/null 2>&1' EXIT
    ds4_arm_stop_trap
fi

echo "logs in:     $LOGDIR"
echo "rows in:     $RESULTS"
echo "harness at:  $HEAD_SHA"

if [ "$DRY_RUN" -eq 0 ]; then
    start_shim
fi
ORDER=(legacy sandbox sandbox legacy)
for n in $(seq 1 "$RUNS"); do
    # HH:MM compares correctly as a string within one day, which is the only
    # window this script is meant to run in. A cutoff that would skip a run
    # VOIDS the batch instead of truncating it: a partial batch is no result.
    if [ -n "$UNTIL" ] && [ "$(date +%H:%M)" \> "$UNTIL" ]; then
        echo "[$(date +%H:%M:%S)] VOID: past $UNTIL before run $n of $RUNS -- a partial batch is no result" >&2
        printf '{"run":%d,"arm":"VOID","reason":"past-until","until":"%s"}\n' \
            "$n" "$UNTIL" >> "$MANIFEST"
        exit 1
    fi
    arm="${ORDER[$(( (n - 1) % 4 ))]}"
    if [ "$DRY_RUN" -eq 0 ]; then
        restart_ds4 "run$n-$arm"
        run_one "$n" "$arm"
    else
        echo "[dry] run $n, targets=$arm"
        printf '{"run":%d,"arm":"%s","dry":true}\n' "$n" "$arm" >> "$MANIFEST"
    fi
done

pkill -f qwen_tool_shim >/dev/null 2>&1 || true
echo "[$(date +%H:%M:%S)] batch complete; manifest: $MANIFEST"
cat "$MANIFEST" 2>/dev/null || true
