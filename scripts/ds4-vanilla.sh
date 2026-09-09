#!/bin/bash
# Reference kernels: bit-exact, slower. See scripts/ds4_serve.py.
exec uv run python "$(dirname "$0")/ds4_serve.py" vanilla "$@"
