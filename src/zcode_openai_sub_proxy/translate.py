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
    """Convert OpenAI chat messages into Codex instructions string + input list.

    Tool calls in assistant messages are serialised as text because the Codex
    Responses API only supports these content types in the ``input`` array:
    ``input_text``, ``output_text``, ``input_image``, ``refusal``,
    ``input_file``, ``computer_screenshot``, ``summary_text``.
    """
    if not isinstance(messages, list):
        return "You are a helpful coding assistant.", []
    instructions: list[str] = []
    inputs: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        text = extract_text(message.get("content"))

        if role == "system":
            if text:
                instructions.append(text)
            continue

        if role == "assistant":
            # Build output_text parts — tool calls are serialised as text.
            parts: list[str] = []
            if text:
                parts.append(text)
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    parts.append(
                        f"[tool_call id={tc.get('id', '?')} "
                        f"name={fn.get('name', '?')} "
                        f"args={fn.get('arguments', '{}')}]"
                    )
            if parts:
                inputs.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "\n".join(parts)}],
                })
            continue

        if role == "tool":
            # Serialise tool result as user input text, preserving call_id context.
            call_id = message.get("tool_call_id", "?")
            inputs.append({
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": f"[tool_result id={call_id}] {text}",
                }],
            })
            continue

        # "user", "function", or anything else → user input.
        if text:
            inputs.append({"role": "user", "content": [{"type": "input_text", "text": text}]})

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
        # Function-call argument deltas are handled by extract_tool_calls().
        if event.get("type") == "response.function_call_arguments.delta":
            return ""
        return delta
    if event.get("type") == "response.output_text.delta" and isinstance(event.get("text"), str):
        return event["text"]
    item = event.get("item")
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, list):
            return "".join(extract_text(part) for part in content if isinstance(part, dict))
    return ""


def extract_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract tool_calls from Codex SSE events.

    Tracks function_call items across ``output_item.added``,
    ``function_call_arguments.delta``, and ``output_item.done`` events.
    """
    calls: dict[str, dict[str, Any]] = {}  # call_id → {name, arguments}
    ordered_ids: list[str] = []

    for event in events:
        etype = event.get("type")

        # output_item.added — capture function_call metadata.
        if etype == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                call_id = item.get("call_id") or item.get("id") or ""
                name = item.get("name") or ""
                args = item.get("arguments") or ""
                if call_id:
                    calls[call_id] = {"name": name, "arguments": args}
                    if call_id not in ordered_ids:
                        ordered_ids.append(call_id)

        # function_call_arguments.delta — accumulate argument text.
        elif etype == "response.function_call_arguments.delta":
            call_id = event.get("call_id") or ""
            delta_text = event.get("delta") or ""
            if call_id:
                if call_id not in calls:
                    calls[call_id] = {"name": "", "arguments": ""}
                    ordered_ids.append(call_id)
                calls[call_id]["arguments"] += delta_text

        # output_item.done — finalize with complete arguments if available.
        elif etype == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                call_id = item.get("call_id") or item.get("id") or ""
                name = item.get("name") or ""
                args = item.get("arguments")
                if call_id:
                    if call_id not in calls:
                        calls[call_id] = {"name": name, "arguments": args if isinstance(args, str) else ""}
                        ordered_ids.append(call_id)
                    else:
                        if name and not calls[call_id]["name"]:
                            calls[call_id]["name"] = name
                        if isinstance(args, str) and args:
                            calls[call_id]["arguments"] = args

    # Build OpenAI-format tool_calls array.
    tool_calls: list[dict[str, Any]] = []
    for call_id in ordered_ids:
        info = calls.get(call_id)
        if not info or not info.get("name"):
            continue
        tool_calls.append({
            "id": call_id,
            "type": "function",
            "function": {
                "name": info["name"],
                "arguments": info["arguments"],
            },
        })

    return tool_calls


def _convert_tools_to_codex(tools: Any) -> list[dict[str, Any]]:
    """Convert OpenAI-format tools to Codex flat format.

    OpenAI: ``{type: "function", function: {name, description, parameters}}``
    Codex:  ``{type: "function", name, description, parameters}``
    """
    if not isinstance(tools, list):
        return []
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        # Already in flat Codex format? Pass through.
        if tool.get("type") == "function" and "name" in tool and "function" not in tool:
            converted.append(tool)
            continue
        # Convert from OpenAI nested format.
        fn = tool.get("function")
        if isinstance(fn, dict):
            converted.append({
                "type": "function",
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        else:
            # Unknown format — pass through as-is.
            converted.append(tool)
    return converted


def _convert_tool_choice_to_codex(tool_choice: Any) -> Any:
    """Convert OpenAI-format tool_choice to Codex flat format.

    Strings (``"auto"``, ``"none"``, ``"required"``) pass through unchanged.
    Objects are flattened: ``{type: "function", function: {name}}`` → ``{type: "function", name}``.
    """
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function":
            fn = tool_choice.get("function")
            if isinstance(fn, dict) and fn.get("name"):
                return {"type": "function", "name": fn["name"]}
        # Already flat or unknown — pass through.
        return tool_choice
    return tool_choice


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

    # Forward tool definitions — the Codex Responses API uses a flat format
    # ({type, name, description, parameters}) unlike OpenAI's nested format
    # ({type: "function", function: {name, description, parameters}}).
    if "tools" in payload:
        body["tools"] = _convert_tools_to_codex(payload["tools"])
    if "tool_choice" in payload:
        body["tool_choice"] = _convert_tool_choice_to_codex(payload["tool_choice"])
    if "parallel_tool_calls" in payload:
        body["parallel_tool_calls"] = payload["parallel_tool_calls"]

    return body


def chat_completion(payload: dict[str, Any], raw: bytes) -> dict[str, Any]:
    """Convert a buffered Codex SSE response into a single OpenAI chat-completion dict."""
    events = decode_sse_payloads(raw)
    text = "".join(event_text(event) for event in events)
    tool_calls = extract_tool_calls(events)
    now = int(time.time())
    message: dict[str, Any] = {"role": "assistant", "content": text}
    finish_reason = "stop"
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    return {
        "id": f"chatcmpl-zcode-openai-sub-{now}",
        "object": "chat.completion",
        "created": now,
        "model": normalize_model_id(payload.get("model")),
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }


def chat_completion_stream(payload: dict[str, Any], raw: bytes) -> bytes:
    """Convert a Codex SSE response into OpenAI-compatible streaming SSE chunks."""
    events = decode_sse_payloads(raw)
    tool_calls = extract_tool_calls(events)
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

    for event in events:
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

    # Emit tool_calls as a delta chunk if the model made any.
    if tool_calls:
        tool_chunk = {
            "id": f"chatcmpl-zcode-openai-sub-{now}",
            "object": "chat.completion.chunk",
            "created": now,
            "model": model,
            "choices": [{"index": 0, "delta": {"tool_calls": tool_calls}, "finish_reason": None}],
        }
        chunks.append(f"data: {json.dumps(tool_chunk, separators=(',', ':'))}\n\n".encode())

    finish_reason = "tool_calls" if tool_calls else "stop"
    done = {
        "id": f"chatcmpl-zcode-openai-sub-{now}",
        "object": "chat.completion.chunk",
        "created": now,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
    }
    chunks.append(f"data: {json.dumps(done, separators=(',', ':'))}\n\n".encode())
    chunks.append(b"data: [DONE]\n\n")
    return b"".join(chunks)
