# Stopping mlx-serve, in one place -- sourced, not executed.
#
# The sibling of lib/ds4_server.sh, written for #191 when `stack_agent_ab.sh`
# gained a second engine. Everything #145 established about ds4 applies here
# and harder: this engine holds ~100 GiB resident, and until 2026-09-07
# `preflight.py` could not see an mlx-serve process AT ALL -- it was absent
# from INFERENCE, so a leftover was invisible to the empty-machine gate rather
# than merely uncounted. A leaked server would have been reported as an empty
# machine with 100 GiB of headroom that did not exist.
#
# So: stop it, then prove it stopped, exactly as the ds4 side does.

# Match the server process, not a shell that mentions it. `pgrep -f mlx-serve`
# alone also matches the script doing the pgrep -- the self-match trap that
# preflight.py and NEXT.md both record. `--serve` is in the server's argv and
# not in this file's own invocation, so it discriminates.
MLX_SERVE_PATTERN=${MLX_SERVE_PATTERN:-'mlx-serve --model'}

mlx_serve_running() {
  pgrep -f "$MLX_SERVE_PATTERN" >/dev/null 2>&1
}

mlx_serve_stop_server() {
  local why=${1:-}
  mlx_serve_running || return 0
  echo "[$(date +%H:%M:%S)] stopping mlx-serve${why:+ ($why)}..."
  pkill -f "$MLX_SERVE_PATTERN" 2>/dev/null || true
  sleep 3
  if mlx_serve_running; then
    pkill -9 -f "$MLX_SERVE_PATTERN" 2>/dev/null || true
    sleep 2
  fi
  if mlx_serve_running; then
    echo "REFUSING: mlx-serve would not stop" >&2
    return 1
  fi
  return 0
}

# Stop BOTH engines on the way out. A run that alternates arms can be
# interrupted while either one is resident, and the trap does not know which.
# Stopping an engine that is not running is a no-op, so asking for both is
# free and asking for one is a coin flip.
mlx_serve_stop_on_exit() {
  local status=$?
  trap - EXIT INT TERM
  mlx_serve_stop_server "teardown" || echo "WARNING: mlx-serve survived teardown" >&2
  if command -v ds4_stop_server >/dev/null 2>&1; then
    ds4_stop_server "teardown" || echo "WARNING: ds4-server survived teardown" >&2
  fi
  exit "$status"
}

# Chain rather than replace, for the same reason ds4_arm_stop_trap does: a
# bare `trap ... EXIT` silently discards an existing handler, and the one it
# would discard here is the ds4 teardown.
mlx_serve_arm_stop_trap() {
  local existing
  existing=$(trap -p EXIT | sed -n "s/^trap -- '\(.*\)' EXIT$/\1/p")
  if [ -n "$existing" ]; then
    # shellcheck disable=SC2064  # expanding NOW is the point: $existing is the
    # text of the handler already installed, captured here and re-installed
    # alongside ours. Single quotes would defer it and lose the chain.
    trap "${existing}; mlx_serve_stop_on_exit" EXIT
  else
    trap mlx_serve_stop_on_exit EXIT
  fi
  trap mlx_serve_stop_on_exit INT TERM
}
