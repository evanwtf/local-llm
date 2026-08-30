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

mkdir -p "$OUT"
for rep in $(seq 1 "$REPS"); do
  for pair in "$LABEL_A:$GGUF_A" "$LABEL_B:$GGUF_B"; do
    label=${pair%%:*}; gguf=${pair#*:}
    csv="$OUT/${label}-rep${rep}.csv"
    echo "[$(date +%H:%M:%S)] $label rep $rep -> $csv"
    "$DS4/ds4-bench" -m "$gguf" --metal \
      --prompt-file "$PROMPT" \
      --ctx-start "$CTX_START" --ctx-max "$CTX_MAX" --step-incr "$STEP" \
      --gen-tokens "$GEN" --csv "$csv"
  done
done
echo "done: $OUT"
