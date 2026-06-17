from __future__ import annotations

import json
import sys
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import HOST, PORT
from .local_store import read_models
from .translate import chat_completion, chat_completion_stream
from .upstream import call_codex


def _model_list() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": model.get("id"), "object": "model", "owned_by": "openai-sub"}
            for model in read_models()
            if isinstance(model.get("id"), str)
        ],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """Log errors (status >= 400) to stderr; suppress successful requests."""
        status_str = args[1] if len(args) >= 2 else "0"
        try:
            status = int(status_str)
        except (ValueError, TypeError):
            status = 0
        if status >= 400:
            print(
                f"[{self.log_date_time_string()}] {self.client_address[0]} "
                f"{format % args}",
                file=sys.stderr,
            )

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    MAX_BODY_SIZE = 2 * 1024 * 1024  # 2 MiB — reject larger payloads

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length > self.MAX_BODY_SIZE:
            raise ValueError(
                f"request body too large ({length} bytes, max {self.MAX_BODY_SIZE})"
            )
        data = self.rfile.read(length) if length else b"{}"
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        try:
            path = self.path.rstrip("/")
            if path in {"/health", "/v1/health"}:
                self._send_json(200, {"ok": True})
                return
            if path not in {"/v1/models", "/models"}:
                self._send_json(404, {"error": {"message": "not found"}})
                return
            self._send_json(200, _model_list())
        except (FileNotFoundError, json.JSONDecodeError) as error:
            print(f"[error] {error}", file=sys.stderr)
            self._send_json(500, {"error": {"message": "failed to load model list"}})
        except Exception as error:
            print(f"[error] {error}", file=sys.stderr)
            self._send_json(500, {"error": {"message": "internal server error"}})

    def do_POST(self) -> None:
        if self.path.rstrip("/") not in {"/v1/chat/completions", "/chat/completions"}:
            self._send_json(404, {"error": {"message": "not found"}})
            return
        try:
            payload = self._read_json()
            raw = call_codex(payload)
            if payload.get("stream") is True:
                self._send_stream(chat_completion_stream(payload, raw))
                return
            self._send_json(200, chat_completion(payload, raw))
        except urllib.error.HTTPError as error:
            print(f"[upstream error {error.code}]", file=sys.stderr)
            self._send_json(
                error.code,
                {"error": {"message": f"upstream returned status {error.code}"}},
            )
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as error:
            print(f"[bad request] {error}", file=sys.stderr)
            self._send_json(400, {"error": {"message": "invalid request"}})
        except Exception as error:
            print(f"[internal error] {error}", file=sys.stderr)
            self._send_json(500, {"error": {"message": "internal server error"}})

    def _send_stream(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        # Send in chunks so the client sees data incrementally.
        offset = 0
        while offset < len(body):
            end = min(offset + 65536, len(body))
            chunk = body[offset:end]
            self.wfile.write(f"{len(chunk):x}\r\n".encode())
            self.wfile.write(chunk)
            self.wfile.write(b"\r\n")
            self.wfile.flush()
            offset = end
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"zcode-openai-sub-proxy listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…")
        server.shutdown()
