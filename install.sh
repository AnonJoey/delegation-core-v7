#!/usr/bin/env bash
# delegation-core installer — Linux / macOS
# Usage: ./install.sh
# Detects OS, installs system dependencies, creates venv, installs package,
# then launches the setup wizard automatically.
set -e

VENV="$HOME/.delegation_core/venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"
ARCH="$(uname -m)"

_banner() {
    echo ""
    echo "┌─────────────────────────────────┐"
    echo "│   delegation-core  installer    │"
    echo "└─────────────────────────────────┘"
    echo "  OS: $OS ($ARCH)"
    echo ""
}

_abort() { echo ""; echo "ERROR: $1"; echo ""; exit 1; }

# ── 1. Find Python 3.11+ ─────────────────────────────────────────────────────
_find_python() {
    for cmd in python3.13 python3.12 python3.11 python3; do
        if command -v "$cmd" &>/dev/null; then
            OK=$("$cmd" -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null)
            if [ "$OK" = "True" ]; then
                echo "$cmd"; return 0
            fi
        fi
    done
    return 1
}

# ── 2. Linux — system packages ───────────────────────────────────────────────
_linux_deps_apt() {
    echo "  Checking system packages..."
    MISSING=""
    for pkg in python3-venv python3-dev build-essential; do
        if ! dpkg -s "$pkg" &>/dev/null 2>&1; then
            MISSING="$MISSING $pkg"
        fi
    done
    if [ -n "$MISSING" ]; then
        echo "  Missing:$MISSING"
        echo "  Installing (requires sudo)..."
        sudo apt-get install -y $MISSING
        echo "  ✓ Packages installed."
    else
        echo "  ✓ System packages OK."
    fi
}

_linux_deps_dnf() {
    echo "  Installing build dependencies via dnf..."
    sudo dnf install -y python3-devel gcc gcc-c++ make 2>/dev/null || true
    echo "  ✓ Done."
}

_linux_deps_pacman() {
    echo "  Installing build dependencies via pacman..."
    sudo pacman -S --noconfirm --needed python base-devel 2>/dev/null || true
    echo "  ✓ Done."
}

