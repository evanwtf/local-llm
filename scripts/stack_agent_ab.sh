#!/usr/bin/env bash
# Interleaved agent-suite A/B for two whole STACKS -- engine + weights (#138).
#
# The decode A/B (scripts/decode_ab_stack.sh) answers rate. This answers the
# only question that decides anything: does a session finish, and how fast.
#
# **Pre-registered as a SCREEN, not a superiority test.** Two sweeps per arm is
# n=30, which resolves a pass-rate difference of ~18-27 pp and a paired wall
# difference of ~17-26%. The effect expected from the decode A/B (-24.5%
# prefill, +9.5% decode) works out to roughly -10% to -17% of session wall,
# which sits AT OR BELOW that bar. A superiority pre-registration at this n
# would land on "could not tell" almost deterministically, so it is not made.
# What this run does buy: the new engine has never run the agent suite at all.
# It answers whether the stack loads, the shim still translates, sessions
# complete, and nothing is catastrophically wrong -- which is a prerequisite
# for the 3+3 paired run that could support a claim.
#
# Arms alternate NEW, OLD, NEW, OLD. #130: throughput declines across a
# measurement window, so a fixed order penalises whichever arm always runs
# later. Same-session pairing is also the whole reason to re-run the old arm
# rather than compare against rows from two evenings ago -- a cross-evening
# comparison hands the drift to one arm.
#
# The server restarts between every sweep. #112 established that server state
# degrades a session, so skipping restarts would give the drift to whichever
# arm ran second within a server's life.
#
# The rows do NOT record the engine binary or the gguf -- `env.servers` carries
# served_model_id and context_length only. Both arms answer to the same
# served_model_id, so without the run record written here the cell would
# silently mix two engines exactly the way it silently mixed two clients
# (#137). run-record.txt is that record; keep it with the results.
set -euo pipefail

SWEEPS=${SWEEPS:-2}
OUT=${OUT:-$HOME/bench-logs/138-stack-ab}
BENCH_LOGS=${BENCH_LOGS:-$HOME/bench-logs}
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

NEW_TREE=$HOME/git/ds4-ivan-qwen38fn
NEW_GGUF=$HOME/models/qwen3.8-flash-next-ds4-q4k-imatrix/Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf
NEW_PLE=$HOME/models/qwen3.8-flash-next-ds4-q4k-imatrix/Qwen3.8-Flash-Next-PLE-Q4_1.gguf
# A dedicated KV directory. ds4-server's disk cache runs cross-quant=accept, so
# a prefix cached by the other arm would be reused here with activations
# computed from different weights. Separate directories, or the two arms
# quietly contaminate each other.
NEW_KV=$HOME/.ds4/server-kv-kimat
NEW_BACKEND=qwen38fnds4kimat

OLD_TREE=$HOME/git/ds4-metal
OLD_GGUF=$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q40RoutedExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf
OLD_PLE=$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf
OLD_KV=$HOME/.ds4/server-kv
OLD_BACKEND=qwen38fnds4shim

mkdir -p "$OUT"

if ! pgrep -f qwen_tool_shim >/dev/null; then
  echo "REFUSING: ds4_qwen_tool_shim.py is not running on :8101" >&2
  exit 1
fi
# The published rows were all taken with the strip on. An inherited
# SHIM_NO_STRIP would make this a different experiment without saying so.
if [ -n "${SHIM_NO_STRIP:-}" ]; then
  echo "REFUSING: SHIM_NO_STRIP is set; every published row has the strip on (#112)" >&2
  exit 1
fi

for f in "$NEW_GGUF" "$NEW_PLE" "$OLD_GGUF" "$OLD_PLE"; do
  [ -e "$f" ] || { echo "REFUSING: missing $f" >&2; exit 1; }
done

{
  echo "# stack agent A/B, started $(date '+%Y-%m-%dT%H:%M:%S %Z')"
  echo "# SCREEN, not a superiority test: n=$((SWEEPS * 15))/arm resolves ~18-27 pp pass, ~17-26% paired wall."
  echo "NEW backend=$NEW_BACKEND engine=$NEW_TREE @ $(git -C "$NEW_TREE" rev-parse --short HEAD 2>/dev/null || echo ?)"
  echo "NEW gguf=$(basename "$NEW_GGUF") ($(stat -f %z "$NEW_GGUF") bytes)  kv=$NEW_KV"
  echo "OLD backend=$OLD_BACKEND engine=$OLD_TREE @ $(git -C "$OLD_TREE" rev-parse --short HEAD 2>/dev/null || echo ?)"
  echo "OLD gguf=$(basename "$OLD_GGUF") ($(stat -f %z "$OLD_GGUF") bytes)  kv=$OLD_KV"
  echo "# engine and quant move together in both arms; neither can be attributed alone (#138)."
} > "$OUT/run-record.txt"

restart_server() {
  local tree=$1 gguf=$2 ple=$3 kv=$4 tag=$5
  echo "[$(date +%H:%M:%S)] stopping ds4-server for $tag..."
  pkill -f 'ds4-server --metal' 2>/dev/null || true
  sleep 3
  pgrep -f 'ds4-server --metal' >/dev/null && { pkill -9 -f 'ds4-server --metal'; sleep 2; }
  pgrep -f 'ds4-server --metal' >/dev/null && { echo "REFUSING: server would not stop" >&2; exit 1; }
  echo "[$(date +%H:%M:%S)] starting $tag..."
  ( cd "$tree" && ./ds4-server --metal -m "$gguf" --ple "$ple" \
      --ctx 100000 --warm-weights \
      --kv-disk-dir "$kv" --kv-disk-space-mb 8192 \
      --host 127.0.0.1 --port 8000 > "$OUT/server-$tag.log" 2>&1 & )
  ( cd "$REPO" && uv run python benchmarks/agent/wait_ready.py \
      --base-url http://127.0.0.1:8000 --model qwen3.8-flash-next-q4 | tail -1 )
}

sweep() {
  local arm=$1 n=$2 backend=$3
  local tag="${arm}-sweep${n}"
  echo "[$(date +%H:%M:%S)] === $tag ($backend) ==="
  ( cd "$REPO" && uv run python benchmarks/agent/run.py \
      --backend "$backend" --trials 1 --client opencode --no-lock \
      > "$OUT/$tag.log" 2>&1 ) || echo "[$(date +%H:%M:%S)] $tag returned non-zero"
  mkdir -p "$OUT/$tag"
  # Transcripts move out of the top level immediately. Leaving them is how
  # #112's pre-remedy evidence was destroyed -- later sweeps write the same
  # filenames. save_transcript() no longer overwrites, but a per-sweep
  # directory is what makes the rows attributable at all.
  mv "$BENCH_LOGS"/*"$backend"-opencode-1* "$OUT/$tag/" 2>/dev/null || true
  echo "[$(date +%H:%M:%S)] $tag done, $(ls "$OUT/$tag" 2>/dev/null | wc -l | tr -d ' ') transcripts"
  echo "$tag $(date '+%H:%M:%S')" >> "$OUT/sweep-order.txt"
}

for n in $(seq 1 "$SWEEPS"); do
  restart_server "$NEW_TREE" "$NEW_GGUF" "$NEW_PLE" "$NEW_KV" "new-sweep$n"
  sweep new "$n" "$NEW_BACKEND"
  restart_server "$OLD_TREE" "$OLD_GGUF" "$OLD_PLE" "$OLD_KV" "old-sweep$n"
  sweep old "$n" "$OLD_BACKEND"
done
echo "[$(date +%H:%M:%S)] all $((SWEEPS * 2)) sweeps complete under $OUT"
