#!/bin/bash
# #112 remedy 2: does echoing the shim's own scaffolding back to the model
# carry the tool-call degeneration loop?
#
# The shim removes bare `<tool_call>` tags from returned content when it has
# recovered a call (shipped 2026-09-03, `f2fcc1f`). It has never been measured.
# `SHIM_NO_STRIP=1` is the off arm and touches nothing else.
#
# Pre-registered on the issue before any run, and repeated here so a reader of
# the output does not have to go and find it:
#
#   works:   with the strip removed, the conditional failure rate after >=1
#            prior tool error rises relative to the strip-on arm on the same
#            protocol, at >=30 failures per arm -- the echo carries the loop
#            and the strip stays.
#   nothing: the two arms are indistinguishable at >=30 failures per arm --
#            the echo is not the carrier; the strip is scaffolding hygiene,
#            not a fix, and item 2 closes as measured-no-effect.
#   no call: fewer than 30 failures per arm -- no claim either way.
#
# The outcome variable (multi-turn death rate) is out of reach: it sits at
# 3/90 under this protocol and would need ~230 trials per arm to resolve a
# drop to zero. The conditional is a MECHANISM PROXY, and the link from its
# slope to actual deaths has never been measured. Say so wherever it is
# quoted.
#
# Design
# ------
# - One run = 15 tasks x 1 trial against qwen38fnds4shim under OpenCode, with
#   the model server restarted before it. That is the restart-between-trials
#   protocol the #112 analysis specified, and it is the unit of alternation.
# - Arms alternate **A B B A**, repeating. This machine drifts ~10% within a
#   session, so an A/B/A/B order would give arm A every early slot; ABBA
#   cancels a linear drift exactly at each block of four, and the arms stay
#   equal in count after every block (#130).
# - Rows go to their own results file, NOT results.jsonl. Nothing records
#   SHIM_NO_STRIP on a row, so a strip-off row in the published file would be
#   indistinguishable from the shipped configuration and would quietly move
#   qwen38fnds4shim's pass rate. This is the trap cohort_split.py was written
#   about: the confound columns read clean.
# - Both arms pass the same flags, including --allow-implausible. The off arm
#   may well collapse -- that is the hypothesis -- and a gate that halts one
#   arm and not the other is a difference between the arms.
# - --require-harness-head pins every run to one harness commit, so the batch
#   cannot span a code change (2026-09-04: four sweeps, four harness_heads).
#   It also means: do not commit to this repo while the batch runs.
#
# Usage:
#   scripts/strip_toggle_ab.sh [RUNS] [UNTIL_HHMM]
#     RUNS       how many runs to attempt, default 8 (two full ABBA blocks)
#     UNTIL_HHMM stop STARTING runs at this local time, default 09:15
#
# Analysis, when it is done:
#   uv run python scripts/strip_ab_report.py

set -eu

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/ds4_server.sh"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNS="${1:-8}"
UNTIL="${2:-09:15}"
LOGDIR="${LOGDIR:-$(mktemp -d)}"
BENCH_LOGS="${BENCH_LOGS:-$HOME/bench-logs}"
RESULTS="${RESULTS:-$REPO/benchmarks/agent/results-112-strip-ab.jsonl}"
# The manifest is the only record of which arm produced which rows, so it
# lives beside them rather than in a temp directory that gets cleaned up.
MANIFEST="${MANIFEST:-${RESULTS%.jsonl}-manifest.jsonl}"

DS4_MODEL="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
DS4_PLE="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_KV="$HOME/.ds4/server-kv"
BATCH="${BATCH:-$(date +%m%d-%H%M)}"
SHIM_PORT=8101

HEAD_SHA="$(git -C "$REPO" rev-parse HEAD)"
if [ -n "$(git -C "$REPO" status --porcelain | grep -v 'results.*\.jsonl' || true)" ]; then
    echo "REFUSING: harness checkout is dirty; commit first" >&2
    exit 1
fi

