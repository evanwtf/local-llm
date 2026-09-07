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
# source names "the A/B rollback arm and always wins"
# (ds4_metal.m:14828 at ds4-pr952 77a054e1). The
# knob table and the refusal paths live in metal_knob_ab.py, so a knob whose
# off arm does not change the default is refused before the lock or any
# measurement.
#
# The on arm uses the REQUIRE spelling and fails the run if the fail-closed
# error string appears. There is no positive admission print, so absence of
# the error is the only admission signal and it must be checked, not assumed.
# stream-overlap has no REQUIRE spelling, so it has no admission signal; the
# driver refuses it unless METAL_KNOB_ACK_NO_SIGNAL=1, and its rows are marked
# admission_signal: none so they cannot later be read as verified.
#
# gathered-heads has no REQUIRE spelling, so it cannot fail closed. Instead it
# carries a count-based admission signal: the driver runs one short
# single-frontier engagement pass per arm with the trace var set, counts the
# `packed FA use=` trace lines, and refuses the timed run unless the on arm
# engaged more layers than the off arm and both are non-zero. The counts go on
# the run so every run carries its own engagement evidence.
#
# A presence knob (gathered-heads) is on by default and has no REQUIRE spelling:
# the on arm unsets the DISABLE var (`env -u`), so its on-value is the sentinel
# "unset" (the `${2:?on value}` guard needs a non-empty positional). The off arm
# sets the DISABLE var to a nonzero value.
#
# Usage: scripts/metal_knob_ab.sh <knob> <on-value> <off-value> <tree> <gguf> [outdir]
set -euo pipefail

KNOB=${1:?knob}; ON_VALUE=${2:?on value}; OFF_VALUE=${3:?off value}
TREE=${4:?ds4 tree}; GGUF=${5:?gguf}
OUT=${6:-$HOME/git/local-llm/benchmarks/ds4/metal-knob-ab}
# Explicit acknowledgment to measure a knob with no admission signal. Without
# it validate() refuses, so a knob that cannot be verified is never measured
# by accident.
ACK_NO_SIGNAL="${METAL_KNOB_ACK_NO_SIGNAL:-0}"

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
# are the whole job: an empty value is a wrong arm waiting to happen, an
# unknown knob is a typo that would otherwise run a different experiment, and
# a knob with no admission signal is refused unless explicitly acknowledged.
if [ "$ACK_NO_SIGNAL" -eq 1 ]; then
  uv run python "$PY" validate "$KNOB" "$ON_VALUE" "$OFF_VALUE" --ack-no-signal
else
  uv run python "$PY" validate "$KNOB" "$ON_VALUE" "$OFF_VALUE"
fi
ON_VAR="$(uv run python "$PY" on-var "$KNOB")"
OFF_VAR="$(uv run python "$PY" off-var "$KNOB")"
ADMISSION_SIGNAL="$(uv run python "$PY" admission-signal "$KNOB")"
# A presence knob's on arm unsets the var (`env -u`) rather than assigning it:
# `=0` still counts as set and would take the wrong path in both arms. The
# per-arm env prefix is decided by `arm-cmd` in Python, so the on arm's argv is
# a pure function of the knob table and the tests can see it.

# A count knob's engagement pass: one short single-frontier run per arm with the
# trace var set, so the driver can count how many layers the packed-FA path
# engaged. The trace prints per dispatch, so it stays off the timed arms.
run_engagement() {
  local label="$1" value="$2"
  local log="$OUT/engagement-${label}.log"
  local count_file="$OUT/engagement-${label}.count"
  local env_prefix trace_var
  env_prefix="$(uv run python "$PY" arm-cmd "$KNOB" "$label" "$value")"
  trace_var="$(uv run python "$PY" trace-var "$KNOB")"
  # The progress line goes to stderr: this function's stdout must stay empty so
  # the count file is the only value it produces. A friendly echo here would
  # pollute the captured count.
  echo "[$(date +%H:%M:%S)] engagement $label -> $log" >&2
  echo "# engagement: $label knob=$KNOB env $env_prefix $trace_var=1 ./ds4-bench -m $GGUF --metal --prompt-file $PROMPT --ctx-start $CTX_START --ctx-max $CTX_START --step-incr $STEP --gen-tokens $GEN" > "$log"
  ( cd "$TREE" && env $env_prefix $trace_var=1 ./ds4-bench -m "$GGUF" --metal \
      --prompt-file "$PROMPT" \
      --ctx-start "$CTX_START" --ctx-max "$CTX_START" --step-incr "$STEP" \
      --gen-tokens "$GEN" --csv "$OUT/engagement-${label}.csv" ) >> "$log" 2>&1
  # The count goes to a file, not stdout: the caller reads it back, so a
  # progress echo cannot pollute the value.
  uv run python "$PY" count-trace-lines "$log" > "$count_file"
}

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

