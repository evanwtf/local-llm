#!/usr/bin/env bash
# Greedy coherence check before trusting any new GGUF (#25, #48).
#
# A model can load, serve, and report entirely plausible token counts while
# emitting noise -- that is #25, and it cost hours. Run this at --temp 0 on
# every new or requantized model BEFORE any benchmark, and read the output
# with your own eyes. A benchmark cannot tell prose from gibberish.
#
# Usage: scripts/coherence_check.sh <gguf> [more.gguf ...]
set -uo pipefail
DS4=${DS4:-$HOME/git/ds4}
PROMPT=${PROMPT:-"Write a Python function that returns the nth Fibonacci number, then explain in one sentence why the iterative form is preferred over the naive recursive one."}
TOKENS=${TOKENS:-200}

for gguf in "$@"; do
  echo "============================================================"
  echo "MODEL: $(basename "$gguf")"
  echo "============================================================"
  "$DS4/ds4" -m "$gguf" -p "$PROMPT" --temp 0 --ctx 8192 2>&1 | tail -n "$TOKENS"
  echo
done
