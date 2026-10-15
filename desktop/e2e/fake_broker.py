"""Fake sign-in Worker for the end-to-end check (mirrors desktop/broker/src/index.ts).

    python fake_broker.py --port 47851

GET  /health                 200 "ok"
GET  /start/{provider}       validates state/challenge/port like the real Worker, then
                             redirects straight to the app's finish URL (no provider page)
POST /redeem {state, verifier}  canned result, once; 403 wrong verifier, 404 unknown
POST /refresh/jira           canned tokens
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
VERIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")

RESULTS = {
    "notion": {"provider": "notion", "access_token": "ntn_e2e_token", "workspace_id": "e2e-ws",
               "workspace_name": "E2E Workspace", "bot_id": "e2e-bot"},
    "google": {"provider": "google", "provider_user_id": "g-e2e-1", "email": "e2e@example.test", "name": "E2E Person"},
    "github": {"provider": "github", "provider_user_id": "gh-e2e-1", "email": "e2e@example.test", "name": "E2E Person"},
}

LOCK = threading.Lock()
PENDING: dict[str, dict] = {}  # state -> {provider, challenge}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, ctype: str = "text/plain; charset=utf-8", headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _body(self) -> dict | None:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            v = json.loads(raw)
        except ValueError:
            return None
        return v if isinstance(v, dict) else None

    def do_GET(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        if parts == ["health"]:
            return self._send(200, b"ok")
        if len(parts) == 2 and parts[0] == "start":
            return self._start(parts[1], parse_qs(url.query))
        self._send(404, b"not found")

    def _start(self, provider: str, q: dict):
        get = lambda k: (q.get(k) or [""])[0]  # noqa: E731
        state, challenge, port_raw = get("state"), get("challenge"), get("port")
        if provider not in RESULTS and provider != "jira":
            return self._send(400, b"invalid")
        if not STATE_RE.match(state) or not CHALLENGE_RE.match(challenge) or not re.fullmatch(r"[0-9]{4,5}", port_raw):
            return self._send(400, b"invalid")
        port = int(port_raw)
        if not 1024 <= port <= 65535:
            return self._send(400, b"invalid")
        with LOCK:
            if state in PENDING:
                return self._send(400, b"expired")
            PENDING[state] = {"provider": provider, "challenge": challenge}
        self._send(302, b"", headers={"Location": f"http://127.0.0.1:{port}/api/connect/finish?state={state}"})

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        body = self._body()
        if path == "/redeem":
            return self._redeem(body)
        if path == "/refresh/jira":
            return self._json({"access_token": "jira-e2e-new", "refresh_token": "jira-e2e-refresh", "expires_in": 3600})
        self._send(404, b"not found")

    def _redeem(self, body: dict | None):
        state = (body or {}).get("state")
        verifier = (body or {}).get("verifier")
        if not isinstance(state, str) or not isinstance(verifier, str) or not STATE_RE.match(state) or not VERIFIER_RE.match(verifier):
            return self._json({"error": "Expected JSON {state, verifier}."}, 400)
        with LOCK:
            entry = PENDING.pop(state, None)  # single use, even on a wrong verifier
        if entry is None:
            return self._json({"error": "expired"}, 404)
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if digest != entry["challenge"]:
            return self._json({"error": "Verifier doesn't match."}, 403)
        result = RESULTS.get(entry["provider"])
        if result is None:
            return self._json({"error": "expired"}, 404)
        self._json(result)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=47851)
    args = ap.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
