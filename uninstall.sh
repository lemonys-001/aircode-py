#!/usr/bin/env sh
set -eu

APP="aircode-py"
PREFIX="${AIRCODE_PREFIX:-$HOME/.local}"
PURGE=0
YES=0
DRY=0

info() { printf '\033[1;34m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
fail() { printf '\033[1;31merror: %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Uninstall aircode-py.

Usage:
  uninstall.sh [options]

Options:
  --prefix DIR   Install root used by install.sh (default: ~/.local)
  --purge        Also delete user data, config and cache
  --dry-run      Print what would be removed
  --yes          Do not prompt
  -h, --help     Show this help

User data is kept by default so a reinstall can resume.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        --purge) PURGE=1; shift ;;
        --dry-run) DRY=1; shift ;;
        --yes|-y) YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

BIN_DIR="$PREFIX/bin"
LAUNCHER="$BIN_DIR/$APP"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/aircode"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/aircode"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/aircode"
VENV="${XDG_DATA_HOME:-$HOME/.local/share}/aircode-py/venv"
UV_TOOL="${UV_TOOL_DIR:-$HOME/.local/share/uv/tools}/aircode-py"

targets=""
add() { if [ -e "$1" ] || [ -L "$1" ]; then if [ -z "$targets" ]; then targets="$1"; else targets="$targets
$1"; fi; fi; }
add "$LAUNCHER"
add "$VENV"
add "$UV_TOOL"
if [ "$PURGE" = 1 ]; then
    add "$DATA"
    add "$CONFIG"
    add "$CACHE"
    add "${XDG_DATA_HOME:-$HOME/.local/share}/aircode-py"
fi

if [ -z "$targets" ]; then
    info "Nothing to uninstall: no aircode-py installation found."
    exit 0
fi

info "The following will be removed:"
printf '%s\n' "$targets" | while IFS= read -r path; do [ -n "$path" ] && printf '  - %s\n' "$path"; done
if [ "$PURGE" = 0 ]; then
    warn "User data in $DATA, $CONFIG and $CACHE is kept. Use --purge for a full wipe."
fi

if [ "$DRY" = 1 ]; then
    info "Dry run: nothing was deleted."
    exit 0
fi

if [ "$YES" = 0 ]; then
    if [ -t 0 ]; then
        printf 'Proceed? [y/N] '
        read -r reply
        case "$reply" in
            y|Y|yes|YES) ;;
            *) info "Aborted."; exit 1 ;;
        esac
    else
        fail "stdin is not a terminal; re-run with --yes"
    fi
fi

printf '%s\n' "$targets" | while IFS= read -r path; do
    [ -n "$path" ] || continue
    rm -rf -- "$path"
    info "Removed $path"
done

line="export PATH=\"$BIN_DIR:\$PATH\""
for rc in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.zshenv"; do
    [ -f "$rc" ] || continue
    if grep -qF "$line" "$rc" 2>/dev/null; then
        grep -vF "$line" "$rc" > "$rc.tmp" && mv "$rc.tmp" "$rc"
    fi
done
info "aircode-py uninstalled."
