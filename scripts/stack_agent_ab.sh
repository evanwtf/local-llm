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
# shellcheck source=lib/ds4_server.sh
source "$HERE/lib/ds4_server.sh"
# shellcheck source=lib/mlx_serve.sh
source "$HERE/lib/mlx_serve.sh"
# shellcheck source=lib/transcript_move.sh
source "$HERE/lib/transcript_move.sh"

# Both arms are overridable, so this harness can answer a question other than
# #138's without a near-copy of it drifting away from the original (the repo
# has already paid for two lists that were supposed to be the same set). The
# defaults ARE #138: change nothing and this runs exactly what it always ran.
#
# NEW_FLAGS/OLD_FLAGS append to the server argv. That is what lets the two arms
# differ by an engine flag rather than by weights -- #210/#151 need MTP on
# against MTP off, same tree, same gguf, same PLE.
NEW_TREE=${NEW_TREE:-$HOME/git/ds4-ivan-qwen38fn}
NEW_GGUF=${NEW_GGUF:-$HOME/models/qwen3.8-flash-next-ds4-q4k-imatrix/Qwen3.8-Flash-Next-Q4KImatrixExperts-MXFP4Down-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf}
NEW_PLE=${NEW_PLE:-$HOME/models/qwen3.8-flash-next-ds4-q4k-imatrix/Qwen3.8-Flash-Next-PLE-Q4_1.gguf}
# A dedicated KV directory. ds4-server's disk cache runs cross-quant=accept, so
# a prefix cached by the other arm would be reused here with activations
# computed from different weights. Separate directories, or the two arms
# quietly contaminate each other. This holds for a flag-only A/B too: ds4
# rejects the other configuration's checkpoints when an engine flag changes the
# KV format, so a shared directory makes one arm re-prefill where the other hit
# cache, and the only symptom is that it looks slower.
NEW_KV=${NEW_KV:-$HOME/.ds4/server-kv-kimat}
NEW_BACKEND=${NEW_BACKEND:-qwen38fnds4kimat}
NEW_FLAGS=${NEW_FLAGS:-}
# Flags for run.py, per arm. The one that matters is --no-require-draft: #210's
# gate refuses a speculative arm that emitted no cycle, which is correct and is
# exactly what this harness must be able to measure on purpose. Passing it is a
# declaration that the arm's silence IS the subject, not an accident.
NEW_RUN_FLAGS=${NEW_RUN_FLAGS:-}
# #191: the arm's ENGINE, not just its flags. Defaults to ds4, so every
# run this script has ever done is unchanged. `mlx-serve` is the only other
# value, and it ignores TREE/GGUF/PLE/KV -- it serves a pulled MLX pack.
NEW_ENGINE=${NEW_ENGINE:-ds4}
NEW_MLX_MODEL=${NEW_MLX_MODEL:-}
NEW_MLX_PORT=${NEW_MLX_PORT:-11234}

OLD_TREE=${OLD_TREE:-$HOME/git/ds4-metal}
OLD_GGUF=${OLD_GGUF:-$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-Q40RoutedExperts-BF16Emb-BF16Control-Q8GDN-Q8QSA-Q8Shared-Q8Out.gguf}
OLD_PLE=${OLD_PLE:-$HOME/models/qwen3.8-flash-next-ds4-q4/Qwen3.8-Flash-Next-PLE-Q4_1.gguf}
OLD_KV=${OLD_KV:-$HOME/.ds4/server-kv}
OLD_BACKEND=${OLD_BACKEND:-qwen38fnds4shim}
OLD_FLAGS=${OLD_FLAGS:-}
OLD_RUN_FLAGS=${OLD_RUN_FLAGS:-}
OLD_ENGINE=${OLD_ENGINE:-ds4}
OLD_MLX_MODEL=${OLD_MLX_MODEL:-}
OLD_MLX_PORT=${OLD_MLX_PORT:-11234}

# These two arms are ds4-only concerns. mlx-serve keeps no disk KV
# directory we manage and sits behind no shim, so a guard written for ds4
# would either refuse a legitimate run or pass while checking nothing.
BOTH_DS4=0
if [ "$NEW_ENGINE" = "ds4" ] && [ "$OLD_ENGINE" = "ds4" ]; then BOTH_DS4=1; fi

