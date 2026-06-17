# zcode-openai-sub-proxy

Standalone local OpenAI-compatible proxy for [ZCode](https://zcode.ai) that routes `openai` models through a ChatGPT/Codex **subscription token** — no paid API key needed.

Inspired by:
- [`sybil-solutions/codex-shim`](https://github.com/sybil-solutions/codex-shim)
- [`OnlyTerp/UltraCode-Shim`](https://github.com/OnlyTerp/UltraCode-Shim)

## What it does

- Exposes a local OpenAI-compatible API on `http://127.0.0.1:48765`.
- Implements `GET /health`, `GET /v1/models`, `POST /v1/chat/completions`. 
- Forwards chat-completion requests to the ChatGPT Codex Responses API.
- Translates the SSE stream back into OpenAI-compatible chunks.
- Uses only local files under `data/` — no external app databases.
- Adds an **UltraCode** instruction envelope by default (long-horizon continuation reminder, configurable reasoning effort).
- Zero pip dependencies — stdlib only.

## Requirements

- **Python >= 3.11** (no third-party packages needed)
- A ChatGPT/Codex **subscription token** — get one by:
  - Running `codex login` (recommended: auto-detected from `~/.codex/auth.json`)
  - Extracting manually from your browser's DevTools
  - Setting the `ZCODE_OPENAI_SUB_TOKEN` environment variable

## Quick Start

```bash
# 1. Run the installer (copies config template, runs self-test)
./install.sh

# 2. Set up auth (pick one):
#    a) Recommended: run `codex login` once — the proxy auto-reads ~/.codex/auth.json
#    b) Alternative: set ZCODE_OPENAI_SUB_TOKEN env var
#    c) Manual: edit data/auth.json with your ChatGPT/Codex access token

# 3. Start the proxy
bin/zcode-openai-sub-proxy
```

Optionally install the launcher on your `PATH`:

```bash
./install.sh --install-launcher
zcode-openai-sub-proxy
```

## Installation

### Via install.sh (recommended)

```bash
git clone https://github.com/pepijnfrenken/zcode-openai-sub-proxy.git
cd zcode-openai-sub-proxy
./install.sh --install-launcher
```

The installer:
- Checks Python >= 3.11 is available
- Copies `data/auth.example.json` → `data/auth.json` (if missing)
- Runs the doctor self-test (`scripts/doctor.py`)
- Optionally symlinks the launcher into `~/.local/bin/`

### Manual

```bash
git clone https://github.com/pepijnfrenken/zcode-openai-sub-proxy.git
cd zcode-openai-sub-proxy
cp data/auth.example.json data/auth.json
pip install -e .       # optional: installs the entry point
```

Then start with either:

```bash
bin/zcode-openai-sub-proxy                    # shell launcher
python3 -m zcode_openai_sub_proxy             # python module
zcode-openai-sub-proxy                        # pip entry point (if installed)
```

## Configuration

### Auth token (four sources, checked in priority order)

The proxy reads your ChatGPT/Codex token from the first available source:

#### 1. `~/.codex/auth.json` (Codex CLI or built-in login — **recommended**)

**Option A: Built-in device-code login** (no extra tools needed):
```bash
python3 scripts/codex-login.py
# Opens a browser-based device-code flow → writes ~/.codex/auth.json
```

**Option B: Official Codex CLI:**
```bash
npm i -g @openai/codex
codex login
# Opens browser → writes ~/.codex/auth.json
```

Either way, the proxy auto-detects `tokens.access_token` from this file, extracts your account ID from the JWT, and refreshes expired tokens automatically via the OAuth token endpoint.

See also: [oh-my-pi's openai-codex OAuth flow](https://github.com/can1357/oh-my-pi/blob/main/packages/ai/src/registry/oauth/openai-codex.ts) (the reference implementation this follows).

#### 2. Environment variable

Set `ZCODE_OPENAI_SUB_TOKEN` — takes highest precedence over all file sources:

```bash
export ZCODE_OPENAI_SUB_TOKEN="your-jwt-token-here"
```

#### 3. `data/auth.json` (manual fallback)

Place your token directly in `data/auth.json`:

```json
{
  "access": "your-jwt-token-here",
  "accountId": "optional-account-id"
}
```

**How to get your token manually:** Open the ChatGPT web app in your browser → DevTools (F12) → Application → Local Storage → find the `codex` key, or use a browser extension that extracts the token.

### Models

`data/models.json` contains the model catalog exposed via `/v1/models`. You can edit it to add or remove models. Each entry needs at minimum an `id` string field — additional fields (name, cost, contextWindow, etc.) are used by some clients for display and filtering.

### Data directory override

Set `ZCODE_OPENAI_SUB_PROXY_DATA` to use a different directory for `auth.json` and `models.json`:

```bash
export ZCODE_OPENAI_SUB_PROXY_DATA="$HOME/.config/zcode-openai-sub-proxy"
```

## Usage

### Starting the proxy

```bash
bin/zcode-openai-sub-proxy
# → zcode-openai-sub-proxy listening on http://127.0.0.1:48765
```

### ZCode provider config

Configure ZCode as an OpenAI-compatible provider:

```json
{
  "kind": "openai-compatible",
  "name": "OpenAI Subscription (Codex)",
  "options": {
    "apiKey": "zcode-openai-sub-proxy",
    "baseURL": "http://127.0.0.1:48765/v1"
  }
}
```

### UltraCode knobs

Environment variables that control the UltraCode instruction envelope:

| Variable | Values | Default | Effect |
|----------|--------|---------|--------|
| `UC_ULTRACODE` | `0`, `false`, `no`, `off` | `1` (on) | Disable the UltraCode instruction envelope |
| `UC_CODEX_EFFORT` | `minimal`, `low`, `medium`, `high`, `xhigh` | `xhigh` | Default reasoning effort |

Per-request, `reasoning_effort` in the chat-completion payload overrides the default.

## Troubleshooting

### Port already in use

```
OSError: [Errno 98] Address already in use
```

Another instance is already running, or something else is on port 48765. Stop the other process or change `PORT` in `config.py`.

### Invalid token

```
RuntimeError: invalid standalone OpenAI subscription token
```

Your access token is missing, expired, or malformed. Extract a fresh token from the ChatGPT web app and update `auth.json` or `ZCODE_OPENAI_SUB_TOKEN`.

### Auth file not found

```
FileNotFoundError: data/auth.json
```

Run `./install.sh` to create the file from the template, or manually copy `data/auth.example.json` → `data/auth.json`. The proxy will also auto-copy it on first run.

### Upstream errors

```
[upstream error 401] …
```

Your token has expired or been revoked. Extract a fresh token from ChatGPT.

## Self-test

Run the doctor to verify your setup:

```bash
python3 scripts/doctor.py
```

Checks: Python version, auth file validity, models file validity, upstream connectivity, port availability.

## Files

```text
zcode-openai-sub-proxy/
├── install.sh                  # one-command setup
├── bin/zcode-openai-sub-proxy  # shell launcher
├── scripts/doctor.py           # self-test / health check
├── data/
│   ├── auth.example.json       # auth template (committed)
│   ├── auth.json               # your credentials (gitignored)
│   └── models.json             # model catalog
├── src/zcode_openai_sub_proxy/
│   ├── server.py               # HTTP server + entry point
│   ├── translate.py            # request/response translation
│   ├── upstream.py             # ChatGPT Codex API calls
│   ├── local_store.py          # auth & model file readers
│   ├── config.py               # paths, URLs, constants
│   ├── __init__.py
│   └── __main__.py
├── pyproject.toml
├── LICENSE
└── README.md
```

## License

MIT — see [LICENSE](LICENSE).
