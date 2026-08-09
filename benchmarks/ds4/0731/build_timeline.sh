#!/bin/sh
# Rebuild TIMELINE.md from every timestamped orchestrator log.
# Re-run any time: sh bench-0731/build_timeline.sh
set -u
cd "$(dirname "$0")"
{
  echo "# Session timeline — DeepSeek V4 Flash 0731 benchmarking"
  echo
  echo "All times local (America/New_York), 2026-08-08. Regenerate with \`sh bench-0731/build_timeline.sh\`."
  echo
  echo '```'
  cat *orchestrator.log prefill_probe*.log streaming_probe.log streaming_ppl.log 2>/dev/null \
    | grep -E "^\[20" | sort -u
  echo '```'
} > TIMELINE.md
echo "wrote TIMELINE.md ($(grep -c '^\[20' TIMELINE.md) events)"
