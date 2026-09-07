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

# Cold large-chunk prefill (#171). Empty by default, so the flag is absent and
# nothing about an ordinary sweep changes.
#
# ds4-bench's prefill_tps times only the newest interval at each frontier
# (ds4_bench.c:10). Frontier 1 is a cold prefill of CTX_START tokens; every
# later frontier takes the resume path and times the appended STEP tokens.
# Setting PREFILL_CHUNK equal to STEP makes each of those a single chunk, which
# is the shape adamlawi's CUDA -12.23% was measured in.
#
# ds4_prefill_cap_for_prompt (ds4.c:12159 at ds4 399acbbe) is the whole of the
# cap logic: a non-zero requested chunk is used as given, clamped only to
# prompt_len. There is no other ceiling -- an earlier version of this comment
# claimed raw_cap clamped the chunk to 8192, which was wrong. ds4_default_raw_cap
# (ds4.c:12144) is the raw-KV cap for attention, DS4_N_SWA clamped to ctx, and
# the built-in shapes set n_swa to 128 or 0. It has nothing to do with prefill
# chunking, and 8192 appears in the prefill path only as the PRO variant's
# DEFAULT when no chunk was requested. So any value is honoured uniformly
# across the sweep and there is no ceiling to warn about.
#
# Line numbers are pinned to a sha because ds4.c is 70k lines and moves daily;
# a bare ds4.c:NNNNN is unverifiable a week later, which is how the wrong
# citation above survived review.
PREFILL_CHUNK=${PREFILL_CHUNK:-}

# bash 3.2 ships on macOS and aborts on "${arr[@]}" when arr is empty under
# set -u, which would kill the run at the first arm. This form is safe on both.
prefill_flag=()
if [ -n "$PREFILL_CHUNK" ]; then
  # Refuse a bad value here, not after the lock is held and 73 GiB is resident.
  # Same reason the missing-binary check above runs before the lock: a typo
  # should cost a second, not a model load.
  # 0 is refused rather than passed through: ds4_prefill_cap_for_prompt takes
  # the `requested_chunk != 0` branch or nothing (ds4.c:12159 at ds4 399acbbe),
  # so 0 falls through to the unspecified path and lands on the 4096 non-PRO
  # default. PREFILL_CHUNK=0 meaning "unlimited" would silently give chunked
  # prefill instead. Refusing is the only reading that cannot mislead.
  #
  # Note the asymmetry: on the DS4_METAL_PREFILL_CHUNK env path in that same
  # function, a value <= 0 DOES mean unlimited (cap stays prompt_len). The flag
  # and the env var disagree about 0, which is the reason to refuse it here
  # rather than pass it on and hope the reader knows which path was taken.
  case "$PREFILL_CHUNK" in
    ''|*[!0-9]*|0|0*[!0-9]*)
      echo "REFUSING: PREFILL_CHUNK='$PREFILL_CHUNK' is not a positive integer" >&2
      exit 1 ;;
  esac
  if [ "$PREFILL_CHUNK" -eq 0 ]; then
    echo "REFUSING: PREFILL_CHUNK=0 means 'unspecified' to ds4, not 'unlimited'" >&2
    exit 1
  fi
  prefill_flag=(--prefill-chunk "$PREFILL_CHUNK")
fi

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
  # The sweep is an input to every ratio in the CSVs and was recorded nowhere.
  # On 2026-09-06 a comparison against a published 32-frontier baseline ran on
  # this script's 8-frontier default and read 1.152 against the published
  # 1.155 -- a confirmatory artifact of sampling only the steep region, and
  # nothing beside the rows said the sweep differed.
  echo "sweep ctx_start=$CTX_START ctx_max=$CTX_MAX step=$STEP gen=$GEN reps=$REPS"
  echo "prefill_chunk=${PREFILL_CHUNK:-<flag absent>}"
  # These set the same caps as the flags, but only when the flags are absent
  # (ds4.c:13554, :40447). An inherited value would silently change the prefill
  # shape of a run that never mentions it.
  echo "DS4_METAL_PREFILL_CHUNK=${DS4_METAL_PREFILL_CHUNK:-<unset>}"
  echo "DS4_METAL_GRAPH_RAW_CAP=${DS4_METAL_GRAPH_RAW_CAP:-<unset>}"
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
    # Per-arm log, not the batch log. ds4 prints its Metal route, its
    # drift-patch flag set and its pipeline fallbacks to stderr at startup,
    # and that output is the only evidence that the two trees ran DIFFERENT
    # code. Interleaved in one batch log it cannot be diffed; per arm it can.
    # This matters when an engine A/B comes out flat: "the change is a wash"
    # and "the new kernels were never selected" produce the same CSV, and
    # only the startup diagnostics separate them.
    log="$OUT/${label}-rep${rep}.log"
    echo "[$(date +%H:%M:%S)] $label rep $rep -> $csv"
    if ! ( cd "$tree" && ./ds4-bench -m "$GGUF" --metal \
      --prompt-file "$PROMPT" \
      --ctx-start "$CTX_START" --ctx-max "$CTX_MAX" --step-incr "$STEP" \
      --gen-tokens "$GEN" ${prefill_flag[@]+"${prefill_flag[@]}"} \
      --csv "$csv" ) > "$log" 2>&1; then
      # The failure text is in the log now, not on the batch's stdout, so
      # say where it went and show the tail rather than dying silently.
      echo "FAILED: $label rep $rep -- see $log" >&2
      tail -20 "$log" >&2
      exit 1
    fi
    # #140: the prompt is an input to the prefill result, so it goes on the
    # rows. Stamped per CSV rather than at the end, so a run that dies
    # halfway still says what it measured.
    uv run python "$(dirname "$0")/prompt_meta.py" --prompt "$PROMPT" --stamp "$csv"
  done
done
echo "done: $OUT"