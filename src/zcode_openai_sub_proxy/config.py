from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parents[1]
SOURCE_DATA_DIR = PROJECT_ROOT / "data"
PACKAGE_DATA_DIR = PACKAGE_DIR / "data"


def _user_data_dir() -> Path:
    """Return the per-user writable data directory for installed wheels."""
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home).expanduser() if xdg_config_home else Path.home() / ".config"
    return base / "zcode-openai-sub-proxy"


def _default_data_dir() -> Path:
    """Use repo-local data in source checkouts, otherwise user config."""
    if (SOURCE_DATA_DIR / "models.json").is_file():
        return SOURCE_DATA_DIR
    return _user_data_dir()


# Allow overriding the data directory via environment variable.
DATA_DIR = (
    Path(os.environ["ZCODE_OPENAI_SUB_PROXY_DATA"]).expanduser()
    if os.environ.get("ZCODE_OPENAI_SUB_PROXY_DATA")
    else _default_data_dir()
)

AUTH_FILE = DATA_DIR / "auth.json"
AUTH_EXAMPLE_FILE = DATA_DIR / "auth.example.json"
if not AUTH_EXAMPLE_FILE.is_file():
    AUTH_EXAMPLE_FILE = PACKAGE_DATA_DIR / "auth.example.json"

MODELS_FILE = DATA_DIR / "models.json"
DEFAULT_MODELS_FILE = PACKAGE_DATA_DIR / "models.json"

CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
HOST = "127.0.0.1"
PORT = 48765
