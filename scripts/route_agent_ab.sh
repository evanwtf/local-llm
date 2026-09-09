#!/usr/bin/env bash
# Interleaved agent-suite A/B for the Metal 4 TensorOps route (#149).
#
# Pre-registered on evanwtf/local-llm#149 before this script was committed.
# The condensed form of the registration:
#
#   One engine tree (ds4-metal @ ba01f5d + ivan's a11bf74 withhold), one
#   binary, one gguf, one PLE, one shim. The arms differ ONLY by the env:
#     T = DS4_METAL_ENABLE_TENSOR=1   (the fast TensorOps route)
#     R = no variable                 (reference kernels, withheld route)
#   3 sweeps per arm, alternating T-first / R-first / T-first. Odd count: T
#   leads twice, so the position term favors T on wall time -- if T reads
#   slower anyway that is robust; if T reads faster, part may be position.
#
#   Screens, fixed before the run:
#   1. Route breaks a task (paired): a task fails in >=2 of 3 T sweeps while
#      passing 3 of 3 R sweeps. Completed verdicts only; harness-level
#      failures void the sweep (rerun, recorded in run-record.txt).
#   2. Aggregate deficit: T passes >=6 fewer task-runs than R (of 45 per
#      arm). 6 is ~2 sigma at p~0.9; a 3-task delta is 1 sigma and is
#      reported as no signal.
#   3. Route dominated (wall): T slower than R in all 3 paired sweeps with
#      pooled mean wall ratio >= 1.05. Arithmetic, not judgment. If it
#      fires the pre-registered conclusion is: the route is not worth its
#      correctness cost, regardless of screens 1-2.
#   4. Route buys with no measured break: T faster in all 3 paired sweeps,
#      pooled ratio <= 0.95, zero tasks on screen 1, aggregate delta >= 0.
#   Anything else: report the numbers and stop. The report does NOT pick
#   between #149's option (a) (cherry-pick and re-measure) and option (b)
#   (keep the fast route, record it, stop calling ds4 Metal numbers exact).
#
# Phase 0 validates both routes with the equivalence gate before any sweep:
# the gate's `auto` candidate runs the withheld route and is ASSERTED
# against the reference (must pass); its `tensor-optin` candidate forces the
# route and is REPORTED (must show the drift signature this issue
# reproduced on GLM-5.3-Flash-Q2: worst_rms ~1.386, worst_max_abs ~7.27,
# accepted band 1.2-1.6 / 6.0-8.5). If the signature does not match, the
# assumption "opt-in route = the standing auto route" is false and nothing
# runs. The gate needs a full model load, so it cannot run mid-sweep beside
# the ~74 GiB server; the in-batch gate is therefore skipped with
# --skip-tensor-gate on both arms, and the per-sweep treatment assertion
# below does the route check from the server log instead.
#
# Per-sweep treatment assertion, from the server startup log: a T sweep
# must show "Metal 4 tensor API enabled for Tensor kernels" and a fast
# path; an R sweep must show the withhold line
# "available but not enabled (numerics)" and must NOT show the enable line.
# A violated assertion aborts the run -- everything after it would sit on a
# broken premise.
#
# Separate KV disk directories per arm: ds4-server's disk cache runs
# cross-quant=accept, so a prefix cached by the other arm would be reused
# with activations computed from different numerics.
set -euo pipefail

SWEEPS_PER_ARM=${SWEEPS_PER_ARM:-3}
OUT=${OUT:-$HOME/bench-logs/149-route-ab}
BENCH_LOGS=${BENCH_LOGS:-$HOME/bench-logs}
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

TREE=$HOME/git/ds4-metal-ref
GGUF=$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q40RoutedExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf
PLE=$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf
GLM_Q2=$HOME/git/ds4/gguf/GLM-5.3-Flash-Q2.gguf
KV_T=$HOME/.ds4/server-kv-149t
KV_R=$HOME/.ds4/server-kv-149r
BACKEND=qwen38fnds4shim

TENSOR_LINE="Metal 4 tensor API enabled for Tensor kernels"
WITHHOLD_LINE="available but not enabled (numerics)"
FAST_PATH_LINE="complete fast path"

mkdir -p "$OUT"

if ! pgrep -f qwen_tool_shim >/dev/null; then
  echo "REFUSING: ds4_qwen_tool_shim.py is not running on :8101" >&2
  exit 1
fi
if [ -n "${SHIM_NO_STRIP:-}" ]; then
  echo "REFUSING: SHIM_NO_STRIP is set; every published row has the strip on (#112)" >&2
  exit 1
fi

for f in "$GGUF" "$PLE" "$GLM_Q2" "$TREE/ds4-server" "$TREE/ds4_test"; do
  [ -e "$f" ] || { echo "REFUSING: missing $f" >&2; exit 1; }
