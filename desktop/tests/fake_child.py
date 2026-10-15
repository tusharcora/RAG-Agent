"""Stand-in for the API/worker child processes in supervisor tests.

    python fake_child.py [--port N] [--mode MODE]

Serves GET /api/health on --port (if given). MODE defaults to env FAKE_MODE:
  ok            serve forever
  exit75-once   exit 75 after 2 s the first time (marker file in FAKE_DIR
                absent: create it); afterwards behave like ok
  crash         exit 3 after 1 s
  never-healthy never bind the port, just sleep
"""

from __future__ import annotations

import argparse
import http.server
import os
import sys
import threading
import time
from pathlib import Path


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/api/health":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int)
    parser.add_argument("--mode", default=os.environ.get("FAKE_MODE", "ok"))
    args = parser.parse_args()

    print(f"FAKE started pid={os.getpid()} provider={os.environ.get('EMBEDDING_PROVIDER')}", flush=True)

    mode = args.mode
    if mode == "exit75-once":
        marker = Path(os.environ["FAKE_DIR"]) / "exit75.marker"
        if marker.exists():
            mode = "ok"
        else:
            marker.write_text("1")

    if mode == "never-healthy":
        while True:
            time.sleep(60)

    if args.port is not None:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()

    if mode == "exit75-once":
        time.sleep(2)
        print("FAKE exiting 75", flush=True)
        return 75
    if mode == "crash":
        time.sleep(1)
        print("FAKE crashing", flush=True)
        return 3
    while True:
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
