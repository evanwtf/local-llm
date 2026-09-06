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
# Refuse before taking the lock, not minutes into it. A missing build used to
# fail mid-run: the lock was already held, the model had been loaded, and the
# first ds4-bench invocation died on "no such file". Check the cheap thing
# first.
for tree in "$TREE_A" "$TREE_B"; do
  if [ ! -x "$tree/ds4-bench" ]; then
    echo "REFUSING: $tree/ds4-bench is missing or not executable -- build it first" >&2
    exit 1
  fi
done

PREFLIGHT="$(dirname "$0")/../benchmarks/agent/preflight.py"
if ! uv run python "$PREFLIGHT" --acquire-lock "decode_ab_engine.sh $LABEL_A vs $LABEL_B" --owner-pid $$; then
  echo "refusing to start: the machine is claimed by another run" >&2
  exit 1
fi
trap 'uv run python "$PREFLIGHT" --release-lock --owner-pid $$ >/dev/null 2>&1' EXIT

mkdir -p "$OUT"
uv run python "$(dirname "$0")/prompt_meta.py" --prompt "$PROMPT" --sidecar "$OUT" --show

# Stamp both trees' commits beside the CSVs. #118's arm shas (b0a147a and
# 8969dbb) live only in issue prose, so re-running it a month later means
# trusting a sentence. An engine A/B whose rows cannot say which commits
# produced them is the same gap #137 found on the client side and #138 found
# on the engine side.
{
  echo "# engine A/B, $(date '+%Y-%m-%dT%H:%M:%S %Z')"
  echo "A label=$LABEL_A tree=$TREE_A @ $(git -C "$TREE_A" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "B label=$LABEL_B tree=$TREE_B @ $(git -C "$TREE_B" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "gguf=$GGUF"
  echo "prompt=$PROMPT"
} >> "$OUT/engines.txt"

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
    # #140: the prompt is an input to the prefill result, so it goes on the
    # rows. Stamped per CSV rather than at the end, so a run that dies
    # halfway still says what it measured.
    uv run python "$(dirname "$0")/prompt_meta.py" --prompt "$PROMPT" --stamp "$csv"
  done
done
echo "done: $OUT"