if [ "$BOTH_DS4" = "1" ] && [ "$NEW_KV" = "$OLD_KV" ]; then
  echo "REFUSING: both arms share KV dir $NEW_KV -- one arm would read the" \
       "other's checkpoints, or be refused them, and look slower for it" >&2
  exit 1
fi
if [ "$NEW_BACKEND" = "$OLD_BACKEND" ]; then
  echo "REFUSING: both arms name backend $NEW_BACKEND -- the rows would be" \
       "indistinguishable in results.jsonl" >&2
  exit 1
fi

# What identifies this arm's engine build. A source tree has a git HEAD; a
# brew binary has a version string and an mtime. Printing `?` for the second
# kind would read as a failed lookup of the first, which is how a run-record
# stops being evidence.
engine_ident() {
  local engine=$1 tree=$2
  case "$engine" in
  ds4)       echo "$tree @ $(git -C "$tree" rev-parse --short HEAD 2>/dev/null || echo ?)" ;;
  mlx-serve) echo "$(command -v mlx-serve) @ $(mlx-serve --version 2>&1 | grep -m1 mlx-serve || echo ?)" ;;
  *)         echo "$engine (unknown kind)" ;;
  esac
}

mkdir -p "$OUT"

if [ "$NEW_ENGINE" = "ds4" ] || [ "$OLD_ENGINE" = "ds4" ]; then
  if ! pgrep -f qwen_tool_shim >/dev/null; then
    echo "REFUSING: ds4_qwen_tool_shim.py is not running on :8101" >&2
    exit 1
  fi
fi
# The published rows were all taken with the strip on. An inherited
# SHIM_NO_STRIP would make this a different experiment without saying so.
if [ -n "${SHIM_NO_STRIP:-}" ]; then
  echo "REFUSING: SHIM_NO_STRIP is set; every published row has the strip on (#112)" >&2
  exit 1
fi

ds4_files=()
[ "$NEW_ENGINE" = "ds4" ] && ds4_files+=("$NEW_GGUF" "$NEW_PLE")
[ "$OLD_ENGINE" = "ds4" ] && ds4_files+=("$OLD_GGUF" "$OLD_PLE")
for f in ${ds4_files+"${ds4_files[@]}"}; do
  [ -e "$f" ] || { echo "REFUSING: missing $f" >&2; exit 1; }
done

