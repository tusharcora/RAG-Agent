"""Minimal fake Ollama for the end-to-end setup check (no real models).

    python fake_ollama.py --port 11434 [--start-empty]

/api/tags      installed models (empty at first with --start-empty)
/api/pull      streams 5 progress lines over ~2 s, then the model is installed
/_pulls        test hook: every model a client asked to download
/api/embed     random 768-d vectors (like nomic-embed-text)
/v1/models, /v1/chat/completions   OpenAI-style; the answer is "Answer [1]"
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS: list[str] = []
PULLS: list[str] = []  # every model a client asked to download, for tests (GET /_pulls)
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _chunked(self, lines, before_end=None):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for payload, delay in lines:
            data = payload if isinstance(payload, bytes) else (json.dumps(payload) + "\n").encode()
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()
            time.sleep(delay)
        if before_end:
            before_end()
        self.wfile.write(b"0\r\n\r\n")

    def do_GET(self):
        with LOCK:
            names = list(MODELS)
        tagged = [m if ":" in m else f"{m}:latest" for m in names]
        if self.path == "/api/tags":
            self._json({"models": [{"name": m} for m in tagged]})
        elif self.path == "/v1/models":
            self._json({"data": [{"id": m} for m in tagged]})
        elif self.path == "/_pulls":
            with LOCK:
                self._json({"pulls": list(PULLS)})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = self._body()
        if self.path == "/api/pull":
            model = body.get("model", "")
            with LOCK:
                PULLS.append(model)
            total = 1_000_000
            lines = [({"status": "pulling manifest"}, 0.4)]
            lines += [({"status": "pulling layer", "total": total, "completed": total * i // 4}, 0.4) for i in range(1, 5)]

            def install():
                with LOCK:
                    if model not in MODELS:
                        MODELS.append(model)

            self._chunked(lines, before_end=install)
        elif self.path == "/api/embed":
            n = len(body.get("input", []))
            self._json({"embeddings": [[random.uniform(-1, 1) for _ in range(768)] for _ in range(n)]})
        elif self.path == "/v1/chat/completions":
            def ev(text=None, finish=None):
                delta = {"content": text} if text else {}
                return (b"data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish}]}).encode() + b"\n\n")

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for data in (ev("Answer "), ev("[1]"), ev(None, "stop"), b"data: [DONE]\n\n"):
                    self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                    self.wfile.flush()
                    time.sleep(0.05)
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self._json({"error": "not found"}, 404)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11434)
    ap.add_argument("--start-empty", action="store_true")
    ap.add_argument("--models", default="", help="comma-separated installed models, e.g. nomic-embed-text:v1.5,qwen3.6:35b")
    args = ap.parse_args()
    if args.models:
        MODELS.extend(m for m in args.models.split(",") if m)
    elif not args.start_empty:
        MODELS.extend(["nomic-embed-text", "llama3.2"])
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
