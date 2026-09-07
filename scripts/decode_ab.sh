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
# #203: absolutize OUT before the lock. The arm runs inside `( cd "$DS4" && ... )`,
# so a relative OUT is created under the repo by mkdir but resolved under the
# ds4 tree by --csv -- the CSV files land nowhere. The default OUT is absolute,
# which is why the bug only shows on a hand-passed relative path.
absolutize_out() {
  case "$OUT" in
    /*) ;;
    *) OUT="$PWD/$OUT" ;;
  esac
}
absolutize_out
DS4=${DS4:-$HOME/git/ds4}
PROMPT=${PROMPT:-$DS4/speed-bench/promessi_sposi.txt}

# Same frontiers and gen budget as ds4's own speed-bench, so our numbers are
# comparable to speed-bench/m5_max.csv rather than a private methodology.
CTX_START=${CTX_START:-2048}
CTX_MAX=${CTX_MAX:-16384}
STEP=${STEP:-2048}
GEN=${GEN:-128}
REPS=${REPS:-3}

# Large-chunk prefill (#162). Empty by default, so an ordinary sweep is
# unchanged and the flag is absent from the command line.
#
# @iammac2 reported ROCm Q4-vs-Q8 at head as "large chunk (8192-token
# prefill)". Without this, this script cannot produce that quantity at all --
# an unflagged prefill above 4096 tokens is chunked at the variant default
# (4096, or 8192 on PRO) whether or not you meant it, so a sweep that does not
# set it is measuring the default and not the chunk.
#
# Set PREFILL_CHUNK equal to STEP to make each frontier a single chunk.
#
# The ceiling is real but sits on a different path from the one people expect.
# ds4_prefill_cap_for_prompt (ds4.c:12986 at ds4-main 9ab70534) honours a
# non-zero request as given. metal_graph_prefill_chunked
# (ds4.c:36867 at ds4-main 9ab70534) then clamps every prefill AFTER THE FIRST
# to raw_cap:
#
#     if (start != 0 && chunk_cap > g->raw_cap) chunk_cap = g->raw_cap;
#
# and metal_graph_raw_cap_for_context (ds4.c:37541 at ds4-main 9ab70534)
# ceilings raw_cap at 8192. At 8192 the two coincide exactly, which is why
# @iammac2's value needs no special handling; above it, frontier 1 honours the
# request and the rest are cut to 8192 -- one run, two quantities.
PREFILL_CHUNK=${PREFILL_CHUNK:-}

prefill_flag=()
if [ -n "$PREFILL_CHUNK" ]; then
  # Refuse before the lock is held and 84 GiB is resident: a typo should cost
  # a second, not a model load. 0 is refused rather than passed through --
  # ds4_prefill_cap_for_prompt takes the `requested_chunk != 0` branch or
  # nothing, so 0 falls through to the variant default. "Unlimited" is not a
  # reading it supports on the flag path.
  case "$PREFILL_CHUNK" in
    ''|*[!0-9]*|0|0*[!0-9]*)
      echo "REFUSING: PREFILL_CHUNK='$PREFILL_CHUNK' is not a positive integer" >&2
      exit 1 ;;
  esac
  # The case above does NOT catch "00": the `0` arm matches only a single
  # zero, and `0*[!0-9]*` needs a non-digit that "00" does not have. This
  # numeric test is what actually refuses it. decode_ab_engine.sh carries the
  # same pair and its test file says so explicitly -- I copied the case here
  # and not this, and the "00" test caught it within the minute.
  if [ "$PREFILL_CHUNK" -eq 0 ]; then
    echo "REFUSING: PREFILL_CHUNK=0 means 'unspecified' to ds4, not 'unlimited'" >&2
    exit 1
  fi
  if [ "$PREFILL_CHUNK" -gt 8192 ]; then
    echo "WARNING: PREFILL_CHUNK=$PREFILL_CHUNK exceeds the raw_cap ceiling (8192)." >&2
    echo "  Frontier 1 will use $PREFILL_CHUNK; every later frontier will use 8192." >&2
    echo "  ds4.c:36867 at ds4-main 9ab70534, ds4.c:37541 at ds4-main 9ab70534." >&2
    echo "  One run, two quantities." >&2
  fi
  # bash 3.2 ships on macOS and aborts on "${arr[@]}" when arr is empty under
  # set -u, which would kill the run at the first arm.
  prefill_flag=(--prefill-chunk "$PREFILL_CHUNK")
fi

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
# #140: name the prompt, or the prefill half of this A/B is not well-posed.
# @adamlawi measured the same Q4-vs-Q8 question on one box at +2.5% with a
# 135 kB prompt and at parity with a 405 kB one. The prompt is an input to
# the result, so it goes on the rows and in a sidecar, not in a default
# nobody wrote down.
uv run python "$(dirname "$0")/prompt_meta.py" --prompt "$PROMPT" --sidecar "$OUT" --show

# #192: name the engine build, or the row cannot say what produced it. $DS4
# selects the binary AND the metal/*.metal shaders it loads at runtime, so the
# tree is part of the measurement, not a path detail. Written before the first
# arm runs: a sweep that dies halfway should still say what it was.
{
  echo "# weights A/B, $(date +%Y-%m-%dT%H:%M:%S%z)"
  echo "engine tree=$DS4 @ $(git -C "$DS4" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  if [ -n "$(git -C "$DS4" status --porcelain 2>/dev/null)" ]; then
    echo "engine_dirty=true   # uncommitted code: the sha does not name this binary"
  fi
  echo "binary_mtime=$(date -r "$DS4/ds4-bench" +%Y-%m-%dT%H:%M:%S%z 2>/dev/null || echo unknown)"
  echo "A label=$LABEL_A gguf=$GGUF_A"
  echo "B label=$LABEL_B gguf=$GGUF_B"
  echo "sweep ctx_start=$CTX_START ctx_max=$CTX_MAX step=$STEP gen=$GEN reps=$REPS"
  echo "prefill_chunk=${PREFILL_CHUNK:-<unset>}"
  echo "DS4_METAL_PREFILL_CHUNK=${DS4_METAL_PREFILL_CHUNK:-<unset>}"
  echo "DS4_METAL_GRAPH_RAW_CAP=${DS4_METAL_GRAPH_RAW_CAP:-<unset>}"
} > "$OUT/engines.txt"
cat "$OUT/engines.txt"

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
      --gen-tokens "$GEN" --csv "$csv" \
      ${prefill_flag[@]+"${prefill_flag[@]}"} )
    # Stamp immediately, not at the end: a run that dies halfway still leaves
    # CSVs, and an unstamped one cannot be told from a differently-prompted one.
    uv run python "$(dirname "$0")/prompt_meta.py" --prompt "$PROMPT" --stamp "$csv"
  done
done
echo "done: $OUT"
