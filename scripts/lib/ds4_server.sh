# Stopping ds4-server, in one place -- sourced, not executed.
#
# #145: `stack_agent_ab.sh` restarted the server between sweeps and stopped
# none of them, so **every** clean finish left the last arm's server resident.
# Four runs in a row did it, most recently 97.9 GiB, and it is deterministic
# rather than a race: the final `restart_server` simply has no matching stop.
# It blocked the next run, and preflight called the machine healthy because a
# leftover from a finished run is indistinguishable from a server the current
# run needs.
#
# Four scripts had already written this same pkill-verify sequence by hand
# (stack_agent_ab, disk_kv_mechanism_test, restart_between_trials and its armB
# twin). AGENTS.md records what three copies of a predicate cost the last time;
# this is the same shape, so the sequence lives here and they call it.

# Match the server, not a shell that mentions it. `pgrep -f ds4-server` also
# matches the script doing the pgrep, which is the self-match trap NEXT.md and
# preflight.py both record.
DS4_SERVER_PATTERN=${DS4_SERVER_PATTERN:-'ds4-server --metal'}

ds4_server_running() {
  pgrep -f "$DS4_SERVER_PATTERN" >/dev/null 2>&1
}

# Stop it, then prove it stopped. Returns non-zero if it will not die; the
# caller decides whether that is fatal, because it is fatal before a run and
# only worth reporting after one.
ds4_stop_server() {
  local why=${1:-}
  ds4_server_running || return 0
  echo "[$(date +%H:%M:%S)] stopping ds4-server${why:+ ($why)}..."
  pkill -f "$DS4_SERVER_PATTERN" 2>/dev/null || true
  sleep 3
  if ds4_server_running; then
    pkill -9 -f "$DS4_SERVER_PATTERN" 2>/dev/null || true
    sleep 2
  fi
  if ds4_server_running; then
    echo "REFUSING: ds4-server would not stop" >&2
    return 1
  fi
  return 0
}

# Teardown on every exit path, not just the happy one. A run that is
# interrupted or that dies mid-sweep leaks exactly the same 98 GiB as one that
# finishes, and Ctrl-C is the likeliest way to end a long batch.
#
# The exit status is preserved: the trap runs for its side effect and must not
# turn a failed run into a successful one, or the reverse.
ds4_stop_on_exit() {
  local status=$?
  trap - EXIT INT TERM
  ds4_stop_server "teardown" || echo "WARNING: server survived teardown" >&2
  exit "$status"
}

# Chain rather than replace. `restart_between_trials.sh` already traps EXIT to
# release the preflight lock, and a second bare `trap ... EXIT` would silently
# discard it -- the lock would then outlive the run that took it.
ds4_arm_stop_trap() {
  local existing
  existing=$(trap -p EXIT | sed -n "s/^trap -- '\(.*\)' EXIT$/\1/p")
  if [ -n "$existing" ]; then
    trap "${existing}; ds4_stop_on_exit" EXIT
  else
    trap ds4_stop_on_exit EXIT
  fi
  trap ds4_stop_on_exit INT TERM
}
