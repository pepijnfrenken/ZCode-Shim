#!/usr/bin/env python3
"""Codex login — device-code OAuth flow for ChatGPT/Codex subscription tokens.

Implements the same OAuth flow used by the official Codex CLI and oh-my-pi
(`openai-codex-device` provider).  Writes credentials to ``~/.codex/auth.json``
in the format the proxy already reads (``local_store.py``).

Usage::

    python3 scripts/codex-login.py

The script is pure Python standard library — no pip dependencies.

Flow:
  1. Request a device code from OpenAI's device-auth endpoint.
  2. Print instructions: open a URL in your browser and enter a short code.
  3. Poll until you complete the browser login.
  4. Exchange the authorization code for access + refresh tokens.
  5. Write ``~/.codex/auth.json``.

Reference:
  https://github.com/can1357/oh-my-pi/blob/main/packages/ai/src/registry/oauth/openai-codex.ts
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# ── Constants (from oh-my-pi's openai-codex.ts) ───────────────────────────────

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
DEVICE_USERCODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
DEVICE_AUTH_URL = "https://auth.openai.com/codex/device"
DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"

POLL_INTERVAL_S = 5
POLL_SAFETY_MARGIN_S = 3
MAX_POLLS = 120
TIMEOUT_S = 15

CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
CODEX_AUTH_FILE = CODEX_HOME / "auth.json"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _http_post(url: str, body: dict | None = None, extra_headers: dict | None = None) -> tuple[int, dict]:
    """POST JSON, return (status_code, parsed_body)."""
    data = None
    headers: dict[str, str] = {
        "User-Agent": "codex_cli_rs/0.0.0",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url,
        method="POST",
        data=data,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": raw.decode("utf-8", "replace")}
    except Exception as e:
        return 0, {"error": str(e)}


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload (middle section) without verification."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        import base64
        payload = base64.urlsafe_b64decode(payload_b64).decode("utf-8")
        claims = json.loads(payload)
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def _extract_account_id(token: str) -> str:
    """Extract ``chatgpt_account_id`` from JWT claims."""
    claims = _decode_jwt_payload(token)
    auth = claims.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        account_id = auth.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id.strip():
            return account_id
    return ""


# ── Main flow ─────────────────────────────────────────────────────────────────


def run_codex_login() -> int:
    """Run the device-code OAuth flow and write ``~/.codex/auth.json``."""
    print()
    print("\033[1mCodex Login — Device Authorization\033[0m")
    print()

    # ── 1. Request device code ────────────────────────────────────────────
    print("Requesting device code from OpenAI …", end=" ", flush=True)
    status, init_data = _http_post(DEVICE_USERCODE_URL, {"client_id": CLIENT_ID})
    if status not in (200, 201):
        print(f"\033[31mfailed\033[0m (HTTP {status})")
        print(f"  {init_data.get('error', 'unknown error')}")
        return 1
    print("\033[32mok\033[0m")

    device_auth_id = init_data.get("device_auth_id")
    user_code = init_data.get("user_code")
    if not device_auth_id or not user_code:
        print("\033[31mMissing required fields in response\033[0m")
        print(f"  {json.dumps(init_data, indent=2)}")
        return 1

    raw_interval = init_data.get("interval", 5)
    poll_interval = (int(raw_interval) if isinstance(raw_interval, (int, float)) else 5) + POLL_SAFETY_MARGIN_S

    # ── 2. Prompt user ─────────────────────────────────────────────────────
    print()
    print("\033[1m\033[33mComplete the login in your browser:\033[0m")
    print()
    print(f"  \033[1mURL:\033[0m  {DEVICE_AUTH_URL}")
    print(f"  \033[1mCode:\033[0m \033[1;32m{user_code}\033[0m")
    print()
    print("Waiting for you to complete the browser login …")
    print()

    # ── 3. Poll for completion ─────────────────────────────────────────────
    for poll in range(1, MAX_POLLS + 1):
        wait = poll_interval if poll > 1 else min(poll_interval, POLL_INTERVAL_S)
        time.sleep(wait)

        status, poll_data = _http_post(DEVICE_TOKEN_URL, {
            "device_auth_id": device_auth_id,
            "user_code": user_code,
        })

        # 403 / 404 = still pending
        if status in (403, 404):
            if poll % 6 == 0:
                print(f"  Still waiting … ({(poll * poll_interval) // 60} min)")
            continue

        if status not in (200, 201):
            print(f"\033[31mDevice token request failed\033[0m (HTTP {status})")
            print(f"  {poll_data.get('error', 'unknown error')}")
            return 1

        authorization_code = poll_data.get("authorization_code")
        code_verifier = poll_data.get("code_verifier")
        if not authorization_code or not code_verifier:
            print("\033[31mResponse missing authorization_code or code_verifier\033[0m")
            return 1

        # ── 4. Exchange for tokens ─────────────────────────────────────────
        print("Exchanging authorization code for tokens …", end=" ", flush=True)
        exchange_body = (
            f"grant_type=authorization_code"
            f"&client_id={CLIENT_ID}"
            f"&code={authorization_code}"
            f"&code_verifier={code_verifier}"
            f"&redirect_uri={DEVICE_REDIRECT_URI}"
        )
        req = urllib.request.Request(
            OAUTH_TOKEN_URL,
            method="POST",
            data=exchange_body.encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                token_data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            print(f"\033[31mfailed\033[0m (HTTP {e.code})")
            print(f"  {body[:500]}")
            return 1
        except Exception as e:
            print(f"\033[31mfailed\033[0m ({e})")
            return 1

        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")

        if not access_token or not refresh_token or not isinstance(expires_in, (int, float)):
            print(f"\033[31mToken response missing required fields\033[0m")
            print(f"  {json.dumps(token_data, indent=2)}")
            return 1

        expires_at = int(time.time() + expires_in)
        account_id = _extract_account_id(access_token)

        if not account_id:
            print(f"\033[31mFailed to extract ChatGPT account ID from token\033[0m")
            return 1

        print("\033[32mok\033[0m")

        # ── 5. Write auth.json ─────────────────────────────────────────────
        CODEX_HOME.mkdir(parents=True, exist_ok=True)
        CODEX_HOME.chmod(0o700)

        auth_data = {
            "tokens": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires": expires_at,
                "account_id": account_id,
            }
        }

        CODEX_AUTH_FILE.write_text(json.dumps(auth_data, indent=2) + "\n", encoding="utf-8")
        CODEX_AUTH_FILE.chmod(0o600)

        print()
        print(f"\033[32m\033[1mCredentials saved to {CODEX_AUTH_FILE}\033[0m")
        print()
        print("The proxy will auto-detect this file on next start.")
        print("Restart the proxy if it's already running:")
        print(f"  systemctl --user restart zcode-openai-sub-proxy")
        print()
        return 0

    print()
    print("\033[31mTimed out waiting for browser login.\033[0m")
    print("Please try again — make sure you complete the login in your browser.")
    return 1


if __name__ == "__main__":
    sys.exit(run_codex_login())
