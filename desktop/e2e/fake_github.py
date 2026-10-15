"""Fake GitHub REST API for the end-to-end check (the GitHub source, path G).

    python fake_github.py --port 47861

Serves exactly what the app calls (services/api/app/integrations/github_api.py, github_sync.py;
services/worker/app/integrations/github.py, app/tasks/github.py) for one installation (99) of
the user e2e-octo with one repository, e2e-octo/notes (id 7, default branch main), holding one
Markdown file and one issue with one comment. No Link headers: every listing is one page.
Every call needs `Authorization: Bearer ghu_e2e_…` (the fake broker's tokens), else 401.

GET /user
GET /user/installations
GET /user/installations/99/repositories
GET /repos/e2e-octo/notes/git/trees/main?recursive=1
GET /repos/e2e-octo/notes/git/blobs/{sha}
GET /repos/e2e-octo/notes/issues            (state/sort/direction/since are accepted, not filtered)
GET /repos/e2e-octo/notes/issues/1
GET /repos/e2e-octo/notes/issues/1/comments
GET /_requests                               test hook: every path asked for (no query)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

LOGIN, INSTALLATION, REPO_ID, FULL_NAME = "e2e-octo", 99, 7, "e2e-octo/notes"
README = b"# Notes\nThe launch codename is BLUEBIRD."
README_SHA = hashlib.sha1(b"blob %d\0" % len(README) + README).hexdigest()
TREE_SHA = "e2e0000000000000000000000000000000000001"
UPDATED = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
API = "http://127.0.0.1:{port}"

REQUESTS: list[str] = []
LOCK = threading.Lock()


def _repo(api: str) -> dict:
    return {
        "id": REPO_ID,
        "name": "notes",
        "full_name": FULL_NAME,
        "private": True,
        "html_url": f"https://github.com/{FULL_NAME}",
        "default_branch": "main",
        "url": f"{api}/repos/{FULL_NAME}",
    }


def _issue(api: str) -> dict:
    return {
        "number": 1,
        "title": "Ship it",
        "state": "open",
        "body": "Release the notes app.",
        "labels": [{"name": "release"}],
        "user": {"login": LOGIN},
        "updated_at": UPDATED,
        "html_url": f"https://github.com/{FULL_NAME}/issues/1",
        "repository_url": f"{api}/repos/{FULL_NAME}",
        "comments": 1,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, obj, status: int = 200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/_requests":
            with LOCK:
                return self._json({"requests": list(REQUESTS)})
        with LOCK:
            REQUESTS.append(path)
        if not (self.headers.get("Authorization") or "").startswith("Bearer ghu_e2e_"):
            return self._json({"message": "Bad credentials"}, 401)
        api = API.format(port=self.server.server_address[1])
        repo = f"/repos/{FULL_NAME}"
        if path == "/user":
            return self._json({"id": 4242, "login": LOGIN})
        if path == "/user/installations":
            return self._json({"total_count": 1, "installations": [
                {"id": INSTALLATION, "account": {"login": LOGIN, "type": "User"}}]})
        if path == f"/user/installations/{INSTALLATION}/repositories":
            return self._json({"total_count": 1, "repositories": [_repo(api)]})
        if path == f"{repo}/git/trees/main":
            return self._json({"sha": TREE_SHA, "truncated": False, "tree": [
                {"path": "README.md", "mode": "100644", "type": "blob", "sha": README_SHA, "size": len(README)}]})
        if path == f"{repo}/git/blobs/{README_SHA}":
            return self._json({"sha": README_SHA, "size": len(README), "encoding": "base64",
                               "content": base64.encodebytes(README).decode()})
        if path == f"{repo}/issues":
            return self._json([_issue(api)])
        if path == f"{repo}/issues/1":
            return self._json(_issue(api))
        if path == f"{repo}/issues/1/comments":
            return self._json([{"id": 11, "user": {"login": LOGIN}, "body": "Approved for Friday."}])
        self._json({"message": "Not Found"}, 404)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=47861)
    args = ap.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
