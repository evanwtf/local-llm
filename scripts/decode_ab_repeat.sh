#!/usr/bin/env bash
# Run the same decode A/B N times, into numbered directories (#136).
#
# One run of an A/B is not a measurement. Four runs of the #118 comparison
# returned +16.5%, +21.2%, +17.6% and +17.7% on identical inputs -- a 4.7 pp
# spread, against a within-run repeat spread of 4.4 pp at a single frontier.
# A single run's internal agreement is not precision, and nothing showed that
# until the same A/B was deliberately run more than once.
#
# Each repetition is a separate invocation of the underlying harness, so each
# takes and releases the run lock (#133) in turn, and each alternates arm
# order internally (#130). Runs are sequential: two at once would measure
# contention.
#
# Usage:
#   scripts/decode_ab_repeat.sh <n> <outdir-prefix> <harness> [harness args...]
#
# Example -- four runs of the q4/q8 A/B at the #952 engine:
#   DS4=~/git/ds4-pr621 CTX_MAX=65536 \
#   scripts/decode_ab_repeat.sh 4 benchmarks/ds4/pr621-recheck \
#     scripts/decode_ab.sh q4 /path/q4.gguf q8 /path/q8.gguf
#
# Report all of them together with:
#   uv run python scripts/decode_ab_report.py <outdir-prefix>-run*
set -euo pipefail

N=${1:?number of runs}; shift
PREFIX=${1:?output directory prefix}; shift
HARNESS=${1:?harness script}; shift

command -v uv >/dev/null || { echo "uv not found" >&2; exit 1; }

# A run is complete when every CSV has the same frontier count as the widest
# one -- not when six files exist. A file being written right now already has
# a name and a header, so counting files reports a run as finished while
# ds4-bench is still filling its last one, and any statistic taken then
# silently includes a partial arm.
complete_runs() {
  local d=$1 want=0 n
  ls "$d"/*.csv >/dev/null 2>&1 || { echo 0; return; }
  for f in "$d"/*.csv; do
    n=$(( $(wc -l < "$f") - 1 ))
    [ "$n" -gt "$want" ] && want=$n
  done
  for f in "$d"/*.csv; do
    n=$(( $(wc -l < "$f") - 1 ))
    [ "$n" -eq "$want" ] || { echo 0; return; }
  done
  echo 1
}

for i in $(seq 1 "$N"); do
  OUT="$(cd "$(dirname "$PREFIX")" && pwd)/$(basename "$PREFIX")-run$i"
  if [ -d "$OUT" ] && [ "$(complete_runs "$OUT")" = "1" ]; then
    # Never silently overwrite a completed run: the whole point of this
    # script is accumulating runs, and a clobbered one is unrecoverable.
    echo "[$(date '+%H:%M:%S')] run $i: $OUT already holds CSVs, skipping" >&2
    continue
  fi
  mkdir -p "$OUT"
  # Machine state at the start of each run. #118's run 2 was an outlier and
  # the cold-start hypothesis could only be tested afterwards because run 4
  # happened to capture this; capture it every time instead of hoping.
  {
    echo "# run $i of $N, launched $(date '+%Y-%m-%dT%H:%M:%S %Z')"
    echo "# harness: $HARNESS $*"
    echo "# fans (read only, never set):"
    fancontrol status --json 2>/dev/null || echo "  (fancontrol unavailable)"
    echo "# thermals at launch:"
    uv run python scripts/thermals.py --json --quiet 2>&1 | tail -1
  } > "$OUT/start-state.txt"

  echo "[$(date '+%H:%M:%S')] run $i of $N -> $OUT"
  bash "$HARNESS" "$@" "$OUT"

  {
    echo "# finished: $(date '+%Y-%m-%dT%H:%M:%S %Z')"
    echo "# thermals at finish:"
    uv run python scripts/thermals.py --json --quiet 2>&1 | tail -1
  } >> "$OUT/start-state.txt"
  echo "[$(date '+%H:%M:%S')] run $i done"
done

echo "[$(date '+%H:%M:%S')] all $N runs complete under ${PREFIX}-run*"
