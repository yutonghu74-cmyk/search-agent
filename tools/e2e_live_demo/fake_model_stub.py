"""Minimal OpenAI-compatible ``/chat/completions`` stub for the live E2E demo.

This is a stdlib-only HTTP server used to drive the *real* production server
(``python3 -m dsh_retrieval.api``) through its real ``ModelRuntime`` HTTP call
path. It returns canned JSON so the full conversation → expansion → draft →
task → background first-search flow can be exercised and *watched* in a real
browser, without any external model or credential.

Branches on the system prompt (the production code embeds the real prompts):

* Dialogue turns are sent with ``src/dsh_retrieval/dialogue.py:PROMPT``, which
  opens with "You are a conversational assistant planning image retrieval".
* Expansion turns are sent with ``model_runtime.EXPANSION_PROMPT``, which
  contains the marker "你是扩词规划器，不是执行器".

The canned responses mirror the canonical local-search fixture
(``tests/test_first_search_local.py``): requesting "找狗图片" then "3张" yields
the 狗 group ``柯基 / 金毛 / 宠物狗``, which routes every query term to the
``dog-v1`` binding and resolves 10 raw hits (dog-1 / dog-2 media units).
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIALOGUE_MARKER = "You are a conversational assistant planning image retrieval"
EXPANSION_MARKER = "你是扩词规划器，不是执行器"

# Target-count turn is keyed on the *context object* the production runtime
# serializes as the first user message of a dialogue call. The last_question
# field disambiguates the two model-backed turns in this demo flow.
DIALOGUE_SUBJECT = {
    "action": "update",
    "subject": "狗",
    "target_count": None,
    "media_type": "image",
    "constraints": None,
    "question": None,
    "reply": "想找什么内容或场景的图片？",
    "count_edits": [],
    "expansion_instruction": None,
}

DIALOGUE_COUNT = {
    "action": "update",
    "subject": None,
    "target_count": 3,
    "media_type": None,
    "constraints": None,
    "question": None,
    "reply": None,
    "count_edits": [],
    "expansion_instruction": None,
}

EXPANSION = {
    "parent_label": "动物",
    "groups": [
        {"primary_label": "狗", "auxiliary_terms": ["柯基", "金毛", "宠物狗"]},
    ],
}


def _chat_response(payload: dict) -> dict:
    return {
        "id": "chatcmpl-e2e-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "e2e-fake",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _is_expansion(body: dict) -> bool:
    messages = body.get("messages") or []
    return any(re.search(EXPANSION_MARKER, message.get("content", ""))
               for message in messages if message.get("role") == "system")


def _dialogue_context(body: dict) -> dict:
    messages = body.get("messages") or []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and "last_question" in parsed:
            return parsed
    return {}


def resolve_payload(body: dict) -> dict:
    if _is_expansion(body):
        return EXPANSION
    context = _dialogue_context(body)
    if context.get("last_question") == "target_count":
        return DIALOGUE_COUNT
    return DIALOGUE_SUBJECT


class StubHandler(BaseHTTPRequestHandler):
    server_version = "e2e-fake-model/1.0"

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send(404, {"error": {"message": "route not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("missing body")
            body = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": {"message": "body must be valid JSON"}})
            return
        self._send(200, _chat_response(resolve_payload(body)))

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"status": "ok", "path": self.path})

    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802
        print(f"[stub] {self.address_string()} - {fmt % args}", flush=True)


def serve(port: int, *, certfile=None, keyfile=None) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), StubHandler)
    scheme = "http"
    if certfile and keyfile:
        import ssl
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    print(f"[stub] fake model listening on {scheme}://127.0.0.1:{port}/v1/chat/completions",
          flush=True)
    return server


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8085)
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    args = parser.parse_args()
    try:
        serve(args.port, certfile=args.tls_cert, keyfile=args.tls_key).serve_forever()
    except KeyboardInterrupt:
        pass