#!/usr/bin/env bash
# delegation-core uninstaller — Linux / macOS
# Usage: ./uninstall.sh
#
# Removes the venv, config, logs, sessions, process tracker, graphs, the
# llama.cpp binary dir, hooks/docs, the autostart entry, and the installed
# dashboard app.
#
# NEVER touches: your Obsidian vault, or downloaded model weights under
# ~/.delegation_core/models/. Those are yours regardless of whether
# delegation-core itself stays installed.
set -e

CFG_DIR="$HOME/.delegation_core"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"
DASH_PKG="delegation-core-dashboard"

_banner() {
    echo ""
    echo "┌─────────────────────────────────┐"
    echo "│  delegation-core  uninstaller   │"
    echo "└─────────────────────────────────┘"
    echo ""
}

_banner

if [ ! -d "$CFG_DIR" ]; then
    echo "Nothing to uninstall — $CFG_DIR does not exist."
    exit 0
fi

# ── Read the vault path before anything is touched, purely to tell the user
# where it still lives afterward. Never write to it. ────────────────────────
VAULT_PATH=""
if [ -f "$CFG_DIR/config.json" ]; then
    VAULT_PATH=$(python3 -c "
import json
try:
    print(json.load(open('$CFG_DIR/config.json')).get('vault_path', ''))
except Exception:
    pass
" 2>/dev/null || true)
fi

# ── Detect an installed dashboard app ────────────────────────────────────────
DASH_INSTALLED=""
if command -v dpkg &>/dev/null && dpkg -s "$DASH_PKG" &>/dev/null 2>&1; then
    DASH_INSTALLED="deb"
elif command -v rpm &>/dev/null && rpm -q "$DASH_PKG" &>/dev/null 2>&1; then
    DASH_INSTALLED="rpm"
elif [ -f "$CFG_DIR/app/delegation-core-dashboard.AppImage" ]; then
    DASH_INSTALLED="appimage"
elif [ "$OS" = "Darwin" ] && [ -d "/Applications/delegation-core Dashboard.app" ]; then
    DASH_INSTALLED="macapp"
fi

# ── Skills that may have been installed by install.sh — list only, never
# removed automatically (they're a general Claude Code layer, not
# delegation-core-specific). Best-effort: only checked if this script is
# still run from within the original project checkout. ─────────────────────
FOUND_SKILLS=""
if [ -d "$SCRIPT_DIR/skills" ] && [ -d "$HOME/.claude/skills" ]; then
    for d in "$SCRIPT_DIR/skills"/*/; do
        [ -d "$d" ] || continue
        sname=$(basename "$d")
        [ -e "$HOME/.claude/skills/$sname" ] && FOUND_SKILLS="$FOUND_SKILLS $sname"
    done
fi

echo "This will remove:"
echo "  • Python venv             $CFG_DIR/venv"
echo "  • Config                  $CFG_DIR/config.json"
echo "  • Logs                    $CFG_DIR/*.log"
echo "  • Client session files    $CFG_DIR/sessions/"
echo "  • Process tracker         $CFG_DIR/processes.json"
echo "  • Code graphs             $CFG_DIR/graphs/, graphs_registry.json"
echo "  • llama.cpp binary dir    $CFG_DIR/llama/  (not the model weights — see below)"
echo "  • Hooks + agent docs      $CFG_DIR/hooks/, AGENT_GUIDE*.md, CLAUDE_SYSTEM_PROMPT*.md"
echo "  • Misc state              last_brief.json, vault_health.json, backups_pre_upgrade_*"
case "$OS" in
    Linux)  echo "  • Autostart entry         systemd --user service 'delegation-core-llama'" ;;
    Darwin) echo "  • Autostart entry         LaunchAgent com.delegation-core.llama" ;;
esac
if [ -n "$DASH_INSTALLED" ]; then
    echo "  • Installed dashboard app ($DASH_INSTALLED)"
fi
echo ""
echo "This will NEVER touch:"
echo "  • Your vault              ${VAULT_PATH:-<unknown — config.json missing or unreadable>}"
echo "  • Downloaded model weights $CFG_DIR/models/"
echo ""
if [ -n "$FOUND_SKILLS" ]; then
    echo "Note: these bundled skills are present in ~/.claude/skills and will NOT be"
    echo "removed (they're a general Claude Code layer, not delegation-core-specific):"
    for s in $FOUND_SKILLS; do echo "    - $s"; done
    echo "  Remove manually if you want: rm -rf ~/.claude/skills/<name>"
    echo ""
fi

read -r -p "Type 'yes' to proceed: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted. Nothing was removed."
    exit 0
fi
echo ""

# ── Stop and remove the autostart entry ──────────────────────────────────────
echo "Removing autostart entry..."
if [ "$OS" = "Linux" ]; then
    systemctl --user disable --now delegation-core-llama &>/dev/null || true
    rm -f "$HOME/.config/systemd/user/delegation-core-llama.service"
    systemctl --user daemon-reload &>/dev/null || true
elif [ "$OS" = "Darwin" ]; then
    launchctl unload "$HOME/Library/LaunchAgents/com.delegation-core.llama.plist" &>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/com.delegation-core.llama.plist"
fi
echo "  ✓ Done."

# ── Remove the installed dashboard app ───────────────────────────────────────
if [ -n "$DASH_INSTALLED" ]; then
    echo "Removing dashboard app ($DASH_INSTALLED)..."
    case "$DASH_INSTALLED" in
        deb) sudo dpkg -r "$DASH_PKG" ;;
        rpm) sudo rpm -e "$DASH_PKG" ;;
        appimage)
            rm -f "$CFG_DIR/app/delegation-core-dashboard.AppImage"
            rm -f "$HOME/.local/share/applications/delegation-core-dashboard.desktop"
            ;;
        macapp) rm -rf "/Applications/delegation-core Dashboard.app" ;;
    esac
    echo "  ✓ Done."
fi

# ── Remove everything else. NEVER touch config.json's vault_path, and NEVER
# touch $CFG_DIR/models/. ────────────────────────────────────────────────────
echo "Removing venv, config, logs, and remaining state..."
rm -rf "$CFG_DIR/venv"
rm -f "$CFG_DIR/config.json"
rm -f "$CFG_DIR"/*.log
rm -rf "$CFG_DIR/sessions"
rm -f "$CFG_DIR/processes.json"
rm -rf "$CFG_DIR/graphs"
rm -f "$CFG_DIR/graphs_registry.json"
rm -rf "$CFG_DIR/llama"
rm -rf "$CFG_DIR/hooks"
rm -f "$CFG_DIR"/AGENT_GUIDE.md "$CFG_DIR"/AGENT_GUIDE.dist.md
rm -f "$CFG_DIR"/CLAUDE_SYSTEM_PROMPT.md "$CFG_DIR"/CLAUDE_SYSTEM_PROMPT.dist.md
rm -f "$CFG_DIR/last_brief.json" "$CFG_DIR/vault_health.json"
rm -rf "$CFG_DIR"/backups_pre_upgrade_*
rm -rf "$CFG_DIR/app"
echo "  ✓ Done."
echo ""

echo "Uninstall complete."
if [ -n "$VAULT_PATH" ]; then
    echo "Your vault was left untouched at: $VAULT_PATH"
else
    echo "Your vault (path unknown — config.json was missing) was not touched."
fi
echo "Downloaded model weights were left untouched at: $CFG_DIR/models/"
