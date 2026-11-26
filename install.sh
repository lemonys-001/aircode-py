#!/usr/bin/env sh
set -eu

REPO="${AIRCODE_REPO:-lemonys-001/aircode-py}"
VERSION="${AIRCODE_VERSION:-latest}"
PREFIX="${AIRCODE_PREFIX:-$HOME/.local}"
SOURCE=0
GUI=0

usage() {
    cat <<'EOF'
Install Aircode from the latest GitHub release.

Usage:
  install.sh [options]

Options:
  --prefix DIR     Install under DIR (default: ~/.local)
  --version TAG    Release tag, for example v0.1.0 (default: latest)
  --source         Install from source instead of a release binary
  --gui            With --source, include PySide6 for the native window
  -h, --help       Show this help

Environment:
  AIRCODE_PREFIX   Same as --prefix
  AIRCODE_VERSION  Same as --version
  GITHUB_TOKEN     Token for a private repository, used by gh
EOF
}

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --source) SOURCE=1; shift ;;
        --gui) GUI=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

BIN_DIR="$PREFIX/bin"
mkdir -p "$BIN_DIR"

install_source() {
    here="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
    if [ -f "$here/pyproject.toml" ]; then
        spec="$here"
    else
        spec="aircode-py @ git+https://github.com/$REPO.git"
    fi
    if [ "$GUI" = 1 ]; then
        spec="$spec[gui]"
    fi
    if command -v uv >/dev/null 2>&1; then
        say "Installing from source with uv..."
        if UV_LINK_MODE=copy UV_TOOL_BIN_DIR="$BIN_DIR" uv tool install --force --python 3.13 "$spec"; then
            say "Installed $BIN_DIR/aircode"
            return
        fi
        say "uv failed; falling back to a Python venv."
    fi
    if command -v python3 >/dev/null 2>&1; then
        venv="${XDG_DATA_HOME:-$HOME/.local/share}/aircode/venv"
        say "Installing from source in $venv..."
        python3 -m venv "$venv"
        "$venv/bin/python" -m pip install --upgrade pip
        "$venv/bin/python" -m pip install "$spec" ||
            fail "pip could not install Aircode"
        ln -sf "$venv/bin/aircode" "$BIN_DIR/aircode"
        say "Installed $BIN_DIR/aircode"
        return
    fi
    fail "install uv or Python 3.11+ to install from source"
}

if [ "$SOURCE" = 1 ]; then
    install_source
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) say "Add $BIN_DIR to PATH to run aircode from any directory." ;;
    esac
    exit 0
fi

case "$(uname -s)" in
    Linux) OS=linux ;;
    Darwin) OS=macos ;;
    *) fail "unsupported operating system: $(uname -s)" ;;
esac

case "$(uname -m)" in
    x86_64|amd64) ARCH=x86_64 ;;
    arm64|aarch64) ARCH=aarch64 ;;
    *) fail "unsupported architecture: $(uname -m)" ;;
esac
if [ "$OS" = macos ] && [ "$ARCH" = aarch64 ]; then
    ARCH=arm64
fi

ASSET="aircode-$OS-$ARCH"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/aircode.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT INT TERM

if [ "$VERSION" = latest ]; then
    BASE="https://github.com/$REPO/releases/latest/download"
else
    BASE="https://github.com/$REPO/releases/download/$VERSION"
fi

downloaded=0
if command -v gh >/dev/null 2>&1; then
    if [ "$VERSION" = latest ]; then
        gh release download --repo "$REPO" --pattern "$ASSET" --dir "$tmp" >/dev/null 2>&1 || true
        gh release download --repo "$REPO" --pattern SHA256SUMS --dir "$tmp" >/dev/null 2>&1 || true
    else
        gh release download "$VERSION" --repo "$REPO" --pattern "$ASSET" --dir "$tmp" >/dev/null 2>&1 || true
        gh release download "$VERSION" --repo "$REPO" --pattern SHA256SUMS --dir "$tmp" >/dev/null 2>&1 || true
    fi
    [ -s "$tmp/$ASSET" ] && downloaded=1
fi

if [ "$downloaded" = 0 ]; then
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 -o "$tmp/$ASSET" "$BASE/$ASSET" || true
        curl -fL --retry 3 -o "$tmp/SHA256SUMS" "$BASE/SHA256SUMS" || true
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$tmp/$ASSET" "$BASE/$ASSET" || true
        wget -O "$tmp/SHA256SUMS" "$BASE/SHA256SUMS" || true
    else
        fail "install curl or wget"
    fi
    [ -s "$tmp/$ASSET" ] && downloaded=1
fi

if [ "$downloaded" = 0 ]; then
    say "No prebuilt binary found for $OS-$ARCH ($VERSION)."
    say "Installing from source instead."
    if [ "$GUI" = 0 ] && { [ "$OS" = macos ] || [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
        GUI=1
    fi
    install_source
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) say "Add $BIN_DIR to PATH to run aircode from any directory." ;;
    esac
    exit 0
fi

if [ -s "$tmp/SHA256SUMS" ]; then
    expected="$(awk -v asset="$ASSET" '$2 == asset { print $1; exit }' "$tmp/SHA256SUMS")"
    if [ -n "$expected" ]; then
        if command -v sha256sum >/dev/null 2>&1; then
            actual="$(sha256sum "$tmp/$ASSET" | awk '{print $1}')"
        elif command -v shasum >/dev/null 2>&1; then
            actual="$(shasum -a 256 "$tmp/$ASSET" | awk '{print $1}')"
        else
            actual=""
        fi
        if [ -n "$actual" ] && [ "$actual" != "$expected" ]; then
            fail "checksum mismatch for $ASSET"
        fi
    fi
fi

if command -v install >/dev/null 2>&1; then
    install -m 0755 "$tmp/$ASSET" "$BIN_DIR/aircode"
else
    cp "$tmp/$ASSET" "$BIN_DIR/aircode"
    chmod 0755 "$BIN_DIR/aircode"
fi

if [ "$OS" = macos ]; then
    xattr -d com.apple.quarantine "$BIN_DIR/aircode" 2>/dev/null || true
fi

say "Installed $BIN_DIR/aircode"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) say "Add $BIN_DIR to PATH to run aircode from any directory." ;;
esac
"$BIN_DIR/aircode" --version || true
