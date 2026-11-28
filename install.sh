#!/usr/bin/env sh
set -eu

REPO="${AIRCODE_REPO:-lemonys-001/aircode-py}"
APP="aircode-py"
VERSION="${AIRCODE_VERSION:-latest}"
PREFIX="${AIRCODE_PREFIX:-$HOME/.local}"
SOURCE=0
GUI=0
MODIFY_PATH=1

BIN_DIR="$PREFIX/bin"
INSTALLED="$BIN_DIR/$APP"

info() { printf '\033[1;34m%s\033[0m\n' "$*"; }
say() { printf '%s\n' "$*"; }
fail() { printf '\033[1;31merror: %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Install aircode-py from a GitHub release.

Usage:
  install.sh [options]

Options:
  --prefix DIR       Install under DIR (default: ~/.local)
  --version TAG      Release tag, for example v0.1.0 (default: latest)
  --source           Install from source instead of a release binary
  --gui              With --source, include PySide6 for the native window
  --no-modify-path   Do not edit shell startup files
  -h, --help         Show this help

Environment:
  AIRCODE_PREFIX     Same as --prefix
  AIRCODE_VERSION    Same as --version
  AIRCODE_REPO       GitHub repository (default: lemonys-001/aircode-py)
  GITHUB_TOKEN       Optional token for gh, for example on a private fork
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --source) SOURCE=1; shift ;;
        --gui) GUI=1; shift ;;
        --no-modify-path) MODIFY_PATH=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done
BIN_DIR="$PREFIX/bin"
INSTALLED="$BIN_DIR/$APP"
mkdir -p "$BIN_DIR"

add_to_path() {
    [ "$MODIFY_PATH" = 1 ] || return 0
    line="export PATH=\"$BIN_DIR:\$PATH\""
    touched=""
    add_rc() {
        rc="$1"
        [ -f "$rc" ] || return 0
        if ! grep -qF "$BIN_DIR" "$rc" 2>/dev/null; then
            printf '\n# Added by aircode-py installer\n%s\n' "$line" >> "$rc"
            touched="$touched $rc"
        fi
    }
    add_rc "$HOME/.profile"
    add_rc "$HOME/.bashrc"
    add_rc "$HOME/.zshrc"
    add_rc "$HOME/.zshenv"
    fish_rc="${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish"
    if [ -f "$fish_rc" ] && ! grep -qF "$BIN_DIR" "$fish_rc" 2>/dev/null; then
        {
            printf '\n# Added by aircode-py installer\n'
            printf 'if not contains "%s" $PATH\n' "$BIN_DIR"
            printf '    set -gx PATH "%s" $PATH\n' "$BIN_DIR"
            printf 'end\n'
        } >> "$fish_rc"
        touched="$touched $fish_rc"
    fi
    if [ -n "$touched" ]; then
        info "Added $BIN_DIR to PATH in:$touched"
    fi
}

finish() {
    add_to_path
    info "aircode-py installed at $INSTALLED"
    case ":$PATH:" in
        *":$BIN_DIR:"*) info "Run: aircode-py" ;;
        *)
            say ""
            say "Start it in this terminal with:"
            say "  export PATH=\"$BIN_DIR:\$PATH\" && aircode-py"
            ;;
    esac
}

install_source() {
    here=""
    case "$0" in
        *install.sh) here="$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd || pwd)" ;;
    esac
    if [ -n "$here" ] && [ -f "$here/pyproject.toml" ]; then
        spec="$here"
    else
        spec="aircode-py @ git+https://github.com/$REPO.git"
    fi
    if [ "$GUI" = 1 ]; then
        spec="$spec[gui]"
    fi
    if command -v uv >/dev/null 2>&1; then
        info "Installing aircode-py from source with uv..."
        if UV_LINK_MODE=copy UV_TOOL_BIN_DIR="$BIN_DIR" uv tool install --force --python 3.13 "$spec"; then
            finish
            return 0
        fi
        say "uv failed; falling back to a Python venv."
    fi
    if command -v python3 >/dev/null 2>&1; then
        venv="${XDG_DATA_HOME:-$HOME/.local/share}/aircode-py/venv"
        info "Installing aircode-py from source in $venv..."
        python3 -m venv "$venv"
        "$venv/bin/python" -m pip install --upgrade pip
        "$venv/bin/python" -m pip install "$spec" || fail "pip could not install aircode-py"
        ln -sf "$venv/bin/aircode-py" "$INSTALLED"
        finish
        return 0
    fi
    fail "install uv or Python 3.11+ to install from source"
}

if [ "$SOURCE" = 1 ]; then
    install_source
    exit 0
fi

case "$(uname -s)" in
    Linux) OS=linux ;;
    Darwin) OS=macos ;;
    *)
        info "No prebuilt binary for $(uname -s). Installing from source."
        install_source
        exit 0
        ;;
esac

case "$(uname -m)" in
    x86_64|amd64) ARCH=x86_64 ;;
    arm64|aarch64) ARCH=aarch64 ;;
    *)
        info "No prebuilt binary for $OS-$(uname -m). Installing from source."
        install_source
        exit 0
        ;;
esac
if [ "$OS" = macos ] && [ "$ARCH" = aarch64 ]; then
    ARCH=arm64
fi

ASSET="$APP-$OS-$ARCH"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/aircode-py.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT INT TERM

if [ "$VERSION" = latest ]; then
    BASE="https://github.com/$REPO/releases/latest/download"
else
    BASE="https://github.com/$REPO/releases/download/$VERSION"
fi

downloaded=0
if command -v gh >/dev/null 2>&1; then
    if [ "$VERSION" = latest ]; then
        gh release download --repo "$REPO" --pattern "$ASSET" --pattern SHA256SUMS --dir "$tmp" >/dev/null 2>&1 || true
    else
        gh release download "$VERSION" --repo "$REPO" --pattern "$ASSET" --pattern SHA256SUMS --dir "$tmp" >/dev/null 2>&1 || true
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
    info "No prebuilt binary for $OS-$ARCH ($VERSION). Installing from source."
    if [ "$GUI" = 0 ] && { [ "$OS" = macos ] || [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
        GUI=1
    fi
    install_source
    exit 0
fi

[ -s "$tmp/SHA256SUMS" ] || fail "release is missing SHA256SUMS; refusing to install"
expected="$(awk -v asset="$ASSET" '$2 == asset || $2 == "*" asset { print tolower($1); exit }' "$tmp/SHA256SUMS")"
[ -n "$expected" ] || fail "SHA256SUMS does not list $ASSET"
if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$tmp/$ASSET" | awk '{print tolower($1)}')"
elif command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$tmp/$ASSET" | awk '{print tolower($1)}')"
elif command -v openssl >/dev/null 2>&1; then
    actual="$(openssl dgst -sha256 "$tmp/$ASSET" | awk '{print tolower($NF)}')"
else
    fail "sha256sum, shasum, or openssl is required to verify the download"
fi
[ "$actual" = "$expected" ] || fail "SHA-256 verification failed for $ASSET"

if command -v install >/dev/null 2>&1; then
    install -m 0755 "$tmp/$ASSET" "$INSTALLED"
else
    cp "$tmp/$ASSET" "$INSTALLED"
    chmod 0755 "$INSTALLED"
fi
if [ "$OS" = macos ]; then
    xattr -d com.apple.quarantine "$INSTALLED" 2>/dev/null || true
fi
finish
