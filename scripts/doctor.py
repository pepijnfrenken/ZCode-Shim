"""Self-test / health-check for zcode-openai-sub-proxy.

Verifies that the environment is ready to run the proxy.  Run via:

    python3 scripts/doctor.py

or automatically from ``install.sh``.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("ZCODE_OPENAI_SUB_PROXY_DATA", PROJECT_ROOT / "data"))
AUTH_FILE = DATA_DIR / "auth.json"
AUTH_EXAMPLE_FILE = DATA_DIR / "auth.example.json"
MODELS_FILE = DATA_DIR / "models.json"
CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
CODEX_AUTH_FILE = CODEX_HOME / "auth.json"
CODEX_HOST = "chatgpt.com"
CODEX_PORT = 443
PROXY_PORT = 48765

_errors = 0
_warnings = 0


def _ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")


def _warn(msg: str) -> None:
    global _warnings
    _warnings += 1
    print(f"  \033[33m⚠\033[0m {msg}")


def _err(msg: str) -> None:
    global _errors
    _errors += 1
    print(f"  \033[31m✗\033[0m {msg}")


def check_python() -> None:
    """Verify Python >= 3.11."""
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 11):
        _ok(f"Python {major}.{minor} (>= 3.11)")
    else:
        _err(f"Python {major}.{minor} found — need >= 3.11")


def _check_token(token: str, source_label: str) -> bool:
    """Validate a token from a given source. Returns True if usable."""
    if not isinstance(token, str) or not token.strip():
        _err(f"{source_label}: token is missing or empty")
        return False

    if token == "paste-chatgpt-codex-access-token-here":
        _warn(f"{source_label} still contains the placeholder token — update it with your real token")
        return False
    elif token.startswith("eyJ"):
        _ok(f"{source_label} found with token (looks like a JWT)")
        return True
    else:
        _warn(f"{source_label} token doesn't look like a JWT — verify it's correct")
        return True


def check_auth_file() -> None:
    """Verify auth sources exist and at least one has a usable token.

    Checks in priority order (matching local_store.py):
      1. ZCODE_OPENAI_SUB_TOKEN env var
      2. ~/.codex/auth.json (Codex CLI)
      3. data/auth.json (manual / local)
    """
    any_ok = False

    # 1. Environment variable (highest priority).
    env_token = os.environ.get("ZCODE_OPENAI_SUB_TOKEN", "")
    if env_token.strip():
        _ok("ZCODE_OPENAI_SUB_TOKEN env var is set (takes precedence)")
        any_ok = True

    # 2. Codex CLI auth (~/.codex/auth.json).
    if CODEX_AUTH_FILE.is_file():
        try:
            data = json.loads(CODEX_AUTH_FILE.read_text())
        except json.JSONDecodeError as exc:
            _warn(f"{CODEX_AUTH_FILE} is not valid JSON: {exc}")
        else:
            if isinstance(data, dict):
                token = (data.get("tokens") or {}).get("access_token")
                if _check_token(token, str(CODEX_AUTH_FILE)):
                    any_ok = True
    else:
        _warn(f"{CODEX_AUTH_FILE} not found — run `python3 scripts/codex-login.py` or `codex login` to create it (recommended)")

    # 3. Local data/auth.json.
    if AUTH_FILE.exists():
        try:
            data = json.loads(AUTH_FILE.read_text())
        except json.JSONDecodeError as exc:
            _err(f"{AUTH_FILE} is not valid JSON: {exc}")
        else:
            if not isinstance(data, dict):
                _err(f"{AUTH_FILE} must contain a JSON object")
            else:
                token = data.get("access")
                if _check_token(token, str(AUTH_FILE)):
                    any_ok = True
    else:
        _warn(f"{AUTH_FILE} not found — run install.sh or copy {AUTH_EXAMPLE_FILE}")

    if not any_ok:
        _err(
            "No valid auth source found. Options:\n"
            "  1. Run `python3 scripts/codex-login.py` (device-code flow, recommended)\n"
            "  2. Run `codex login` (if Codex CLI is installed)\n"
            "  3. Set ZCODE_OPENAI_SUB_TOKEN env var\n"
            "  4. Edit data/auth.json with your token"
        )


def check_models_file() -> None:
    """Verify data/models.json exists and has valid models."""
    if not MODELS_FILE.exists():
        _err(f"{MODELS_FILE} not found")
        return

    try:
        models = json.loads(MODELS_FILE.read_text())
    except json.JSONDecodeError as exc:
        _err(f"{MODELS_FILE} is not valid JSON: {exc}")
        return

    if not isinstance(models, list):
        _err(f"{MODELS_FILE} must contain a JSON array")
        return

    valid = [m for m in models if isinstance(m, dict) and isinstance(m.get("id"), str)]
    if valid:
        _ok(f"{MODELS_FILE} loaded with {len(valid)} model(s)")
    else:
        _warn(f"{MODELS_FILE} has no valid model entries")


def check_upstream_connectivity() -> None:
    """Check we can reach the ChatGPT Codex backend."""
    try:
        with socket.create_connection((CODEX_HOST, CODEX_PORT), timeout=10):
            _ok(f"can reach {CODEX_HOST}:{CODEX_PORT}")
    except OSError as exc:
        _warn(f"cannot reach {CODEX_HOST}:{CODEX_PORT} — {exc}")


def check_proxy_port() -> None:
    """Check whether the proxy port is already in use."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", PROXY_PORT))
        _ok(f"port {PROXY_PORT} is free")
    except OSError:
        _err(f"port {PROXY_PORT} is already in use — is another instance running?")
    finally:
        sock.close()


def main() -> int:
    print()
    print("\033[1mzcode-openai-sub-proxy — doctor\033[0m")
    print()

    check_python()
    check_auth_file()
    check_models_file()
    check_upstream_connectivity()
    check_proxy_port()

    print()
    if _errors == 0:
        suffix = f"({_warnings} warning(s))" if _warnings else ""
        print(f"\033[32m\033[1mAll checks passed.\033[0m {suffix}")
    else:
        print(f"\033[31m\033[1m{_errors} error(s), {_warnings} warning(s)\033[0m")

    print()
    return 0 if _errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
