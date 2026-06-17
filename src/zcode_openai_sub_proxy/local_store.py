from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from .config import AUTH_FILE, AUTH_EXAMPLE_FILE, DEFAULT_MODELS_FILE, MODELS_FILE

_credential_cache: dict[str, Any] | None = None

# ── Codex CLI auth path (following UltraCode-Shim / oh-my-pi patterns) ────────

CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
CODEX_AUTH_FILE = CODEX_HOME / "auth.json"
CODEX_REFRESH_CMD = os.environ.get("ZCODE_CODEX_REFRESH_CMD", "codex login status")

# OAuth token endpoint for direct refresh (from oh-my-pi's openai-codex.ts).
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    """Decode JWT payload claims without verification (base64 + JSON)."""
    try:
        payload_b64 = token.split(".")[1]
        # Pad to a multiple of 4 characters for base64 decoding.
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = base64.urlsafe_b64decode(payload_b64).decode("utf-8")
        claims = json.loads(payload)
        if isinstance(claims, dict):
            return claims
    except Exception:
        pass
    return {}


def _account_id_from_jwt(token: str) -> str:
    """Extract the ChatGPT account ID from JWT claims."""
    claims = _decode_jwt_claims(token)
    auth = claims.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        account_id = auth.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id.strip():
            return account_id
    return ""


def _is_expiring(token: str, skew: int = 120) -> bool:
    """Check if the token is near expiry.

    Checks both:
      - JWT ``exp`` claim (spec time)
      - Explicit ``expires`` field in the stored auth dict
    """
    claims = _decode_jwt_claims(token)
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and time.time() >= (exp - skew):
        return True
    return False


def _best_effort_refresh() -> None:
    """Attempt a best-effort token refresh.

    Tries the Codex CLI first (``codex login status``), then falls back to
    direct OAuth refresh using the stored ``refresh_token``.
    """
    # Strategy 1: Codex CLI subprocess (works if user ran `codex login`).
    if CODEX_REFRESH_CMD:
        try:
            subprocess.run(
                CODEX_REFRESH_CMD.split(),
                timeout=25,
                capture_output=True,
                check=False,
            )
        except Exception:
            pass

    # Strategy 2: Direct OAuth refresh using the stored refresh_token
    # (works if user ran our scripts/codex-login.py or we have a refresh_token).
    # This is handled inline in _read_credential() where we have the data.