# Count-based admission: run one short single-frontier engagement pass per arm
# with the trace on, record both counts, and refuse the timed run if the counts
# are equal. Equal counts mean the knob did nothing, which is the
# tight-meaningless result the driver refuses. The trace prints per dispatch, so
# it stays off the timed arms -- tracing inside a timed arm would add I/O to one
# arm and not the other.
ON_COUNT=0
OFF_COUNT=0
if [ "$ADMISSION_SIGNAL" = "count" ]; then
  run_engagement on "$ON_VALUE"
  run_engagement off "$OFF_VALUE"
  ON_COUNT="$(cat "$OUT/engagement-on.count")"
  OFF_COUNT="$(cat "$OUT/engagement-off.count")"
  if ! uv run python "$PY" count-admission-ok "$ON_COUNT" "$OFF_COUNT"; then
    echo "REFUSING: knob $KNOB did not engage (on=$ON_COUNT off=$OFF_COUNT trace lines); the on arm must exceed the off arm and both must be non-zero" >&2
    exit 1
  fi
fi

# The knob name, both arm vars, both arm values, the admission signal, and the
# engagement counts go on the run, so a later reader can tell which knob and
# which values produced the rows, and whether the rows are verified. A knob
# with admission_signal "none" must not be read as verified.
cat > "$OUT/run-meta.json" <<EOF
{
  "knob": "$KNOB",
  "on_var": "$ON_VAR",
  "off_var": "$OFF_VAR",
  "on_value": "$ON_VALUE",
  "off_value": "$OFF_VALUE",
  "admission_signal": "$ADMISSION_SIGNAL",
  "tree": "$TREE",
  "gguf": "$GGUF",
  "reps": "$REPS",
  "started": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EOF
if [ "$ADMISSION_SIGNAL" = "count" ]; then
  cat >> "$OUT/run-meta.json" <<EOF
  ,
  "engagement": {
    "trace_var": "$(uv run python "$PY" trace-var "$KNOB")",
    "on_count": "$ON_COUNT",
    "off_count": "$OFF_COUNT"
  }
EOF
fi
echo "}" >> "$OUT/run-meta.json"

run_arm() {
  local label="$1" value="$2" rep="$3" position="$4"
  local csv="$OUT/${label}-rep${rep}.csv"
  local log="$OUT/${label}-rep${rep}.log"
  # The arm construction is a pure function of the knob table: `-u VAR` for a
  # presence knob's on arm, `VAR=value` otherwise. The shell runs it verbatim,
  # so the on arm's argv is decided in Python where the tests can see it.
  local env_prefix
  env_prefix="$(uv run python "$PY" arm-cmd "$KNOB" "$label" "$value")"
  echo "[$(date +%H:%M:%S)] $label rep $rep (position $position) -> $csv"
  # The arm construction goes on the log so every measurement carries its own
  # evidence: which var, which value, and whether the on arm unsets it. The
  # admission probe can read the log rather than trusting the table.
  echo "# arm: $label knob=$KNOB env $env_prefix ./ds4-bench -m $GGUF --metal --prompt-file $PROMPT --ctx-start $CTX_START --ctx-max $CTX_MAX --step-incr $STEP --gen-tokens $GEN --csv $csv" > "$log"
  # ds4-bench resolves metal/*.metal relative to its own tree, so run from
  # there. The env prefix is set for the bench process only. The unquoted
  # expansion is deliberate: `-u VAR` splits into two words, `VAR=value` into
  # one, and both values come from the validated knob table.
  ( cd "$TREE" && env $env_prefix ./ds4-bench -m "$GGUF" --metal \
      --prompt-file "$PROMPT" \
      --ctx-start "$CTX_START" --ctx-max "$CTX_MAX" --step-incr "$STEP" \
      --gen-tokens "$GEN" --csv "$csv" ) >> "$log" 2>&1
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
      value="$ON_VALUE"
    else
      value="$OFF_VALUE"
    fi
    echo "rep=$rep position=$position of 2 label=$label" >> "$OUT/run-order.txt"
    run_arm "$label" "$value" "$rep" "$position"
  done
done
echo "done: $OUT"
