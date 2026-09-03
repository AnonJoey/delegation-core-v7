#!/usr/bin/env bash
# delegation-core uninstaller: Linux / macOS
# Usage: ./uninstall.sh [--yes] [--dry-run]
#
# This script is a stub on purpose. It has exactly two jobs that Python cannot
# do for itself:
#
#   1. find the interpreter, and
#   2. delete the venv AFTER that interpreter has exited.
#
# Everything else (reading the vault path, refusing unsafe configurations,
# stopping the daemon, removing BOTH service registrations, and checking each
# removal) lives in delegation_core/installer.py, so Linux, macOS and Windows
# run the same code instead of three transcriptions of it that drift.
#
# The previous version of this file was 193 lines of bash with a batch twin, and
# the two had already diverged: this one checked the result of its removals and
# the batch one discarded every error with `>nul 2>&1`. Both removed the
# llama.cpp autostart entry and left the MCP daemon's own systemd unit enabled,
# pointing at the venv they had just deleted.
#
# NEVER touched, by this or by the Python it calls: your vault, or the model
# weights under ~/.delegation_core/models/.
set -e

CFG_DIR="$HOME/.delegation_core"
VENV="$CFG_DIR/venv"
SENTINEL="$CFG_DIR/.venv-pending-removal"

echo ""
echo "+---------------------------------+"
echo "|  delegation-core  uninstaller   |"
echo "+---------------------------------+"
echo ""

if [ ! -d "$CFG_DIR" ]; then
    echo "Nothing to uninstall: $CFG_DIR does not exist."
    exit 0
fi

if [ ! -x "$VENV/bin/delegation-core" ]; then
    echo "ERROR: $VENV/bin/delegation-core is missing."
    echo ""
    echo "Without it the uninstall cannot stop the daemon or unregister its"
    echo "services, and deleting files while those are live is what this script"
    echo "exists to avoid. If the install is already broken, remove it by hand:"
    echo ""
    echo "  systemctl --user disable --now delegation-core delegation-core-llama   # Linux"
    echo "  launchctl unload -w ~/Library/LaunchAgents/com.delegation-core*.plist  # macOS"
    echo "  rm -rf $CFG_DIR/venv $CFG_DIR/hooks $CFG_DIR/sessions"
    echo ""
    echo "Your vault and $CFG_DIR/models/ are not part of that."
    exit 1
fi

# The sentinel is the handshake. Python writes it only when it has actually
# finished removing state and the venv is the one thing still standing. Clearing
# it first means a stale one from an earlier run cannot authorise a deletion
# this run did not ask for.
rm -f "$SENTINEL"

set +e
"$VENV/bin/delegation-core" uninstall "$@"
CODE=$?
set -e

# Deliberately NOT `if [ $CODE -eq 0 ]`. Exit 0 also covers "the user typed
# something other than yes at the prompt" and "--dry-run", and deleting the venv
# in either case would destroy the install of someone who had just declined to
# uninstall it.
if [ -f "$SENTINEL" ]; then
    echo ""
    echo "Removing the virtual environment..."
    rm -rf "$VENV"
    rm -f "$SENTINEL"
    echo "  done: $VENV"
fi

exit $CODE