def _refresh_with_token(refresh_token: str) -> dict[str, Any] | None:
    """Exchange a refresh_token for new tokens via the OAuth token endpoint.

    Returns a dict with ``access_token``, ``refresh_token``, ``expires_at``,
    ``account_id`` on success, or ``None`` on failure.
    """
    body = (
        f"grant_type=refresh_token"
        f"&refresh_token={refresh_token}"
        f"&client_id={CODEX_CLIENT_ID}"
    )
    req = urllib.request.Request(
        CODEX_OAUTH_TOKEN_URL,
        method="POST",
        data=body.encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (urllib.error.HTTPError, Exception):
        return None

    access_token = data.get("access_token")
    new_refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")

    if not access_token or not new_refresh or not isinstance(expires_in, (int, float)):
        return None

    account_id = _account_id_from_jwt(access_token)
    if not account_id:
        return None

    return {
        "access_token": access_token,
        "refresh_token": new_refresh,
        "expires_at": int(time.time() + expires_in),
        "account_id": account_id,
    }


# ── Credential reading (cached) ────────────────────────────────────────────────


def _read_credential() -> dict[str, Any]:
    """Read and cache the parsed auth.json credential dict.

    Sources checked in order:
      1. ``~/.codex/auth.json`` (Codex CLI format: ``tokens.access_token``)
      2. ``data/auth.json`` (local format: ``"access"`` field)
    """
    global _credential_cache
    if _credential_cache is not None:
        return _credential_cache

    credential: dict[str, Any] = {}

    # ── Try Codex CLI auth first (following UltraCode-Shim / oh-my-pi) ─────
    if CODEX_AUTH_FILE.is_file():
        try:
            codex_data = json.loads(CODEX_AUTH_FILE.read_text())
        except Exception:
            codex_data = {}

        if isinstance(codex_data, dict):
            tokens = codex_data.get("tokens") or {}
            if isinstance(tokens, dict):
                token = tokens.get("access_token")
                if isinstance(token, str) and token.strip():
                    credential["access"] = token
                    credential["_source"] = str(CODEX_AUTH_FILE)

                    # Account ID: prefer stored, fall back to JWT extraction.
                    account_id = tokens.get("account_id")
                    if not isinstance(account_id, str) or not account_id.strip():
                        account_id = _account_id_from_jwt(token)
                    if account_id:
                        credential["accountId"] = account_id

                    # Check expiry from either JWT or explicit expires field.
                    stored_expires = tokens.get("expires")
                    is_near_expiry = _is_expiring(token)
                    if not is_near_expiry and isinstance(stored_expires, (int, float)):
                        is_near_expiry = time.time() >= (stored_expires - 120)

                    if is_near_expiry:
                        # Try Codex CLI refresh first.
                        if CODEX_REFRESH_CMD:
                            try:
                                subprocess.run(
                                    CODEX_REFRESH_CMD.split(),
                                    timeout=25,
                                    capture_output=True,
                                    check=False,
                                )
                            except Exception:
                                pass

                        # Try direct OAuth refresh with stored refresh_token.
                        refresh_token = tokens.get("refresh_token")
                        if isinstance(refresh_token, str) and refresh_token.strip():
                            refreshed = _refresh_with_token(refresh_token)
                            if refreshed:
                                credential["access"] = refreshed["access_token"]
                                credential["accountId"] = refreshed["account_id"]
                                # Write refreshed tokens back to disk.
                                try:
                                    new_auth = {
                                        "tokens": {
                                            "access_token": refreshed["access_token"],
                                            "refresh_token": refreshed["refresh_token"],
                                            "expires": refreshed["expires_at"],
                                            "account_id": refreshed["account_id"],
                                        }
                                    }
                                    CODEX_AUTH_FILE.write_text(
                                        json.dumps(new_auth, indent=2) + "\n",
                                        encoding="utf-8",
                                    )
                                    CODEX_AUTH_FILE.chmod(0o600)
                                except Exception:
                                    pass  # Best-effort; token still works in memory.
                        else:
                            # Re-read from disk (Codex CLI may have refreshed it).
                            try:
                                codex_data = json.loads(CODEX_AUTH_FILE.read_text())
                                tokens2 = (codex_data.get("tokens") or {}) if isinstance(codex_data, dict) else {}
                                if isinstance(tokens2, dict):
                                    refreshed_token = tokens2.get("access_token")
                                    if isinstance(refreshed_token, str) and refreshed_token.strip():
                                        credential["access"] = refreshed_token
                                        account_id = tokens2.get("account_id")
                                        if not isinstance(account_id, str) or not account_id.strip():
                                            account_id = _account_id_from_jwt(refreshed_token)
                                        if account_id:
                                            credential["accountId"] = account_id
                            except Exception:
                                pass

    # ── Fall back to local data/auth.json ──────────────────────────────────
    if not credential.get("access"):
        # Auto-copy example file if auth.json doesn't exist yet.
        if not AUTH_FILE.exists() and AUTH_EXAMPLE_FILE.exists():
            AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(AUTH_EXAMPLE_FILE, AUTH_FILE)
            print(
                f"Created {AUTH_FILE} from template.\n"
                "Edit it with your ChatGPT/Codex access token, then restart.",
                file=sys.stderr,
            )
            raise RuntimeError("auth.json created from template — edit it and restart")

        try:
            local_data = json.loads(AUTH_FILE.read_text())
            if isinstance(local_data, dict):
                local_token = local_data.get("access")
                if isinstance(local_token, str) and local_token.strip():
                    credential["access"] = local_token
                    credential["_source"] = str(AUTH_FILE)
                    # Optionally carry over explicit accountId from local config.
                    local_account = local_data.get("accountId")
                    if isinstance(local_account, str) and local_account.strip():
                        credential["accountId"] = local_account
        except Exception:
            raise RuntimeError("invalid standalone OpenAI subscription credential")

    if not isinstance(credential.get("access"), str) or not credential["access"].strip():
        raise RuntimeError("invalid standalone OpenAI subscription token")

    _credential_cache = credential
    return credential


def read_openai_codex_credential() -> dict[str, Any]:
    """Return the full OpenAI Codex credential dictionary."""
    return _read_credential()


def read_openai_codex_token() -> str:
    """Return the Codex access token.

    Prefers the ``ZCODE_OPENAI_SUB_TOKEN`` environment variable.
    Falls back to ``~/.codex/auth.json`` (Codex CLI), then ``data/auth.json``.
    """
    env_token = os.environ.get("ZCODE_OPENAI_SUB_TOKEN", "").strip()
    if env_token:
        return env_token

    token = _read_credential().get("access")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("invalid standalone OpenAI subscription token")
    return token


def read_openai_codex_account_id() -> str:
    """Return the optional ChatGPT account ID.

    Sources checked in order:
      1. Extracted from ``ZCODE_OPENAI_SUB_TOKEN`` env var
      2. Explicit ``accountId`` in ``data/auth.json``
      3. Extracted from JWT claims (when using Codex CLI auth)
    """
    env_token = os.environ.get("ZCODE_OPENAI_SUB_TOKEN", "").strip()
    if env_token:
        return _account_id_from_jwt(env_token)

    credential = _read_credential()
    account_id = credential.get("accountId")
    if isinstance(account_id, str) and account_id.strip():
        return account_id

    # If no explicit accountId, try to extract from the JWT.
    token = credential.get("access")
    if isinstance(token, str) and token.strip():
        return _account_id_from_jwt(token)

    return ""


def read_models() -> list[dict[str, Any]]:
    """Return the list of model definitions from data/models.json."""
    path = MODELS_FILE if MODELS_FILE.is_file() else DEFAULT_MODELS_FILE
    models = json.loads(path.read_text())
    if not isinstance(models, list):
        return []
    return [model for model in models if isinstance(model, dict)]