start_shim() {
    local arm="$1" want log
    pkill -f qwen_tool_shim >/dev/null 2>&1 || true
    sleep 1
    log="$LOGDIR/shim-$arm.log"
    if [ "$arm" = "off" ]; then
        (cd "$REPO" && SHIM_NO_STRIP=1 uv run python ds4_qwen_tool_shim.py \
            --port "$SHIM_PORT" --upstream http://127.0.0.1:8000 > "$log" 2>&1 &)
        want="scaffolding strip: OFF"
    else
        (cd "$REPO" && env -u SHIM_NO_STRIP uv run python ds4_qwen_tool_shim.py \
            --port "$SHIM_PORT" --upstream http://127.0.0.1:8000 > "$log" 2>&1 &)
        want="scaffolding strip: ON"
    fi
    # Read the arm back out of the shim's own startup line rather than trusting
    # the variable we just set. The whole experiment is void if the shim is in
    # the other mode, and nothing downstream would ever show it.
    for _ in $(seq 1 30); do
        grep -q "scaffolding strip:" "$log" 2>/dev/null && break
        sleep 1
    done
    if ! grep -q "$want" "$log"; then
        echo "REFUSING: shim did not report '$want'; got:" >&2
        grep "scaffolding strip:" "$log" >&2 || echo "  (no line at all)" >&2
        exit 1
    fi
    echo "[$(date +%H:%M:%S)] shim up, arm=$arm ($(grep -o 'scaffolding strip: [A-Z]*' "$log"))"
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
    # #149: stamp which Metal kernel route this server took, or every row in
    # the run says `metal_route: unrecorded`. The first eight runs of this
    # experiment did exactly that, because this call was missing.
    ds4_record_route "$LOGDIR/ds4server-$tag.log" 8000
}

run_one() {
    local n="$1" arm="$2" dir started
    dir="$BENCH_LOGS/112-strip-$arm-$BATCH-run$n"
    mkdir -p "$dir"
    started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[$(date +%H:%M:%S)] run $n, arm=$arm, starting"
    (cd "$REPO" && uv run python benchmarks/agent/run.py \
        --backend qwen38fnds4shim --trials 1 --client opencode --no-lock \
        --allow-implausible --results "$RESULTS" \
        --require-harness-head "$HEAD_SHA" \
        > "$LOGDIR/run$n-$arm.log" 2>&1) || \
        echo "[$(date +%H:%M:%S)] run $n exited non-zero; keeping what it wrote"
    mv "$BENCH_LOGS"/*qwen38fnds4shim-opencode-1* "$dir/" 2>/dev/null || true
    printf '{"run":%d,"arm":"%s","started":"%s","ended":"%s","dir":"%s"}\n' \
        "$n" "$arm" "$started" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$dir" >> "$MANIFEST"
    echo "[$(date +%H:%M:%S)] run $n done, $(ls "$dir" | wc -l | tr -d ' ') transcripts"
}

PREFLIGHT="$REPO/benchmarks/agent/preflight.py"
if ! uv run python "$PREFLIGHT" --acquire-lock "strip_toggle_ab.sh (#112 remedy 2, $RUNS runs)" --owner-pid $$; then
    echo "refusing to start: the machine is claimed by another run" >&2
    exit 1
fi
trap 'uv run python "$PREFLIGHT" --release-lock --owner-pid $$ >/dev/null 2>&1' EXIT
ds4_arm_stop_trap

echo "logs in:     $LOGDIR"
echo "rows in:     $RESULTS"
echo "harness at:  $HEAD_SHA"
echo "stop start:  $UNTIL"

# A B B A, repeating.
ORDER=(on off off on)
for n in $(seq 1 "$RUNS"); do
    if [ "$(date +%H:%M)" \> "$UNTIL" ]; then
        echo "[$(date +%H:%M:%S)] past $UNTIL -- not starting run $n"
        break
    fi
    arm="${ORDER[$(( (n - 1) % 4 ))]}"
    start_shim "$arm"
    restart_ds4 "run$n-$arm"
    run_one "$n" "$arm"
done

pkill -f qwen_tool_shim >/dev/null 2>&1 || true
echo "[$(date +%H:%M:%S)] batch complete; manifest: $MANIFEST"
cat "$MANIFEST" 2>/dev/null || true
