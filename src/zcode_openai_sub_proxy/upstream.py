from __future__ import annotations

import json
import urllib.request
from typing import Any

from .config import CODEX_URL
from .local_store import read_openai_codex_account_id, read_openai_codex_token
from .translate import build_codex_body


def call_codex(payload: dict[str, Any]) -> bytes:
    """POST a chat-completion payload to the ChatGPT Codex Responses API and return raw bytes."""
    token = read_openai_codex_token()
    account_id = read_openai_codex_account_id()
    body = json.dumps(build_codex_body(payload), separators=(",", ":")).encode()
    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "OpenAI-Beta": "responses=2026-02-06",
        "originator": "codex_cli_rs",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    request = urllib.request.Request(
        CODEX_URL,
        method="POST",
        headers=headers,
        data=body,
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return response.read()
