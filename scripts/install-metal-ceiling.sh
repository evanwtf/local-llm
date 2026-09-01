#!/bin/sh
# Persist the Metal wired limit across reboots.
#
# ds4 budgets from Metal's recommendedMaxWorkingSetSize, which on a 128 GiB Mac
# is 107.52 GiB stock -- yielding a 75.5 GiB budget against an 89.87 GiB
# GLM-5.3 model, i.e. a refusal. Raised to 112.00 GiB it runs.
#
# This is a CAP on what the GPU may wire, not a reservation: with the ceiling
# at 112 GiB and no model loaded, wired memory sits at ~5 GiB. Persisting it
# costs nothing on a normal day.
#
# macOS ignores /etc/sysctl.conf on current releases, so this uses a
# LaunchDaemon. Requires sudo.
set -eu
PLIST=wtf.local-llm.metal-ceiling.plist
LABEL=wtf.local-llm.metal-ceiling
SRC="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/$PLIST"
DEST="/Library/LaunchDaemons/$PLIST"
LOG=/var/log/metal-ceiling.log

sudo cp "$SRC" "$DEST"
sudo chown root:wheel "$DEST"
sudo chmod 644 "$DEST"

# Re-installing over a loaded job: drop it first, or bootstrap reports EBUSY.
sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
sudo launchctl bootstrap system "$DEST"
sudo launchctl enable "system/$LABEL"

echo "installed $DEST"
echo

# sysctl alone proves nothing here: the value may already be set by hand, so a
# correct reading is equally consistent with a daemon that never ran. The
# daemon's own log is the evidence -- it records the transition it performed.
if sudo launchctl print "system/$LABEL" >/dev/null 2>&1; then
    echo "job is loaded"
else
    echo "WARNING: job is NOT loaded; it will not fire at boot" >&2
fi
echo "current value: $(sysctl -n iogpu.wired_limit_mb) (expect 114688)"
echo "last daemon run: $(sudo tail -1 "$LOG" 2>/dev/null || echo '(no log yet)')"
echo
echo "To confirm it PERSISTS you must reboot, then check that $LOG"
echo "has a fresh line reading '0 -> 114688'. A '114688 -> 114688' line means"
echo "the value was already set and the daemon proved nothing."
echo
echo "To undo:  sudo launchctl bootout system/$LABEL && sudo rm $DEST"
echo
echo "NOTE: sysctl reports 0 when no override is set, and 0 means 'device"
echo "default', not 'no ceiling'. A 0 after reboot means the daemon did not"
echo "fire. The authoritative check is the Metal probe in issue #30."
