#!/usr/bin/env bash
# Paired decode-rate A/B for two ENGINE BUILDS of the same GGUF (#118).
#
# decode_ab.sh varies the weights; this varies the tree. It is the reproduction
# shape of ds4 PR #964, which measured main / branch / branch / main with each
# side built in its own worktree so each reads its own metal/*.metal at
# runtime -- two builds in one tree would silently share shaders. The arm
# order alternates between repetitions (#130's rule: odd reps run A-B, even
# reps run B-A, so drift divides across both arms instead of always landing
# on B). ds4 PR #964 itself ran main/branch/branch/main for this reason.
#
# Usage: scripts/decode_ab_engine.sh <label-a> <tree-a> <label-b> <tree-b> <gguf> [outdir]
set -euo pipefail

LABEL_A=${1:?label A}; TREE_A=${2:?ds4 tree A}
LABEL_B=${3:?label B}; TREE_B=${4:?ds4 tree B}
GGUF=${5:?gguf}
OUT=${6:-$HOME/git/local-llm/benchmarks/ds4/decode-ab-964}

# The corpus comes from tree A for both arms, so it is byte-identical across
# the A/B even if the branch touches speed-bench/.
PROMPT=${PROMPT:-$TREE_A/speed-bench/promessi_sposi.txt}

# Same frontiers and gen budget as decode_ab.sh and ds4's own speed-bench, so
# the numbers stay comparable to speed-bench/m5_max.csv and to #91's #621 A/B.
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
if ! uv run python "$PREFLIGHT" --acquire-lock "decode_ab_engine.sh $LABEL_A vs $LABEL_B" --owner-pid $$; then
  echo "refusing to start: the machine is claimed by another run" >&2
  exit 1
fi
trap 'uv run python "$PREFLIGHT" --release-lock --owner-pid $$ >/dev/null 2>&1' EXIT

mkdir -p "$OUT"
for rep in $(seq 1 "$REPS"); do
  if [ $((rep % 2)) -eq 0 ]; then
    order=("$LABEL_B:$TREE_B" "$LABEL_A:$TREE_A")
  else
    order=("$LABEL_A:$TREE_A" "$LABEL_B:$TREE_B")
  fi
  for pair in "${order[@]}"; do
    label=${pair%%:*}; tree=${pair#*:}
    csv="$OUT/${label}-rep${rep}.csv"
    echo "[$(date +%H:%M:%S)] $label rep $rep -> $csv"
    ( cd "$tree" && ./ds4-bench -m "$GGUF" --metal \
      --prompt-file "$PROMPT" \
      --ctx-start "$CTX_START" --ctx-max "$CTX_MAX" --step-incr "$STEP" \
      --gen-tokens "$GEN" --csv "$csv" )
  done
done
echo "done: $OUT"