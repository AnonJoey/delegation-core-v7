#!/usr/bin/env bash
# Double-click entry point for Finder — macOS only opens .command files (not
# .sh) in Terminal automatically. Just a thin shim to the real installer so
# there's a single implementation to maintain.
cd "$(dirname "$0")"
exec ./install.sh
