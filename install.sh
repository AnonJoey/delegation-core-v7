#!/usr/bin/env bash
# delegation-core installer: Linux / macOS
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

# ── 2. Linux : system packages ───────────────────────────────────────────────
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

# ── 3. macOS : Xcode CLT ─────────────────────────────────────────────────────
_macos_deps() {
    echo "  Checking Xcode Command Line Tools..."
    if ! xcode-select -p &>/dev/null 2>&1; then
        echo "  Not found. Starting installer..."
        echo "  A dialog will appear: click Install, then come back and press Enter."
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
    else echo "  ⚠  Unknown package manager: skipping. Install python3-dev and build tools manually if needed."
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
    echo "Existing install detected: backed up to:"
    echo "  $BK"
    echo ""
fi

# Install package
echo "Installing delegation-core and Python dependencies..."
echo "  (sentence-transformers and chromadb are large: this may take a few minutes)"
# v5.1 patch: pin setuptools<82. sentence-transformers pulls torch, and
# torch (2.x) requires setuptools<82. An unpinned `--upgrade setuptools` grabs
# 82.x and breaks the torch import on a fresh install. wheel/pip stay unpinned.
"$VENV/bin/pip" install --quiet --upgrade pip wheel "setuptools<82"
if ! "$VENV/bin/pip" install "$SCRIPT_DIR[graph,web]" 2>/dev/null; then
    "$VENV/bin/pip" install "$SCRIPT_DIR"
fi
echo "  ✓ Installation complete."
echo ""

# ── Everything from here used to be ~230 more lines of bash, transcribed again
# into install.bat and already drifted between the two: the .bat refreshed the
# MCP client config on an upgrade and this file did not. It now lives in
# delegation_core/installer.post_install(), which the three platforms share:
# agent docs and hooks, bundled skills, the Tauri dashboard, the health cache,
# the service registration, and the client configs.
"$VENV/bin/delegation-core" post-install --root "$SCRIPT_DIR"
