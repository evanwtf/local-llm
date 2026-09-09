#!/bin/bash
# The first ds4 MTP arm that can actually draft, against its own control (#151, #39).
#
# Every MTP row this project has published was taken on an arm that never
# speculated. ds4 reaches its Qwen MTP path only at `temperature <= 0.0f`
# (`ds4.c:80120 at ds4-metal ba01f5d`); above it the speculative call does one
# plain eval and returns (`ds4.c:80216 at ds4-metal ba01f5d`). A request with
# no temperature gets `DS4_DEFAULT_TEMPERATURE`, `1.0f`
# (`ds4.h:56 at ds4-metal ba01f5d`), and OpenCode's config declares
# `"temperature": false` for this model, so it sends none.
#
# TWO ARMS, NOT ONE. Pinning the temperature is itself a change of regime, so
# a greedy MTP arm alone cannot separate speculation from greedy decoding:
#
#   qwen38fnds4mtp7greedy   temperature 0, MTP on
#   qwen38fnds4greedy       temperature 0, MTP off   <- what greedy costs alone
#
# Both talk to a SECOND shim on :8102 with SHIM_TEMPERATURE=0. :8101 is left
# exactly as it is, so the 262 existing rows keep comparing.
#
# The server is restarted between arms because the MTP and non-MTP KV formats
# are incompatible and ds4 rejects the other's checkpoints -- a shared
# directory leaves one arm re-prefilling and the only symptom is that it looks
# slower.
#
# Usage:
#   scripts/greedy_mtp_ab.sh              # one sweep per arm
#   TRIALS=2 scripts/greedy_mtp_ab.sh     # two
#
# run.py interleaves nothing here: each arm needs its own server, so they are
# separate invocations and the ORDER ALTERNATES per trial round. Position bias
# is +0.9% median and +5.9% on the first rep of a cold session (#130, #201),
# and an unalternated pair hands the whole of it to one arm.

set -eu

# shellcheck source=lib/ds4_server.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/ds4_server.sh"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TRIALS="${TRIALS:-1}"
ROUNDS="${ROUNDS:-2}"
LOGDIR="${LOGDIR:-$HOME/bench-logs/greedy-mtp-ab-$(date +%Y%m%d-%H%M%S)}"

DS4_MODEL="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q4KExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf"
DS4_PLE="$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf"
DS4_MTP="$HOME/models/qwen3.8-flash-next-ds4-q4/qwen3.8-flash-next-q4-mtp.gguf"
KV_MTP="$HOME/.ds4/server-kv-mtp"
KV_PLAIN="$HOME/.ds4/server-kv"

if [ $((ROUNDS % 2)) -ne 0 ] && [ "${ALLOW_ODD_ROUNDS:-}" != 1 ]; then
    echo "REFUSING: ROUNDS=$ROUNDS is odd. Alternation cancels position bias" >&2
    echo "only on an even count (#130, #201). ALLOW_ODD_ROUNDS=1 overrides." >&2
    exit 2
fi

mkdir -p "$LOGDIR"

# The greedy shim, on its own port, so :8101 keeps serving every other backend
# unchanged. Started here and stopped on exit -- a leftover shim pinning
# temperature would silently make a later run greedy.
start_greedy_shim() {
    pkill -f 'qwen_tool_shim.py --port 8102' 2>/dev/null || true
    sleep 1
    (cd "$REPO" && SHIM_TEMPERATURE=0 nohup uv run python ds4_qwen_tool_shim.py \
        --port 8102 --upstream http://127.0.0.1:8000 \
        > "$LOGDIR/shim-8102.log" 2>&1 &)
    sleep 3
    pgrep -f 'qwen_tool_shim.py --port 8102' >/dev/null || {
        echo "REFUSING: the greedy shim did not start on :8102" >&2; exit 1; }
}

stop_greedy_shim() {
    pkill -f 'qwen_tool_shim.py --port 8102' 2>/dev/null || true
}

