#!/usr/bin/env bash
# Paired decode-rate A/B for a Metal knob env var within one tree (#162 Task 4).
#
# decode_ab.sh varies the weights, decode_ab_engine.sh varies the tree, and
# nothing varies the environment within one tree. This driver varies one Metal
# knob env var between two arms of the same tree and GGUF.
#
# The off arm sets the knob to =0. All four knobs are value-parsed (per-helper
# evidence in evidence/0162-metal-knobs.json), so =0 genuinely disables;
# unsetting would select each knob's default, which is not the same arm. The
# three helpers disagree on the empty string, so an empty value is refused.
#
# The on arm uses the REQUIRE spelling and fails the run if the fail-closed
# error string appears. There is no positive admission print, so absence of
# the error is the only admission signal and it must be checked, not assumed.
#
# Usage: scripts/metal_knob_ab.sh <knob> <on-value> <off-value> <tree> <gguf> [outdir]
set -euo pipefail

KNOB=${1:?knob}; ON_VALUE=${2:?on value}; OFF_VALUE=${3:?off value}
TREE=${4:?ds4 tree}; GGUF=${5:?gguf}
OUT=${6:-$HOME/git/local-llm/benchmarks/ds4/metal-knob-ab}

# Same frontiers and gen budget as decode_ab.sh and ds4's own speed-bench, so
# the numbers stay comparable to speed-bench/m5_max.csv.
CTX_START=${CTX_START:-2048}
CTX_MAX=${CTX_MAX:-16384}
STEP=${STEP:-2048}
GEN=${GEN:-128}
REPS=${REPS:-3}
PROMPT=${PROMPT:-$TREE/speed-bench/promessi_sposi.txt}

PY="$(dirname "$0")/metal_knob_ab.py"

# Refuse a wrong arm before the lock or any measurement. The negative cases
# are the whole job: an empty value is a wrong arm waiting to happen, and an
# unknown knob is a typo that would otherwise run a different experiment.
uv run python "$PY" validate "$KNOB" "$ON_VALUE" "$OFF_VALUE"
ENV_VAR="$(uv run python "$PY" env "$KNOB")"

# Check the cheap thing first: a missing build used to fail mid-run, after the
# lock was held and the model loaded.
if [ ! -x "$TREE/ds4-bench" ]; then
  echo "REFUSING: $TREE/ds4-bench is missing or not executable -- build it first" >&2
  exit 1
fi

# #133: claim the machine before loading anything. preflight sees the process
# table but cannot see intent, and this script spends minutes between arms
# with nothing running -- a scan in that window truthfully says "all clear"
# while the machine is committed for hours. `$$` is this script, whose
# lifetime the lock should track; preflight's own pid exits immediately.
PREFLIGHT="$(dirname "$0")/../benchmarks/agent/preflight.py"
if ! uv run python "$PREFLIGHT" --acquire-lock "metal_knob_ab.sh $KNOB" --owner-pid $$; then
  echo "refusing to start: the machine is claimed by another run" >&2
  exit 1
fi
trap 'uv run python "$PREFLIGHT" --release-lock --owner-pid $$ >/dev/null 2>&1' EXIT

mkdir -p "$OUT"
uv run python "$(dirname "$0")/prompt_meta.py" --prompt "$PROMPT" --sidecar "$OUT" --show

# The knob name and both arm values go on the run, so a later reader can tell
# which knob and which values produced the rows.
cat > "$OUT/run-meta.json" <<EOF
{
  "knob": "$KNOB",
  "env": "$ENV_VAR",
  "on_value": "$ON_VALUE",
  "off_value": "$OFF_VALUE",
  "tree": "$TREE",
  "gguf": "$GGUF",
  "reps": "$REPS",
  "started": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

run_arm() {
  local label="$1" value="$2" rep="$3" position="$4"
  local csv="$OUT/${label}-rep${rep}.csv"
  local log="$OUT/${label}-rep${rep}.log"
  echo "[$(date +%H:%M:%S)] $label rep $rep (position $position) -> $csv"
  # ds4-bench resolves metal/*.metal relative to its own tree, so run from
  # there. The env var is set for the bench process only.
  ( cd "$TREE" && env "$ENV_VAR=$value" ./ds4-bench -m "$GGUF" --metal \
      --prompt-file "$PROMPT" \
      --ctx-start "$CTX_START" --ctx-max "$CTX_MAX" --step-incr "$STEP" \
      --gen-tokens "$GEN" --csv "$csv" ) > "$log" 2>&1
  # On arm: the REQUIRE spelling must not fail closed. Absence of the error is
  # the only admission signal, and it must be checked, not assumed.
  if [ "$value" != "0" ] && uv run python "$PY" check-fail-closed "$KNOB" "$log"; then
    echo "REFUSING: $label failed closed (knob $KNOB did not take effect)" >&2
    exit 1
  fi
  uv run python "$(dirname "$0")/prompt_meta.py" --prompt "$PROMPT" --stamp "$csv"
}

for rep in $(seq 1 "$REPS"); do
  # #130: alternate which arm runs first. Odd reps on-off, even reps off-on,
  # so drift divides across both arms instead of always landing on one.
  if [ $((rep % 2)) -eq 0 ]; then
    order=("off:$OFF_VALUE" "on:$ON_VALUE")
  else
    order=("on:$ON_VALUE" "off:$OFF_VALUE")
  fi
  position=0
  for pair in "${order[@]}"; do
    position=$((position + 1))
    label=${pair%%:*}; value=${pair#*:}
    echo "rep=$rep position=$position of 2 label=$label" >> "$OUT/run-order.txt"
    run_arm "$label" "$value" "$rep" "$position"
  done
done
echo "done: $OUT"