done
# The R arm is defined by the withhold being in the tree. The commit hash of
# the cherry-pick differs from ivan's; the subject is the identity.
if ! git -C "$TREE" log --format=%s -1 | grep -q "^Withhold the automatic Metal 4 tensor enable"; then
  echo "REFUSING: $TREE is not on the withhold commit" >&2
  exit 1
fi

# Phase 0: equivalence validation, server down. Reuses an existing passing
# log so a rerun does not pay another full model load; re-runs and re-checks
# otherwise.
validate_routes() {
  if [ -e "$OUT/gate-validate.log" ] \
     && grep -Fq "metal-tensor-equivalence: OK" "$OUT/gate-validate.log" \
     && parse_signature "$OUT/gate-validate.log"; then
    echo "[$(date +%H:%M:%S)] gate validation already recorded in $OUT/gate-validate.log"
    return 0
  fi
  echo "[$(date +%H:%M:%S)] running ds4_test --metal-tensor-equivalence (full GLM-5.3-Flash-Q2 load; tens of minutes)..."
  pgrep -f 'ds4-server --metal' >/dev/null && {
    echo "REFUSING: a ds4-server is resident; the gate cannot load beside it" >&2
    exit 1
  }
  local rc=0
  ( cd "$TREE" && DS4_TEST_MODEL="$GLM_Q2" ./ds4_test --metal-tensor-equivalence \
      > "$OUT/gate-validate.log" 2>&1 ) || rc=$?
  # In the withhold tree the asserted `auto` candidate runs the withheld
  # route, so the gate's overall verdict must be OK even though the
  # reported tensor-optin candidate fails.
  [ "$rc" -eq 0 ] || { echo "REFUSING: gate exited $rc; see $OUT/gate-validate.log" >&2; exit 1; }
  grep -Fq "metal-tensor-equivalence: OK" "$OUT/gate-validate.log" \
    || { echo "REFUSING: no OK verdict in $OUT/gate-validate.log" >&2; exit 1; }
  parse_signature "$OUT/gate-validate.log" \
    || { echo "REFUSING: tensor-optin signature outside the pre-registered band; see $OUT/gate-validate.log" >&2; exit 1; }
}

# The tensor-optin summary line must carry the reproduced drift signature
# (worst_rms ~1.386, worst_max_abs ~7.27; band 1.2-1.6 / 6.0-8.5).
parse_signature() {
  local log=$1
  local rms maxabs
  rms=$(sed -nE 's/.*tensor-optin.*worst_rms=([0-9.]+).*/\1/p' "$log" | head -1)
  maxabs=$(sed -nE 's/.*tensor-optin.*worst_max_abs=([0-9.]+).*/\1/p' "$log" | head -1)
  [ -n "$rms" ] && [ -n "$maxabs" ] || return 1
  awk -v r="$rms" -v m="$maxabs" 'BEGIN { exit !(r >= 1.2 && r <= 1.6 && m >= 6.0 && m <= 8.5) }'
}

validate_routes

{
  echo "# route agent A/B, started $(date '+%Y-%m-%dT%H:%M:%S %Z')"
  echo "# pre-registered on #149; screens 1-4 in the script header"
  echo "TREE=$TREE @ $(git -C "$TREE" rev-parse --short HEAD) $(git -C "$TREE" log --format=%s -1)"
  echo "GGUF=$(basename "$GGUF") ($(stat -f %z "$GGUF") bytes)"
  echo "PLE=$(basename "$PLE")  KV_T=$KV_T  KV_R=$KV_R"
  echo "BACKEND=$BACKEND (one backend name; arms attributed via sweep-order.txt windows)"
  echo "gate validation: $OUT/gate-validate.log"
} > "$OUT/run-record.txt"

