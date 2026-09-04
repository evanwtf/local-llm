#!/usr/bin/env bash
# Paired decode A/B for two whole STACKS -- engine tree + weights together.
#
# Written for #138, where the clean experiment turned out to be impossible.
# Ivan's Q4_K-imatrix rebuild of Qwen3.8-Flash-Next needs the `qwen4exp-schema`
# structure and its own engine branch; the Q4_0 build our 142 rows were taken
# on needs the engine we already run. Measured 2026-09-04:
#
#   ds4-metal ba01f5d          old Q4_0 loads      new Q4_K "deepseek4.block_count missing"
#   ivan qwen3.8-flash-next    old Q4_0 same error new Q4_K loads, coherent
#
# So no binary loads both, and `decode_ab.sh` (two GGUFs, one engine) and
# `decode_ab_engine.sh` (two engines, one GGUF) can neither of them express
# this comparison. This script is the honest third shape: each arm carries its
# own engine, weights and PLE sidecar.
#
# **The result is a two-variable comparison and must always be reported as
# one.** The quant and the engine move together and nothing here can separate
# them. That is not a flaw in the method -- it is the actual state of the
# artifacts, and the alternative is a one-variable claim that is false.
#
# Both Qwen3.8-Flash-Next ds4 builds keep the 51B-value PLE n-gram table in an
# external sidecar, so every arm needs `--ple`. `ds4-bench` accepts it; the
# flag is real but undocumented, absent from `--help` and present in the
# parser at ds4_bench.c:275. Passing no sidecar fails with "required tensor is
# missing: per_layer_token_embd.weight", which reads exactly like the flag not
# existing. It does exist.
#
# Usage:
#   scripts/decode_ab_stack.sh <label-a> <tree-a> <gguf-a> <ple-a> \
#                              <label-b> <tree-b> <gguf-b> <ple-b> [outdir]
#
# Pass "-" for a PLE sidecar an arm does not use.
set -euo pipefail

LABEL_A=${1:?label A}; TREE_A=${2:?engine tree A}; GGUF_A=${3:?gguf A}; PLE_A=${4:?ple A or -}
LABEL_B=${5:?label B}; TREE_B=${6:?engine tree B}; GGUF_B=${7:?gguf B}; PLE_B=${8:?ple B or -}
OUT=${9:-$HOME/git/local-llm/benchmarks/ds4/stack-ab}

# The prompt is an input to the prefill result, not a detail (#140). Both arms
# must use the same one, and it is stamped onto every row below.
PROMPT=${PROMPT:-$HOME/git/ds4/speed-bench/promessi_sposi.txt}

CTX_START=${CTX_START:-2048}
CTX_MAX=${CTX_MAX:-16384}
STEP=${STEP:-2048}
GEN=${GEN:-128}
REPS=${REPS:-3}

HERE="$(cd "$(dirname "$0")" && pwd)"

# #133: claim the machine before loading anything. This script spends minutes
# between arms with nothing running, and a scan in that window would truthfully
# report "all clear" while the machine is committed for hours.
PREFLIGHT="$HERE/../benchmarks/agent/preflight.py"
if ! uv run python "$PREFLIGHT" --acquire-lock "decode_ab_stack.sh $LABEL_A vs $LABEL_B" --owner-pid $$; then
  echo "refusing to start: the machine is claimed by another run" >&2
  exit 1
fi
trap 'uv run python "$PREFLIGHT" --release-lock --owner-pid $$ >/dev/null 2>&1' EXIT

mkdir -p "$OUT"
uv run python "$HERE/prompt_meta.py" --prompt "$PROMPT" --sidecar "$OUT" --show

# Record what each arm actually is. A stack A/B whose rows do not say which
# engine produced them is unreadable a week later, and this is the one shape
# where the engine is part of the arm rather than held constant.
{
  echo "# stack A/B, $(date '+%Y-%m-%dT%H:%M:%S %Z')"
  for arm in "A:$LABEL_A:$TREE_A:$GGUF_A:$PLE_A" "B:$LABEL_B:$TREE_B:$GGUF_B:$PLE_B"; do
    IFS=: read -r slot label tree gguf ple <<< "$arm"
    echo "$slot label=$label"
    echo "$slot engine=$tree @ $(git -C "$tree" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "$slot gguf=$gguf ($(stat -f %z "$gguf" 2>/dev/null || echo ?) bytes)"
    echo "$slot ple=$ple"
  done
  echo "# TWO VARIABLES: engine and weights move together. Report as a stack"
  echo "# comparison; neither half can be attributed on its own (#138)."
} >> "$OUT/stacks.txt"

for rep in $(seq 1 "$REPS"); do
  # #130: alternate arm order. Throughput declines across a measurement
  # window, so a fixed order penalises whichever arm always runs second --
  # a bias measured upstream as larger than three of the four effects being
  # compared.
  if [ $((rep % 2)) -eq 0 ]; then
    order=("B" "A")
  else
    order=("A" "B")
  fi
  position=0
  for slot in "${order[@]}"; do
    position=$((position + 1))
    if [ "$slot" = "A" ]; then
      label=$LABEL_A; tree=$TREE_A; gguf=$GGUF_A; ple=$PLE_A
    else
      label=$LABEL_B; tree=$TREE_B; gguf=$GGUF_B; ple=$PLE_B
    fi
    csv="$OUT/${label}-rep${rep}.csv"
    echo "rep=$rep position=$position of 2 label=$label" >> "$OUT/run-order.txt"
    echo "[$(date +%H:%M:%S)] $label rep $rep (position $position) -> $csv"
    ple_args=()
    [ "$ple" != "-" ] && ple_args=(--ple "$ple")
    # ds4-bench resolves metal/*.metal relative to its own tree, so run from
    # there; without this it dies with "metal/activations.metal not found".
    ( cd "$tree" && ./ds4-bench -m "$gguf" --metal "${ple_args[@]}" \
      --prompt-file "$PROMPT" \
      --ctx-start "$CTX_START" --ctx-max "$CTX_MAX" --step-incr "$STEP" \
      --gen-tokens "$GEN" --csv "$csv" )
    uv run python "$HERE/prompt_meta.py" --prompt "$PROMPT" --stamp "$csv"
  done
done
echo "done: $OUT"
