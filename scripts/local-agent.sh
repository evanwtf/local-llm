#!/bin/sh
# Start a recommended local stack and drop into a coding agent (see RECOMMENDATIONS.md).
# Thin shim: the launcher is scripts/local_agent.py (#235). Args pass through.
here="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec uv run --project "$here/.." python "$here/local_agent.py" "$@"
