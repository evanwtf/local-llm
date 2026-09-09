#!/usr/bin/env bash
# One status line for a set of decode-A/B run directories.
#
# Exists so a status monitor does not carry its own idea of what "complete"
# means. Three separate copies of that rule drifted apart in one afternoon
# (a03ca8d); this delegates to post_ab_run.is_complete like everything else.
#
# A field that cannot be computed says so rather than printing empty: a blank
# where a number belongs is indistinguishable from "nothing to report", which
# is how a broken status line goes unnoticed.
#
# Usage: scripts/ab_status.sh <prefix-glob>   e.g. benchmarks/ds4/pr621-recheck-run
set -uo pipefail
cd "$(dirname "$0")/.."
PREFIX=${1:?run directory prefix}
WANT=${WANT_RUNS:-4}

COMPLETE=""; DONE=0
for d in "$PREFIX"*; do
  [ -d "$d" ] || continue
  if [ "$(uv run python -c "
import pathlib, sys
sys.path.insert(0, 'scripts')
from post_ab_run import is_complete
print(1 if is_complete(pathlib.Path('$d')) else 0)" 2>/dev/null)" = "1" ]; then
    DONE=$((DONE + 1)); COMPLETE="$COMPLETE $d"
  fi
done

CUR=$(ls -d "$PREFIX"* 2>/dev/null | tail -1)
ROWS=$(cat "$CUR"/*.csv 2>/dev/null | grep -c '^[0-9]' || echo 0)
LOCK=$([ -f .run-lock.json ] && echo held || echo free)
TEMP=$(uv run python scripts/thermals.py --json --quiet 2>&1 | tail -1 \
  | sed -E 's/.*"die_max_c": ([0-9.]+).*/\1C/') || TEMP="unreadable"

RATIO="no complete run yet"
if [ -n "$COMPLETE" ]; then
  # shellcheck disable=SC2086
  OUT=$(uv run python scripts/decode_ab_report.py $COMPLETE 2>&1)
  if [ $? -ne 0 ]; then
    RATIO="REPORT FAILED: $(echo "$OUT" | tail -1)"
  else
    RATIO=$(echo "$OUT" | grep -m1 "runs: median" \
      || echo "$OUT" | grep -m1 "paired median" \
      || echo "report gave no median line")
  fi
fi

echo "$(date '+%H:%M') | ${DONE}/${WANT} complete | $(basename "$CUR") at ${ROWS} rows | lock=$LOCK die=$TEMP | $RATIO"
[ "$DONE" -ge "$WANT" ] && exit 10 || exit 0
