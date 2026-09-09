#!/bin/bash
# Metal 4 TensorOps: ~21% faster, not bit-exact. See scripts/ds4_serve.py.
exec uv run python "$(dirname "$0")/ds4_serve.py" fast "$@"
