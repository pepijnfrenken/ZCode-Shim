#!/usr/bin/env sh
set -eu

# ── zcode-openai-sub-proxy installer ──────────────────────────────────────────
# One-command setup: copies the example config, runs a self-test, and
# optionally installs the launcher on your PATH.
#
# Usage:
#   ./install.sh                  # setup + doctor
#   ./install.sh --install-launcher  # also symlink into ~/.local/bin
#   ./install.sh --uninstall       # remove launcher symlink

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
LAUNCHER_SRC="$PROJECT_DIR/bin/zcode-openai-sub-proxy"
LAUNCHER_DST="$HOME/.local/bin/zcode-openai-sub-proxy"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Color

say()  { printf '%b\n' "$*"; }
info() { printf '  %b\n' "$*"; }
ok()   { printf "  ${GREEN}✓${NC} %b\n" "$*"; }
warn() { printf "  ${YELLOW}⚠${NC} %b\n" "$*"; }
err()  { printf "  ${RED}✗${NC} %b\n" "$*"; }

# ── Python version check ─────────────────────────────────────────────────────

check_python() {
    if ! command -v python3 >/dev/null 2>&1; then
        err "python3 not found on PATH — Python >= 3.11 is required"
        return 1
    fi
    PY_VER="$(python3 -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || true)"
    MAJOR="$(echo "$PY_VER" | cut -d',' -f1 | tr -d ' ()')"
    MINOR="$(echo "$PY_VER" | cut -d',' -f2 | tr -d ' ()')"
    if [ "${MAJOR:-0}" -lt 3 ] || { [ "${MAJOR:-0}" -eq 3 ] && [ "${MINOR:-0}" -lt 11 ]; }; then
        err "Python >= 3.11 required (found ${MAJOR:-?}.${MINOR:-?})"
        return 1
    fi
    ok "python3 $MAJOR.$MINOR found"
}

# ── Auth file setup ───────────────────────────────────────────────────────────

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
CODEX_AUTH="$CODEX_HOME/auth.json"

setup_auth() {
    AUTH_FILE="$PROJECT_DIR/data/auth.json"
    AUTH_EXAMPLE="$PROJECT_DIR/data/auth.example.json"

    # Check if Codex CLI auth already exists (no setup needed).
    if [ -f "$CODEX_AUTH" ]; then
        ok "Codex CLI auth found at $CODEX_AUTH — proxy will use it automatically"
        return 0
    fi

    # Check if codex CLI is available and suggest login.
    if command -v codex >/dev/null 2>&1; then
        warn "Run \`codex login\` to set up auth — no manual editing needed"
    else
        # Offer our built-in device-code login script.
        if [ -f "$PROJECT_DIR/scripts/codex-login.py" ]; then
            warn "Run \`python3 scripts/codex-login.py\` to log in (browser device-code flow)"
        fi
    fi

    if [ -f "$AUTH_FILE" ]; then
        ok "data/auth.json already exists"
        return 0
    fi

    if [ ! -f "$AUTH_EXAMPLE" ]; then
        err "data/auth.example.json not found — cannot create auth.json"
        return 1
    fi

    cp "$AUTH_EXAMPLE" "$AUTH_FILE"
    ok "created data/auth.json from template"
    warn "Set up auth (pick one):"
    warn "  1. Run \`codex login\` (recommended)"
    warn "  2. Set ZCODE_OPENAI_SUB_TOKEN env var"
    warn "  3. Edit data/auth.json with your token"
}

# ── Launcher install / uninstall ──────────────────────────────────────────────

install_launcher() {
    if [ ! -f "$LAUNCHER_SRC" ]; then
        err "launcher not found at $LAUNCHER_SRC"
        return 1
    fi

    mkdir -p "$(dirname "$LAUNCHER_DST")"

    if [ -L "$LAUNCHER_DST" ] || [ -f "$LAUNCHER_DST" ]; then
        warn "launcher already exists at $LAUNCHER_DST (skipping)"
        return 0
    fi

    ln -s "$LAUNCHER_SRC" "$LAUNCHER_DST"
    ok "symlinked launcher → $LAUNCHER_DST"

    if ! echo "$PATH" | tr ':' '\n' | grep -qxF "$(dirname "$LAUNCHER_DST")"; then
        warn "~/.local/bin is not on your PATH — add it to your shell profile"
    fi
}

uninstall_launcher() {
    if [ -L "$LAUNCHER_DST" ]; then
        rm "$LAUNCHER_DST"
        ok "removed launcher symlink from $LAUNCHER_DST"
    elif [ -f "$LAUNCHER_DST" ]; then
        warn "$LAUNCHER_DST exists but is not a symlink (not removing)"
    else
        info "no launcher found at $LAUNCHER_DST"
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

INSTALL_LAUNCHER=false
UNINSTALL=false

for arg in "$@"; do
    case "$arg" in
        --install-launcher) INSTALL_LAUNCHER=true ;;
        --uninstall)        UNINSTALL=true ;;
        -h|--help)
            say "Usage: ./install.sh [--install-launcher] [--uninstall]"
            exit 0
            ;;
        *) err "unknown flag: $arg"; exit 1 ;;
    esac
done

say ""
say "${BOLD}zcode-openai-sub-proxy — installer${NC}"
say ""

if $UNINSTALL; then
    uninstall_launcher
    say ""
    exit 0
fi

check_python
setup_auth

say ""
say "${BOLD}Running self-test…${NC}"
say ""

DOCTOR_EXIT=0
PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
    python3 "$PROJECT_DIR/scripts/doctor.py" || DOCTOR_EXIT=$?

say ""
if [ "$DOCTOR_EXIT" -eq 0 ]; then
    if $INSTALL_LAUNCHER; then
        install_launcher
    fi
    say ""
    say "${GREEN}${BOLD}All checks passed.${NC}"
    say ""
    say "Next steps:"
    say "  1. ${BOLD}Set up auth${NC}:"
    say "     a) Device-code flow: ${BOLD}python3 scripts/codex-login.py${NC}"
    say "     b) Or install Codex CLI: ${BOLD}npm i -g @openai/codex && codex login${NC}"
    say "     c) Or set ${BOLD}ZCODE_OPENAI_SUB_TOKEN${NC} env var"
    say "     d) Or edit ${BOLD}data/auth.json${NC} manually"
    say "  2. Start the proxy: ${BOLD}bin/zcode-openai-sub-proxy${NC}"
    if ! $INSTALL_LAUNCHER; then
        say "     (run with ${BOLD}--install-launcher${NC} to add the launcher to ~/.local/bin)"
    fi
    say ""
else
    say ""
    say "${RED}${BOLD}Some checks failed.${NC} Fix the issues above and re-run install.sh."
    say ""
    exit 1
fi
