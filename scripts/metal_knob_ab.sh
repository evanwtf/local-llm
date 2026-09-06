#!/usr/bin/env bash
# Paired decode-rate A/B for a Metal knob env var within one tree (#162 Task 4).
#
# decode_ab.sh varies the weights, decode_ab_engine.sh varies the tree, and
# nothing varies the environment within one tree. This driver varies one Metal
# knob env var between two arms of the same tree and GGUF.
#
# Each knob has two env vars: the var that turns it on and the var that turns
# it off. For the three opt-in knobs (default off) they are the same variable,
# set to a nonzero value to enable and =0 to disable. For exact-rows they are
# not: the persistent cache is on by default, so REQUIRE=0 means "not required"
# and leaves the cache running. Its off arm is the DISABLE var, which the
# source names "the A/B rollback arm and always wins" (ds4_metal.m:14827). The
# knob table and the refusal paths live in metal_knob_ab.py, so a knob whose
# off arm does not change the default is refused before the lock or any
# measurement.
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
ON_VAR="$(uv run python "$PY" on-var "$KNOB")"
OFF_VAR="$(uv run python "$PY" off-var "$KNOB")"

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

# The knob name, both arm vars, and both arm values go on the run, so a later
# reader can tell which knob and which values produced the rows.
cat > "$OUT/run-meta.json" <<EOF
{
  "knob": "$KNOB",
  "on_var": "$ON_VAR",
  "off_var": "$OFF_VAR",
  "on_value": "$ON_VALUE",
  "off_value": "$OFF_VALUE",
  "tree": "$TREE",
  "gguf": "$GGUF",
  "reps": "$REPS",
  "started": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

run_arm() {
  local label="$1" var="$2" value="$3" rep="$4" position="$5"
  local csv="$OUT/${label}-rep${rep}.csv"
  local log="$OUT/${label}-rep${rep}.log"
  echo "[$(date +%H:%M:%S)] $label rep $rep (position $position) -> $csv"
  # ds4-bench resolves metal/*.metal relative to its own tree, so run from
  # there. The env var is set for the bench process only.
  ( cd "$TREE" && env "$var=$value" ./ds4-bench -m "$GGUF" --metal \
      --prompt-file "$PROMPT" \
      --ctx-start "$CTX_START" --ctx-max "$CTX_MAX" --step-incr "$STEP" \
      --gen-tokens "$GEN" --csv "$csv" ) > "$log" 2>&1
  # On arm: the REQUIRE spelling must not fail closed. Absence of the error is
  # the only admission signal, and it must be checked, not assumed.
  if [ "$label" = "on" ] && uv run python "$PY" check-fail-closed "$KNOB" "$log"; then
    echo "REFUSING: $label failed closed (knob $KNOB did not take effect)" >&2
    exit 1
  fi
  uv run python "$(dirname "$0")/prompt_meta.py" --prompt "$PROMPT" --stamp "$csv"
}

for rep in $(seq 1 "$REPS"); do
  # #130: alternate which arm runs first. Odd reps on-off, even reps off-on,
  # so drift divides across both arms instead of always landing on one.
  if [ $((rep % 2)) -eq 0 ]; then
    order=("off" "on")
  else
    order=("on" "off")
  fi
  position=0
  for label in "${order[@]}"; do
    position=$((position + 1))
    if [ "$label" = "on" ]; then
      var="$ON_VAR"; value="$ON_VALUE"
    else
      var="$OFF_VAR"; value="$OFF_VALUE"
    fi
    echo "rep=$rep position=$position of 2 label=$label" >> "$OUT/run-order.txt"
    run_arm "$label" "$var" "$value" "$rep" "$position"
  done
done
echo "done: $OUT"
