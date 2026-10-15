"""Fake sign-in Worker for the end-to-end check (mirrors desktop/broker/src/index.ts).

    python fake_broker.py --port 47851

GET  /health                 200 "ok"
GET  /start/{provider}       validates state/challenge/port like the real Worker, then
                             redirects straight to the app's finish URL with a fresh
                             one-time `pickup` (no provider page)
POST /redeem {state, verifier, pickup}
                             canned result, once; 403 wrong verifier or wrong/missing
                             pickup (entry burnt), 404 unknown
POST /refresh/jira           canned tokens
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

STATE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
VERIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
PICKUP_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")

RESULTS = {
    "notion": {"provider": "notion", "access_token": "ntn_e2e_token", "workspace_id": "e2e-ws",
               "workspace_name": "E2E Workspace", "bot_id": "e2e-bot"},
    "jira": {"provider": "jira", "access_token": "jira-e2e-token", "refresh_token": "jira-e2e-refresh",
             "expires_in": 3600, "cloud_id": "e2e-cloud", "site_url": "https://e2e.atlassian.net",
             "site_name": "e2e"},
    "google": {"provider": "google", "provider_user_id": "g-e2e-1", "email": "e2e@example.test", "name": "E2E Person"},
    "github": {"provider": "github", "provider_user_id": "gh-e2e-1", "email": "e2e@example.test", "name": "E2E Person"},
}

LOCK = threading.Lock()
PENDING: dict[str, dict] = {}  # state -> {provider, challenge, pickup_hash}
SEEN: set[str] = set()  # every state ever started: one sign-in per state, like the Worker's seen: marker


def _sha(s: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(s.encode()).digest()).rstrip(b"=").decode()


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
        if provider not in RESULTS:
            return self._send(400, b"invalid")
        if not STATE_RE.match(state) or not CHALLENGE_RE.match(challenge) or not re.fullmatch(r"[0-9]{4,5}", port_raw):
            return self._send(400, b"invalid")
        port = int(port_raw)
        if not 1024 <= port <= 65535:
            return self._send(400, b"invalid")
        pickup = secrets.token_urlsafe(32)
        with LOCK:
            if state in SEEN:
                return self._send(400, b"expired")
            SEEN.add(state)
            PENDING[state] = {"provider": provider, "challenge": challenge, "pickup_hash": _sha(pickup)}
        location = f"http://127.0.0.1:{port}/api/connect/finish?state={state}&pickup={pickup}"
        self._send(302, b"", headers={"Location": location})

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
        pickup = (body or {}).get("pickup")
        pickup = pickup if isinstance(pickup, str) else ""
        if not isinstance(state, str) or not isinstance(verifier, str) or not STATE_RE.match(state) or not VERIFIER_RE.match(verifier):
            return self._json({"error": "Expected JSON {state, verifier, pickup}."}, 400)
        with LOCK:
            entry = PENDING.pop(state, None)  # single use, even on a wrong verifier or pickup
        if entry is None:
            return self._json({"error": "expired"}, 404)
        verifier_ok = secrets.compare_digest(_sha(verifier), entry["challenge"])
        pickup_ok = bool(PICKUP_RE.match(pickup)) and secrets.compare_digest(_sha(pickup), entry["pickup_hash"])
        if not (verifier_ok and pickup_ok):
            return self._json({"error": "This sign-in couldn't be confirmed."}, 403)
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