# An mlx-serve arm needs its pack on disk and the binary installed. Same
# refusal, different evidence: a half-pulled pack is void condition 11.
for pair in "$NEW_ENGINE:$NEW_MLX_MODEL" "$OLD_ENGINE:$OLD_MLX_MODEL"; do
  [ "${pair%%:*}" = "mlx-serve" ] || continue
  d=${pair#*:}
  [ -n "$d" ] || { echo "REFUSING: mlx-serve arm has no MLX_MODEL set" >&2; exit 1; }
  [ -d "$d" ] || { echo "REFUSING: missing mlx pack dir $d" >&2; exit 1; }
  [ -e "$d/config.json" ] || { echo "REFUSING: $d has no config.json" >&2; exit 1; }
  command -v mlx-serve >/dev/null || { echo "REFUSING: mlx-serve not installed" >&2; exit 1; }
done

{
  echo "# stack agent A/B, started $(date '+%Y-%m-%dT%H:%M:%S %Z')"
  echo "# SCREEN, not a superiority test: n=$((SWEEPS * 15))/arm resolves ~18-27 pp pass, ~17-26% paired wall."
  echo "NEW backend=$NEW_BACKEND engine=$NEW_ENGINE $(engine_ident "$NEW_ENGINE" "$NEW_TREE")"
  if [ "$NEW_ENGINE" = "mlx-serve" ]; then
    echo "NEW pack=$NEW_MLX_MODEL ($(du -sk "$NEW_MLX_MODEL" 2>/dev/null | cut -f1) KiB)  port=$NEW_MLX_PORT"
  else
    echo "NEW gguf=$(basename "$NEW_GGUF") ($(stat -Lf %z "$NEW_GGUF") bytes, $(readlink "$NEW_GGUF" || basename "$NEW_GGUF"))  kv=$NEW_KV"
  fi
  echo "NEW flags=${NEW_FLAGS:-<none>}  run.py=${NEW_RUN_FLAGS:-<none>}"
  echo "OLD backend=$OLD_BACKEND engine=$OLD_ENGINE $(engine_ident "$OLD_ENGINE" "$OLD_TREE")"
  if [ "$OLD_ENGINE" = "mlx-serve" ]; then
    echo "OLD pack=$OLD_MLX_MODEL ($(du -sk "$OLD_MLX_MODEL" 2>/dev/null | cut -f1) KiB)  port=$OLD_MLX_PORT"
  else
    echo "OLD gguf=$(basename "$OLD_GGUF") ($(stat -Lf %z "$OLD_GGUF") bytes, $(readlink "$OLD_GGUF" || basename "$OLD_GGUF"))  kv=$OLD_KV"
  fi
  echo "OLD flags=${OLD_FLAGS:-<none>}  run.py=${OLD_RUN_FLAGS:-<none>}"
  if [ "$NEW_ENGINE" != "$OLD_ENGINE" ]; then
    echo "# DIFFERENT ENGINES and different weight formats. Engine and quant move"
    echo "# together; neither can be attributed alone (#138, #191). This is a"
    echo "# stack comparison, and must be reported as one."
  elif [ "$NEW_GGUF" = "$OLD_GGUF" ] && [ "$NEW_TREE" = "$OLD_TREE" ]; then
    echo "# Same tree and same gguf in both arms: the flags above are the only variable."
  else
    echo "# engine and quant move together in both arms; neither can be attributed alone (#138)."
  fi
} > "$OUT/run-record.txt"

# Whichever engine this arm names. BOTH engines are stopped first, not just
# the one about to start: the previous sweep may have been the other arm, and
# two ~100 GiB servers do not fit on this machine at once. Stopping one that
# is not running is a no-op.
restart_server() {
  local engine=$1 tree=$2 gguf=$3 ple=$4 kv=$5 tag=$6 flags=${7:-} \
        mlx_model=${8:-} mlx_port=${9:-11234}
  ds4_stop_server "for $tag" || exit 1
  mlx_serve_stop_server "for $tag" || exit 1
  echo "[$(date +%H:%M:%S)] starting $tag on $engine${flags:+ with $flags}..."
  case "$engine" in
  ds4)
    # shellcheck disable=SC2086  # flags is a deliberate word-split argv fragment
    ( cd "$tree" && ./ds4-server --metal -m "$gguf" --ple "$ple" \
        --ctx 100000 --warm-weights $flags \
        --kv-disk-dir "$kv" --kv-disk-space-mb 8192 \
        --host 127.0.0.1 --port 8000 > "$OUT/server-$tag.log" 2>&1 & )
    ( cd "$REPO" && uv run python benchmarks/agent/wait_ready.py \
        --base-url http://127.0.0.1:8000 --model qwen3.8-flash-next-q4 | tail -1 )
    # #149: both #138 arms auto-enabled the Metal 4 tensor route on M5, and
    # that was established by hand after the fact. The server has just written
    # which route it took to its own log; record it so the rows can say so.
    ds4_record_route "$OUT/server-$tag.log" 8000
    ;;
  mlx-serve)
    # --serve, not `run`: `run` is the interactive chat REPL and never
    # returns. --host 127.0.0.1 explicitly, because the documented default is
    # 0.0.0.0 and a benchmark server does not belong on the local network.
    # No route recording: the Metal route is a ds4 concept, and a row for this
    # arm must read `unrecorded` rather than inherit ds4's provenance (#149).
    # shellcheck disable=SC2086
    ( mlx-serve --model "$mlx_model" --serve \
        --host 127.0.0.1 --port "$mlx_port" \
        --ctx-size 100000 $flags > "$OUT/server-$tag.log" 2>&1 & )
    ( cd "$REPO" && uv run python benchmarks/agent/wait_ready.py \
        --base-url "http://127.0.0.1:$mlx_port" | tail -1 )
    ;;
  *)
    echo "REFUSING: unknown engine '$engine' for $tag (ds4|mlx-serve)" >&2
    exit 1
    ;;
  esac
}