restart_server() {
  local arm=$1 tag=$2 kv=$3
  echo "[$(date +%H:%M:%S)] stopping ds4-server for $tag..."
  pkill -f 'ds4-server --metal' 2>/dev/null || true
  sleep 3
  pgrep -f 'ds4-server --metal' >/dev/null && { pkill -9 -f 'ds4-server --metal'; sleep 2; }
  pgrep -f 'ds4-server --metal' >/dev/null && { echo "REFUSING: server would not stop" >&2; exit 1; }
  mkdir -p "$kv"
  echo "[$(date +%H:%M:%S)] starting $tag (arm=$arm)..."
  if [ "$arm" = t ]; then
    ( cd "$TREE" && DS4_METAL_ENABLE_TENSOR=1 ./ds4-server --metal -m "$GGUF" --ple "$PLE" \
        --ctx 100000 --warm-weights \
        --kv-disk-dir "$kv" --kv-disk-space-mb 8192 \
        --host 127.0.0.1 --port 8000 > "$OUT/server-$tag.log" 2>&1 & )
  else
    ( cd "$TREE" && env -u DS4_METAL_ENABLE_TENSOR ./ds4-server --metal -m "$GGUF" --ple "$PLE" \
        --ctx 100000 --warm-weights \
        --kv-disk-dir "$kv" --kv-disk-space-mb 8192 \
        --host 127.0.0.1 --port 8000 > "$OUT/server-$tag.log" 2>&1 & )
  fi
  ( cd "$REPO" && uv run python benchmarks/agent/wait_ready.py \
      --base-url http://127.0.0.1:8000 --model qwen3.8-flash-next-q4 | tail -1 )
  assert_route "$arm" "$tag"
}

assert_route() {
  local arm=$1 tag=$2 log="$OUT/server-$2.log"
  grep -Fq "$FAST_PATH_LINE" "$log" \
    || { echo "REFUSING: $tag server log lacks '$FAST_PATH_LINE'; see $log" >&2; exit 1; }
  if [ "$arm" = t ]; then
    grep -Fq "$TENSOR_LINE" "$log" \
      || { echo "REFUSING: $tag is a T sweep but the server log lacks '$TENSOR_LINE'; see $log" >&2; exit 1; }
  else
    grep -Fq "$WITHHOLD_LINE" "$log" \
      || { echo "REFUSING: $tag is an R sweep but the server log lacks the withhold line; see $log" >&2; exit 1; }
    if grep -Fq "$TENSOR_LINE" "$log"; then
      echo "REFUSING: $tag is an R sweep but the server log shows the tensor route; see $log" >&2
      exit 1
    fi
  fi
  echo "[$(date +%H:%M:%S)] $tag route assertion passed (arm=$arm)"
}

sweep() {
  local arm=$1 n=$2
  local tag="${arm}-sweep${n}"
  # Both times, labeled by position -- one-time-per-line was the #138 bug
  # that gave each sweep the next sweep's window.
  local started
  started=$(date '+%H:%M:%S')
  echo "[$(date +%H:%M:%S)] === $tag ($arm arm, $BACKEND) ==="
  ( cd "$REPO" && uv run python benchmarks/agent/run.py \
      --backend "$BACKEND" --trials 1 --client opencode --no-lock \
      --skip-tensor-gate \
      --require-harness-head "$HARNESS_HEAD" \
      > "$OUT/$tag.log" 2>&1 ) || echo "[$(date +%H:%M:%S)] $tag returned non-zero"
  mkdir -p "$OUT/$tag"
  mv "$BENCH_LOGS"/*"$BACKEND"-opencode-1* "$OUT/$tag/" 2>/dev/null || true
  echo "[$(date +%H:%M:%S)] $tag done, $(ls "$OUT/$tag" 2>/dev/null | wc -l | tr -d ' ') transcripts"
  echo "$tag $started $(date '+%H:%M:%S')" >> "$OUT/sweep-order.txt"
}

# Pin the harness for the whole comparison. A comparative run pinned to a
# commit cannot be reproduced from uncommitted code.
HARNESS_HEAD=$(git -C "$REPO" rev-parse --short HEAD)
if ! git -C "$REPO" diff --quiet -- ':!*.jsonl' ':!*.log' || \
   ! git -C "$REPO" diff --cached --quiet -- ':!*.jsonl' ':!*.log'; then
  echo "refusing to start: harness has uncommitted code at $HARNESS_HEAD." \
       "A comparative run pinned to a commit cannot be reproduced from one." >&2
  exit 1
fi
echo "harness pinned at $HARNESS_HEAD for all $((SWEEPS_PER_ARM * 2)) sweeps" \
  | tee -a "$OUT/run-record.txt"

if [ $((SWEEPS_PER_ARM % 2)) -ne 0 ]; then
  echo "[$(date +%H:%M:%S)] WARNING: SWEEPS_PER_ARM=$SWEEPS_PER_ARM is odd -- T leads twice" \
       "and the position term does not cancel. Pre-registered as such." | tee -a "$OUT/run-record.txt"
fi

for n in $(seq 1 "$SWEEPS_PER_ARM"); do
  if [ $((n % 2)) -eq 1 ]; then first=t; first_kv=$KV_T; second=r; second_kv=$KV_R
  else first=r; first_kv=$KV_R; second=t; second_kv=$KV_T; fi
  restart_server "$first" "$first-sweep$n" "$first_kv"
  sweep "$first" "$n"
  restart_server "$second" "$second-sweep$n" "$second_kv"
  sweep "$second" "$n"
done
echo "[$(date +%H:%M:%S)] all $((SWEEPS_PER_ARM * 2)) sweeps complete under $OUT"