start_server() {
    local want_mtp="$1" kvdir="$2" tag="$3"
    ds4_stop_server || exit 1
    mkdir -p "$kvdir"
    local mtp_args=()
    [ "$want_mtp" = yes ] && mtp_args=(--mtp-model "$DS4_MTP" --mtp-draft 7 --mtp-timing)
    (cd "$HOME/git/ds4-metal" && \
        ./ds4-server --metal -m "$DS4_MODEL" --ple "$DS4_PLE" \
            --ctx 100000 --warm-weights \
            --kv-disk-dir "$kvdir" --kv-disk-space-mb 8192 \
            ${mtp_args[@]+"${mtp_args[@]}"} \
            --host 127.0.0.1 --port 8000 > "$LOGDIR/ds4server-$tag.log" 2>&1 &)
    (cd "$REPO" && uv run python benchmarks/agent/wait_ready.py \
        --base-url http://127.0.0.1:8000 --model qwen3.8-flash-next-q4 | tail -1)
    ds4_record_route "$LOGDIR/ds4server-$tag.log" 8000
    local line
    line=$(grep -m1 'Qwen graph allocated' "$LOGDIR/ds4server-$tag.log" || true)
    echo "graph($tag): $line"
    [ -n "$line" ] || {
        echo "REFUSING: no 'Qwen graph allocated' line in $LOGDIR/ds4server-$tag.log;" \
             "the server did not start, so nothing can be said about its MTP head" >&2
        exit 1; }
    case "$want_mtp:$line" in
        yes:*MTP=off*) echo "REFUSING: MTP arm reports MTP=off" >&2; exit 1 ;;
        no:*MTP=off*) ;;
        no:*) echo "REFUSING: control arm loaded an MTP head" >&2; exit 1 ;;
    esac
}

run_arm() {
    local backend="$1" tag="$2"
    (cd "$REPO" && uv run python benchmarks/agent/run.py \
        --backend "$backend" --client opencode --trials "$TRIALS" --no-lock \
        --batch "greedy-mtp-ab" \
        --server-log "$LOGDIR/ds4server-$tag.log" --draft-log-engine ds4) \
        2>&1 | tee "$LOGDIR/run-$tag.log"
}

PREFLIGHT="$REPO/benchmarks/agent/preflight.py"
if ! (cd "$REPO" && uv run python "$PREFLIGHT" --acquire-lock "greedy_mtp_ab.sh (#151/#39)" --owner-pid $$); then
  echo "refusing to start: the machine is claimed by another run" >&2
  exit 1
fi
trap '(cd "$REPO" && uv run python "$PREFLIGHT" --release-lock --owner-pid '$$' >/dev/null 2>&1); stop_greedy_shim' EXIT
ds4_arm_stop_trap

echo "logs in: $LOGDIR"
start_greedy_shim

for round in $(seq 1 "$ROUNDS"); do
    echo "=== round $round of $ROUNDS ==="
    if [ $((round % 2)) -eq 1 ]; then
        order=("mtp:qwen38fnds4mtp7greedy" "plain:qwen38fnds4greedy")
    else
        order=("plain:qwen38fnds4greedy" "mtp:qwen38fnds4mtp7greedy")
    fi
    for entry in "${order[@]}"; do
        kind="${entry%%:*}"; backend="${entry#*:}"
        tag="r$round-$kind"
        if [ "$kind" = mtp ]; then
            start_server yes "$KV_MTP" "$tag"
        else
            start_server no "$KV_PLAIN" "$tag"
        fi
        echo "[$(date +%H:%M:%S)] round $round arm $kind ($backend)"
        run_arm "$backend" "$tag"
    done
done

echo "[$(date +%H:%M:%S)] complete -- $LOGDIR"
echo "Read the MTP arm's rows for drafting_share before reading any wall time:"
echo "  an arm that emitted no cycle is not an MTP arm, whatever it declared."
