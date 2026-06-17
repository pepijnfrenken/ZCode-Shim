from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Allow overriding the data directory via environment variable.
DATA_DIR = Path(
    os.environ.get("ZCODE_OPENAI_SUB_PROXY_DATA", PROJECT_ROOT / "data")
)

AUTH_FILE = DATA_DIR / "auth.json"
AUTH_EXAMPLE_FILE = DATA_DIR / "auth.example.json"
MODELS_FILE = DATA_DIR / "models.json"

CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
HOST = "127.0.0.1"
PORT = 48765