sweep() {
  local arm=$1 n=$2 backend=$3 run_flags=${4:-} engine=${5:-ds4}
  local tag="${arm}-sweep${n}"
  # Capture the START. sweep-order.txt used to carry one time, written here at
  # the END, while stack_agent_report read it as the sweep's START and gave
  # each sweep [start, next start). Every window therefore held the NEXT
  # sweep's rows: on the 2026-09-05 re-run, 45 of 60 rows fit no window and the
  # old-arm control read 14/30 against a true 27/30. Write both, so the file
  # says which is which.
  local started
  started=$(date '+%H:%M:%S')
  # Stamp a marker to this sweep's instant. The transcript move filters by
  # mtime, so it needs date+time, not the time-of-day that `started` passes to
  # sweep-order -- and it needs a stamped file (`find -newer`), not a date
  # string, whose BSD and GNU `touch -t` parsers disagree. This sweep's
  # transcripts appear after this instant; anything older in the shared log dir
  # is a leftover from a killed run and must not be swept in.
  local move_marker
  move_marker="$OUT/.transcript-start"
  : > "$move_marker"
  touch -t "$(date '+%Y%m%d%H%M.%S')" "$move_marker"
  echo "[$(date +%H:%M:%S)] === $tag ($backend) ==="
  # #210: without --server-log the row carries no `draft` field at all, and an
  # MTP arm that never speculated is then indistinguishable from one that did.
  # The log is this sweep's own server, started moments ago, so the probe's
  # byte window covers exactly this sweep.
  # shellcheck disable=SC2086  # run_flags is a deliberate argv fragment
  ( cd "$REPO" && uv run python benchmarks/agent/run.py \
      --backend "$backend" --trials 1 --client opencode --no-lock \
      --require-harness-head "$HARNESS_HEAD" \
      --server-log "$OUT/server-$tag.log" --draft-log-engine "$engine" $run_flags \
      > "$OUT/$tag.log" 2>&1 ) || echo "[$(date +%H:%M:%S)] $tag returned non-zero"
  # Transcripts move out of the top level immediately, but only ones written
  # after this sweep started. Leaving stale ones is how #112's pre-remedy
  # evidence was destroyed -- later sweeps write the same filenames, and a run
  # killed before ITS move ran leaves transcripts the next same-arm sweep would
  # wrongly claim. save_transcript() no longer overwrites, and the per-sweep
  # directory is what makes the rows attributable at all; the mtime filter is
  # what keeps a leftover out of a sweep it was not part of.
  move_transcripts_since "$BENCH_LOGS" "$OUT" "$tag" "$move_marker" "$backend"
  echo "[$(date +%H:%M:%S)] $tag done, $(ls "$OUT/$tag" 2>/dev/null | wc -l | tr -d ' ') transcripts"
  echo "$tag $started $(date '+%H:%M:%S')" >> "$OUT/sweep-order.txt"
}

# Alternate which arm goes first. Running new-then-old every sweep puts the old
# arm second on a hotter machine every single time, which is exactly the #130
# bias this script's own header warns about -- and is what it did until
# 2026-09-04. decode_ab.sh has alternated per rep since #130; this did not.
#
# With an even SWEEPS each arm leads half the time and the thermal term cancels
# in the pairing. With an odd SWEEPS it does not, so say so rather than let a
# reader assume it balances.
# Pin the harness for the whole comparison. On 2026-09-04 the four sweeps of
# this script's own A/B recorded FOUR different harness_head values, because
# the harness was being committed to from the checkout the batch ran from --
# new-sweep1 at 563e94b against old-sweep1 at 19958b1. The two arms were not
# running the same code, which voids the comparison whatever the stacks did.
HARNESS_HEAD=$(git -C "$REPO" rev-parse --short HEAD)
if ! git -C "$REPO" diff --quiet -- ':!*.jsonl' ':!*.log' || \
   ! git -C "$REPO" diff --cached --quiet -- ':!*.jsonl' ':!*.log'; then
  echo "refusing to start: harness has uncommitted code at $HARNESS_HEAD." \
       "A comparative run pinned to a commit cannot be reproduced from one." >&2
  exit 1