# ── 3. macOS — Xcode CLT ─────────────────────────────────────────────────────
_macos_deps() {
    echo "  Checking Xcode Command Line Tools..."
    if ! xcode-select -p &>/dev/null 2>&1; then
        echo "  Not found. Starting installer..."
        echo "  A dialog will appear — click Install, then come back and press Enter."
        xcode-select --install 2>/dev/null || true
        read -r -p "  Press Enter once the Xcode installer has finished: "
    else
        echo "  ✓ Xcode CLT OK."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
_banner

# Python check
echo "Checking Python..."
PYTHON=$(_find_python) || {
    if [ "$OS" = "Darwin" ]; then
        _abort "Python 3.11+ required.\nInstall: https://www.python.org/downloads/  or  brew install python@3.11"
    else
        _abort "Python 3.11+ required.\nInstall: sudo apt install python3.11  (Ubuntu/Debian)"
    fi
}
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  ✓ Python $PY_VER  ($PYTHON)"
echo ""

# OS-specific dependencies
echo "Installing system dependencies..."
if [ "$OS" = "Linux" ]; then
    if   command -v apt-get &>/dev/null; then _linux_deps_apt
    elif command -v dnf     &>/dev/null; then _linux_deps_dnf
    elif command -v pacman  &>/dev/null; then _linux_deps_pacman
    else echo "  ⚠  Unknown package manager — skipping. Install python3-dev and build tools manually if needed."
    fi
elif [ "$OS" = "Darwin" ]; then
    _macos_deps
fi
echo ""

# Virtual environment
echo "Creating virtual environment at $VENV..."
"$PYTHON" -m venv "$VENV"
echo "  ✓ Done."
echo ""

# ── Back up any existing install before upgrading (idempotent, reversible) ────
# Makes this zip safe to drop onto a machine that already runs delegation-core:
# the prior package is preserved so an upgrade can always be rolled back.
EXISTING_PKG=$(compgen -G "$VENV/lib/python*/site-packages/delegation_core" | head -1 || true)
if [ -n "$EXISTING_PKG" ]; then
    TS=$(date +%Y%m%d_%H%M%S)
    BK="$HOME/.delegation_core/backups_pre_upgrade_$TS"
    mkdir -p "$BK"
    cp -R "$EXISTING_PKG" "$BK/" 2>/dev/null || true
    cp -R "$VENV"/lib/python*/site-packages/delegation_core-*.dist-info "$BK/" 2>/dev/null || true
    echo "Existing install detected — backed up to:"
    echo "  $BK"
    echo ""
fi

# Install package
echo "Installing delegation-core and Python dependencies..."
echo "  (sentence-transformers and chromadb are large — this may take a few minutes)"
# v5.1 patch: pin setuptools<82. sentence-transformers pulls torch, and
# torch (2.x) requires setuptools<82. An unpinned `--upgrade setuptools` grabs
# 82.x and breaks the torch import on a fresh install. wheel/pip stay unpinned.
"$VENV/bin/pip" install --quiet --upgrade pip wheel "setuptools<82"
"$VENV/bin/pip" install "$SCRIPT_DIR"
echo "  ✓ Installation complete."
echo ""

# Copy agent docs and hooks to a stable location, independent of where this
# project folder ends up (the wizard wires Claude Code/Desktop up to these paths).
# Portability guard: NEVER clobber a doc the user has already customized (e.g. a
# translated AGENT_GUIDE). If one exists, keep theirs and drop the shipped copy
# alongside as <name>.dist.md so they can diff/merge deliberately.
echo "Installing agent docs and hooks to \$HOME/.delegation_core..."
mkdir -p "$HOME/.delegation_core/hooks"
_install_doc() {   # $1 = filename in $SCRIPT_DIR
    local src="$SCRIPT_DIR/$1" dst="$HOME/.delegation_core/$1"
    [ -f "$src" ] || return 0
    if [ -f "$dst" ]; then
        cp -f "$src" "$HOME/.delegation_core/${1%.md}.dist.md"
        echo "  • $1 already present — kept yours; shipped copy saved as ${1%.md}.dist.md"
    else
        cp -f "$src" "$dst"
        echo "  • $1 installed"
    fi
}
_install_doc AGENT_GUIDE.md
_install_doc CLAUDE_SYSTEM_PROMPT.md
cp -f "$SCRIPT_DIR/hooks/"*.py "$HOME/.delegation_core/hooks/" 2>/dev/null || true
echo "  ✓ Done."
echo ""

# ── Install bundled Claude skills to ~/.claude/skills (universal layer) ───────
# Personal skills here are available in every Claude Code session on this machine,
# independent of any plugin config — so the same skill set travels to any host the
# installer runs on. Guard: never clobber a skill the user already has by that name.
if [ -d "$SCRIPT_DIR/skills" ]; then
    echo "Installing bundled skills to \$HOME/.claude/skills..."
    mkdir -p "$HOME/.claude/skills"
    for d in "$SCRIPT_DIR/skills"/*/; do
        [ -d "$d" ] || continue
        sname=$(basename "$d")
        if [ -e "$HOME/.claude/skills/$sname" ]; then
            echo "  • $sname already present — kept yours"
        else
            cp -R "$d" "$HOME/.claude/skills/$sname"
            echo "  + $sname"
        fi
    done
    echo "  ✓ Skills available on next Claude Code session start."
    echo ""
fi

# ── Install the Tauri dashboard app ──────────────────────────────────────────
# Prefer a bundle already built locally (dev/CI convenience); otherwise fall
# back to the latest GitHub Release via `gh` if it's installed. Never fail the
# whole install over this — the Python/MCP side above is the part that matters
# most, so a missing dashboard just prints manual build instructions.
DASH_BUNDLE_DIR="$SCRIPT_DIR/dashboard/src-tauri/target/release/bundle"

_repo_slug() {
    local url
    # "origin" is the active repo that publishes releases. "fork" is only tried
    # for checkouts predating the 2026-08-31 remote rename, where the active repo
    # was named "fork" and "origin" pointed at the frozen delegation-core-v6.4 —
    # picking the wrong one makes the `gh release download` fallback below look
    # in a repo with no releases. Hardcoded default last.
    for remote in origin fork; do
        url=$(git -C "$SCRIPT_DIR" remote get-url "$remote" 2>/dev/null || true)
        if [[ "$url" =~ github\.com[:/]([^/]+/[^/.]+)(\.git)?$ ]]; then
            echo "${BASH_REMATCH[1]}"
            return
        fi
    done
    echo "AnonJoey/delegation-core-v7"
}

_no_dashboard_found() {
    echo "  ⚠  No dashboard build found locally or on GitHub releases."
    echo "     Build it manually:  cd dashboard && npm install && npm run tauri build"
}

_install_dashboard_linux() {
    local deb rpm appimage tmp
    deb=$(compgen -G "$DASH_BUNDLE_DIR/deb/*.deb" 2>/dev/null | head -1 || true)
    rpm=$(compgen -G "$DASH_BUNDLE_DIR/rpm/*.rpm" 2>/dev/null | head -1 || true)
    appimage=$(compgen -G "$DASH_BUNDLE_DIR/appimage/*.AppImage" 2>/dev/null | head -1 || true)

    if [ -z "$deb" ] && [ -z "$rpm" ] && [ -z "$appimage" ] && command -v gh &>/dev/null; then
        echo "  No local dashboard build found — checking the latest GitHub release..."
        tmp=$(mktemp -d)
        if command -v dpkg &>/dev/null; then
            gh release download --repo "$(_repo_slug)" --pattern '*.deb' --dir "$tmp" 2>/dev/null || true
            deb=$(compgen -G "$tmp/*.deb" 2>/dev/null | head -1 || true)
        elif command -v rpm &>/dev/null; then
            gh release download --repo "$(_repo_slug)" --pattern '*.rpm' --dir "$tmp" 2>/dev/null || true
            rpm=$(compgen -G "$tmp/*.rpm" 2>/dev/null | head -1 || true)
        else
            gh release download --repo "$(_repo_slug)" --pattern '*.AppImage' --dir "$tmp" 2>/dev/null || true
            appimage=$(compgen -G "$tmp/*.AppImage" 2>/dev/null | head -1 || true)
        fi
    fi

    if [ -n "$deb" ] && command -v dpkg &>/dev/null; then
        echo "  Installing $(basename "$deb") via dpkg (requires sudo)..."
        # Never let a dashboard packaging failure take down the whole install
        # (script runs under `set -e`) — the Python/MCP side above is what
        # matters most; report a clear warning instead of aborting.
        if sudo dpkg -i "$deb" || sudo apt-get install -f -y; then
            echo "  ✓ Dashboard installed — find \"delegation-core Dashboard\" in your applications menu."
        else
            echo "  ⚠  Dashboard install failed — try manually: sudo dpkg -i \"$deb\""
        fi
    elif [ -n "$rpm" ] && command -v rpm &>/dev/null; then
        echo "  Installing $(basename "$rpm") (requires sudo)..."
        if sudo rpm -i "$rpm" 2>/dev/null || sudo dnf install -y "$rpm"; then
            echo "  ✓ Dashboard installed — find \"delegation-core Dashboard\" in your applications menu."
        else
            echo "  ⚠  Dashboard install failed — try manually: sudo rpm -i \"$rpm\""
        fi
    elif [ -n "$appimage" ]; then
        mkdir -p "$HOME/.local/share/applications" "$HOME/.delegation_core/app"
        local dest="$HOME/.delegation_core/app/delegation-core-dashboard.AppImage"
        cp -f "$appimage" "$dest"
        chmod +x "$dest"

        # Install the real app icon into the standard XDG hicolor theme dirs
        # so the desktop entry below can reference it by name (Icon=<name>,
        # resolved by every desktop environment's icon theme lookup) instead
        # of falling back to a generic placeholder — this only works from a
        # source checkout that still has dashboard/src-tauri/icons/ next to
        # it; harmless no-op (falls back to the placeholder) if run from a
        # stripped-down distribution that doesn't include those source assets.
        local icon_src="$SCRIPT_DIR/dashboard/src-tauri/icons"
        local icon_name="utilities-terminal"  # fallback if the source icons aren't present
        if [ -f "$icon_src/128x128.png" ]; then
            mkdir -p "$HOME/.local/share/icons/hicolor/128x128/apps" \
                     "$HOME/.local/share/icons/hicolor/32x32/apps"
            cp -f "$icon_src/128x128.png" "$HOME/.local/share/icons/hicolor/128x128/apps/delegation-core-dashboard.png" 2>/dev/null || true
            [ -f "$icon_src/128x128@2x.png" ] && {
                mkdir -p "$HOME/.local/share/icons/hicolor/256x256@2/apps"
                cp -f "$icon_src/128x128@2x.png" "$HOME/.local/share/icons/hicolor/256x256@2/apps/delegation-core-dashboard.png" 2>/dev/null || true
            }
            [ -f "$icon_src/32x32.png" ] && cp -f "$icon_src/32x32.png" "$HOME/.local/share/icons/hicolor/32x32/apps/delegation-core-dashboard.png" 2>/dev/null || true
            icon_name="delegation-core-dashboard"
            command -v gtk-update-icon-cache &>/dev/null && gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
        fi

        cat > "$HOME/.local/share/applications/delegation-core-dashboard.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=delegation-core Dashboard
Exec=$dest
Icon=$icon_name
Categories=Utility;
EOF
        command -v update-desktop-database &>/dev/null && update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true
        echo "  ✓ AppImage installed to $dest — find \"delegation-core Dashboard\" in your applications menu."
    else
        _no_dashboard_found
    fi
}

_install_dashboard_macos() {
    local dmg tmp mnt app
    dmg=$(compgen -G "$DASH_BUNDLE_DIR/dmg/*.dmg" 2>/dev/null | head -1 || true)
    if [ -z "$dmg" ] && command -v gh &>/dev/null; then
        echo "  No local dashboard build found — checking the latest GitHub release..."
        tmp=$(mktemp -d)
        gh release download --repo "$(_repo_slug)" --pattern '*.dmg' --dir "$tmp" 2>/dev/null || true
        dmg=$(compgen -G "$tmp/*.dmg" 2>/dev/null | head -1 || true)
    fi
    if [ -n "$dmg" ]; then
        echo "  Mounting $(basename "$dmg")..."
        mnt=$(hdiutil attach "$dmg" -nobrowse -readonly | tail -1 | awk '{print $NF}')
        # Guard against a failed/garbled `hdiutil attach` (its exit code is lost
        # in the pipeline above): if $mnt ends up empty, "$mnt/*.app" collapses
        # to "/*.app" and would glob-match unrelated .app bundles at the
        # filesystem root, which the next step would then copy into
        # /Applications. Require a real mounted directory first.
        if [ -n "$mnt" ] && [ -d "$mnt" ]; then
            app=$(compgen -G "$mnt/*.app" 2>/dev/null | head -1 || true)
            if [ -n "$app" ]; then
                cp -R "$app" /Applications/
                echo "  ✓ Installed to /Applications/$(basename "$app")"
            fi
            hdiutil detach "$mnt" -quiet 2>/dev/null || true
        else
            echo "  ⚠  Could not mount $(basename "$dmg")."
            _no_dashboard_found
        fi
    else
        _no_dashboard_found
    fi
}

echo "Installing delegation-core Dashboard app..."
if [ "$OS" = "Linux" ]; then
    _install_dashboard_linux
elif [ "$OS" = "Darwin" ]; then
    _install_dashboard_macos
fi
echo ""

# Invalidate cached health so the corrected (recursive) broken-link metric
# recomputes on next start instead of serving a stale cached count.
rm -f "$HOME/.delegation_core/vault_health.json" 2>/dev/null || true

# ── Launch wizard only on a FRESH install ─────────────────────────────────────
# On an existing deployment the wizard would re-prompt and could overwrite a
# working config.json, so an upgrade must leave configuration untouched.
if [ -f "$HOME/.delegation_core/config.json" ]; then
    echo "Existing config.json detected — preserved. Skipping setup wizard."
    echo ""
    echo "Upgrade complete. Restart delegation-core (or your MCP client, e.g. quit"
    echo "and reopen Claude) to load the new code."
    echo "To reconfigure manually later:  $VENV/bin/delegation-core setup"
else
    echo "Launching setup wizard..."
    echo ""
    exec "$VENV/bin/delegation-core" setup
fi
