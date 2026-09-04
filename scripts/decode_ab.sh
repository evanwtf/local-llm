#!/usr/bin/env bash
# Paired decode-rate A/B for two GGUFs of the same model (#48).
#
# Why a sweep and not three runs: a 3-trial agent median carries +/-28% (#23),
# and the effect we are chasing is ~9.5%. ds4-bench decodes greedily at fixed
# token counts across N context frontiers, so one invocation yields N paired
# points per model -- a far tighter instrument than the agent suite, and it
# measures decode rate directly instead of inferring it from wall time.
#
# Usage: scripts/decode_ab.sh <label-a> <gguf-a> <label-b> <gguf-b> [outdir]
set -euo pipefail

LABEL_A=${1:?label A}; GGUF_A=${2:?gguf A}
LABEL_B=${3:?label B}; GGUF_B=${4:?gguf B}
OUT=${5:-$HOME/git/local-llm/benchmarks/ds4/decode-ab}
DS4=${DS4:-$HOME/git/ds4}
PROMPT=${PROMPT:-$DS4/speed-bench/promessi_sposi.txt}

# Same frontiers and gen budget as ds4's own speed-bench, so our numbers are
# comparable to speed-bench/m5_max.csv rather than a private methodology.
CTX_START=${CTX_START:-2048}
CTX_MAX=${CTX_MAX:-16384}
STEP=${STEP:-2048}
GEN=${GEN:-128}
REPS=${REPS:-3}

# #133: claim the machine before loading anything. preflight sees the process
# table but cannot see intent, and this script spends minutes between arms
# with nothing running -- a scan in that window truthfully says "all clear"
# while the machine is committed for hours. `$$` is this script, whose
# lifetime the lock should track; preflight's own pid exits immediately.
PREFLIGHT="$(dirname "$0")/../benchmarks/agent/preflight.py"
if ! uv run python "$PREFLIGHT" --acquire-lock "decode_ab.sh $LABEL_A vs $LABEL_B" --owner-pid $$; then
  echo "refusing to start: the machine is claimed by another run" >&2
  exit 1
fi
trap 'uv run python "$PREFLIGHT" --release-lock --owner-pid $$ >/dev/null 2>&1' EXIT

mkdir -p "$OUT"
for rep in $(seq 1 "$REPS"); do
  # #130: alternate which arm runs first. Throughput declines across a
  # measurement window, so a fixed order penalises whichever arm always runs
  # second. @adamlawi measured the positional bias on antirez/ds4#952 as
  # larger than three of the four effects being compared -- at one frontier
  # the SIGN of the result depended only on load order. Odd reps run A-B,
  # even reps B-A, so the drift divides between the arms instead of landing
  # on one. decode_ab_engine.sh has done this since it was written; this
  # script predates the finding.
  if [ $((rep % 2)) -eq 0 ]; then
    order=("$LABEL_B:$GGUF_B" "$LABEL_A:$GGUF_A")
  else
    order=("$LABEL_A:$GGUF_A" "$LABEL_B:$GGUF_B")
  fi
  position=0
  for pair in "${order[@]}"; do
    position=$((position + 1))
    label=${pair%%:*}; gguf=${pair#*:}
    csv="$OUT/${label}-rep${rep}.csv"
    # Record the order this arm ran in, so a later reader can test for
    # positional bias instead of assuming it away (#130 item 3).
    echo "rep=$rep position=$position of 2 label=$label" >> "$OUT/run-order.txt"
    echo "[$(date +%H:%M:%S)] $label rep $rep (position $position) -> $csv"
    # ds4-bench resolves metal/*.metal relative to its own tree, so run from
    # there. Without this it dies with "metal/activations.metal not found".
    ( cd "$DS4" && ./ds4-bench -m "$gguf" --metal \
      --prompt-file "$PROMPT" \
      --ctx-start "$CTX_START" --ctx-max "$CTX_MAX" --step-incr "$STEP" \
      --gen-tokens "$GEN" --csv "$csv" )
  done
done
echo "done: $OUT"