fi
echo "harness pinned at $HARNESS_HEAD for all $((SWEEPS * 2)) sweeps" \
  | tee -a "$OUT/run-record.txt"

# #145: stop the server on EVERY exit path, not just the last line of the
# script. Armed here rather than at the top, so a run that refuses to start --
# no shim, dirty harness, missing gguf -- does not tear down a server it never
# owned and somebody else may be using.
ds4_arm_stop_trap
mlx_serve_arm_stop_trap

if [ $((SWEEPS % 2)) -ne 0 ]; then
  echo "[$(date +%H:%M:%S)] WARNING: SWEEPS=$SWEEPS is odd -- one arm leads once" \
       "more than the other and the position term does not cancel. Prefer an" \
       "even SWEEPS." | tee -a "$OUT/run-record.txt"
fi

for n in $(seq 1 "$SWEEPS"); do
  if [ $((n % 2)) -eq 1 ]; then
    first_tag=new; first_backend=$NEW_BACKEND; first_run_flags=$NEW_RUN_FLAGS
    first_tree=$NEW_TREE; first_gguf=$NEW_GGUF; first_ple=$NEW_PLE; first_kv=$NEW_KV; first_flags=$NEW_FLAGS
    first_engine=$NEW_ENGINE; first_mlx=$NEW_MLX_MODEL; first_mlx_port=$NEW_MLX_PORT
    second_tag=old; second_backend=$OLD_BACKEND; second_run_flags=$OLD_RUN_FLAGS
    second_tree=$OLD_TREE; second_gguf=$OLD_GGUF; second_ple=$OLD_PLE; second_kv=$OLD_KV; second_flags=$OLD_FLAGS
    second_engine=$OLD_ENGINE; second_mlx=$OLD_MLX_MODEL; second_mlx_port=$OLD_MLX_PORT
  else
    first_tag=old; first_backend=$OLD_BACKEND; first_run_flags=$OLD_RUN_FLAGS
    first_tree=$OLD_TREE; first_gguf=$OLD_GGUF; first_ple=$OLD_PLE; first_kv=$OLD_KV; first_flags=$OLD_FLAGS
    first_engine=$OLD_ENGINE; first_mlx=$OLD_MLX_MODEL; first_mlx_port=$OLD_MLX_PORT
    second_tag=new; second_backend=$NEW_BACKEND; second_run_flags=$NEW_RUN_FLAGS
    second_tree=$NEW_TREE; second_gguf=$NEW_GGUF; second_ple=$NEW_PLE; second_kv=$NEW_KV; second_flags=$NEW_FLAGS
    second_engine=$NEW_ENGINE; second_mlx=$NEW_MLX_MODEL; second_mlx_port=$NEW_MLX_PORT
  fi
  restart_server "$first_engine" "$first_tree" "$first_gguf" "$first_ple" "$first_kv" \
    "$first_tag-sweep$n" "$first_flags" "$first_mlx" "$first_mlx_port"
  sweep "$first_tag" "$n" "$first_backend" "$first_run_flags" "$first_engine"
  restart_server "$second_engine" "$second_tree" "$second_gguf" "$second_ple" "$second_kv" \
    "$second_tag-sweep$n" "$second_flags" "$second_mlx" "$second_mlx_port"
  sweep "$second_tag" "$n" "$second_backend" "$second_run_flags" "$second_engine"
done
echo "[$(date +%H:%M:%S)] all $((SWEEPS * 2)) sweeps complete under $OUT"
# The teardown itself is the EXIT trap's job -- see ds4_arm_stop_trap above.
# Until 2026-09-06 this line was the end of the script and the last arm's
# server stayed resident, holding 97.9 GiB, on four consecutive clean runs.
