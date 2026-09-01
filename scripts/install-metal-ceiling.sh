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
SRC="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/$PLIST"
DEST="/Library/LaunchDaemons/$PLIST"

sudo cp "$SRC" "$DEST"
sudo chown root:wheel "$DEST"
sudo chmod 644 "$DEST"
sudo launchctl load -w "$DEST"

echo "installed $DEST"
echo "current value: $(sysctl -n iogpu.wired_limit_mb) (expect 114688)"
echo
echo "To undo:  sudo launchctl unload -w $DEST && sudo rm $DEST"
echo
echo "NOTE: sysctl reports 0 when no override is set, and 0 means 'device"
echo "default', not 'no ceiling'. A 0 after reboot means the daemon did not"
echo "fire. The authoritative check is the Metal probe in issue #30."
