from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

THINKING_LEVELS = {"minimal", "low", "medium", "high", "xhigh"}
ULTRACODE_REMINDER = (
    "UltraCode mode: use the highest useful reasoning effort, keep long-horizon "
    "coding context in mind, and continue autonomously until the requested work is complete."
)


def ultracode_enabled() -> bool:
    """Return whether the UltraCode instruction envelope is enabled (default: on)."""
    return os.environ.get("UC_ULTRACODE", "1").strip().lower() not in {"0", "false", "no", "off"}


def default_effort() -> str:
    """Return the default reasoning effort from UC_CODEX_EFFORT env var."""
    effort = os.environ.get("UC_CODEX_EFFORT", "xhigh").strip().lower()
    return effort if effort in THINKING_LEVELS else "xhigh"


def apply_ultracode_instructions(instructions: str) -> str:
    """Append the UltraCode reminder to instructions if enabled."""
    if not ultracode_enabled():
        return instructions
    return f"{instructions}\n\n{ULTRACODE_REMINDER}" if instructions else ULTRACODE_REMINDER


def normalize_model_id(model: Any) -> str:
    """Normalize a model identifier: strip 'openai/' prefix, default to gpt-5.5."""
    if not isinstance(model, str) or not model.strip():
        print("[model] no model specified, defaulting to gpt-5.5", file=sys.stderr)
        return "gpt-5.5"
    value = model.strip()
    normalized = value.removeprefix("openai/")
    if normalized != value:
        print(f"[model] stripped 'openai/' prefix: {value!r} → {normalized!r}", file=sys.stderr)
    return normalized


def extract_text(content: Any) -> str:
    """Extract plain text from a message content field (string or list of parts)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def convert_messages(messages: Any) -> tuple[str, list[dict[str, Any]]]:
    """Convert OpenAI chat messages into Codex instructions string + input list."""
    if not isinstance(messages, list):
        return "You are a helpful coding assistant.", []
    instructions: list[str] = []
    inputs: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        text = extract_text(message.get("content"))
        if not text:
            continue
        if role == "system":
            instructions.append(text)
            continue
        if role == "assistant":
            mapped_role = "assistant"
            content_type = "output_text"
        else:
            # "user", "tool", "function" all map to user input.
            mapped_role = "user"
            content_type = "input_text"
        inputs.append({"role": mapped_role, "content": [{"type": content_type, "text": text}]})
    return "\n\n".join(instructions) or "You are a helpful coding assistant.", inputs


def decode_sse_payloads(raw: bytes) -> list[dict[str, Any]]:
    """Parse a Codex SSE stream into a list of JSON event dicts."""
    events: list[dict[str, Any]] = []
    for block in raw.decode("utf-8", "replace").split("\n\n"):
        data_lines = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        data = "\n".join(data_lines)
        if data == "[DONE]":
            continue
        try:
            value = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def event_text(event: dict[str, Any]) -> str:
    """Extract delta text from a Codex SSE event dict."""
    delta = event.get("delta")
    if isinstance(delta, str):
        return delta
    if event.get("type") == "response.output_text.delta" and isinstance(event.get("text"), str):
        return event["text"]
    item = event.get("item")
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, list):
            return "".join(extract_text(part) for part in content if isinstance(part, dict))
    return ""


def build_codex_body(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the Codex Responses API request body from an OpenAI chat-completion payload."""
    instructions, inputs = convert_messages(payload.get("messages"))
    effort = payload.get("reasoning_effort")
    body: dict[str, Any] = {
        "model": normalize_model_id(payload.get("model")),
        "instructions": apply_ultracode_instructions(instructions),
        "input": inputs or [{"role": "user", "content": [{"type": "input_text", "text": ""}]}],
        "store": False,
        "stream": True,
    }
    if isinstance(effort, str) and effort in THINKING_LEVELS:
        body["reasoning"] = {"effort": effort}
    elif ultracode_enabled():
        body["reasoning"] = {"effort": default_effort()}
    return body


def chat_completion(payload: dict[str, Any], raw: bytes) -> dict[str, Any]:
    """Convert a buffered Codex SSE response into a single OpenAI chat-completion dict."""
    text = "".join(event_text(event) for event in decode_sse_payloads(raw))
    now = int(time.time())
    return {
        "id": f"chatcmpl-zcode-openai-sub-{now}",
        "object": "chat.completion",
        "created": now,
        "model": normalize_model_id(payload.get("model")),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
    }


def chat_completion_stream(payload: dict[str, Any], raw: bytes) -> bytes:
    """Convert a Codex SSE response into OpenAI-compatible streaming SSE chunks."""
    model = normalize_model_id(payload.get("model"))
    now = int(time.time())
    chunks: list[bytes] = []

    # First chunk carries the role delta per the OpenAI streaming spec.
    role_chunk = {
        "id": f"chatcmpl-zcode-openai-sub-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    chunks.append(f"data: {json.dumps(role_chunk, separators=(',', ':'))}\n\n".encode())

    for event in decode_sse_payloads(raw):
        text = event_text(event)
        if not text:
            continue
        chunk = {
            "id": f"chatcmpl-zcode-openai-sub-{now}",
            "object": "chat.completion.chunk",
            "created": now,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }
        chunks.append(f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n".encode())

    done = {
        "id": f"chatcmpl-zcode-openai-sub-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    chunks.append(f"data: {json.dumps(done, separators=(',', ':'))}\n\n".encode())
    chunks.append(b"data: [DONE]\n\n")
    return b"".join(chunks)
