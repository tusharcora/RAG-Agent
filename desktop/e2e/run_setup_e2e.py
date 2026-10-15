"""End-to-end check of the first-run setup flow against the bundled backend.

    /home/claude/dvenv/bin/python desktop/e2e/run_setup_e2e.py

Needs the bundle (python desktop/build_backend.py) and Playwright's Chromium.
Path A: local models via a fake Ollama.  Path B: API keys against local stubs.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "tauri" / "src-tauri" / "resources" / "backend"
PY = BACKEND / "python" / "bin" / "python"
CHROMIUM = os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium")
FAKE_PORT, APP_PORT, STUB_PORT = 11499, 47831, 47841
GOOD_VOYAGE, GOOD_ANTHROPIC = "pa-good", "sk-ant-good"


class Stub(BaseHTTPRequestHandler):
    """Stands in for Voyage (/embeddings) and Anthropic (/models)."""

    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.endswith("/embeddings"):
            if self.headers.get("Authorization") != f"Bearer {GOOD_VOYAGE}":
                return self._send(401, {"detail": "Provided API key is invalid."})
            return self._send(200, {"data": [{"embedding": [0.1] * 512}]})
        if self.path.startswith("/anthropic/messages"):
            event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "OK"}}
            body = f"event: content_block_delta\ndata: {json.dumps(event)}\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send(404, {})

    def do_GET(self):
        if self.path.startswith("/anthropic/models"):
            if self.headers.get("x-api-key") != GOOD_ANTHROPIC:
                return self._send(401, {"error": {"message": "invalid x-api-key"}})
            return self._send(200, {"data": [{"id": "claude-sonnet-5-5"}]})
        self._send(404, {})


def wait_http(url: str, timeout: float = 30) -> None:
    import urllib.request

    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return
        except Exception:
            time.sleep(0.3)
    raise TimeoutError(url)


class Launcher:
    def __init__(self, home: str, env_extra: dict):
        env = {**os.environ, "KNOWLEDGE_AGENT_HOME": home, **env_extra}
        self.proc = subprocess.Popen(
            [str(PY), "-I", str(BACKEND / "launcher.py"), "--port", str(APP_PORT), "--no-browser", "--lifeline",
             "--resources", str(BACKEND)],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.lines: list[str] = []
        self.url = None
        ready = threading.Event()

        def pump():
            for line in self.proc.stdout:
                self.lines.append(line.rstrip())
                m = re.match(r"Ready: (\S+)", line)
                if m and not self.url:
                    self.url = m.group(1)
                    ready.set()

        threading.Thread(target=pump, daemon=True).start()
        if not ready.wait(120):
            raise RuntimeError("launcher never became ready:\n" + "\n".join(self.lines[-30:]))

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def fake_ollama(port: int, empty: bool = True) -> subprocess.Popen:
    cmd = [sys.executable, str(HERE / "fake_ollama.py"), "--port", str(port)] + (["--start-empty"] if empty else [])
    p = subprocess.Popen(cmd)
    wait_http(f"http://127.0.0.1:{port}/api/tags")
    return p


def seed_document(home: str) -> None:
    """One notion connection + document + chunk in the local org (as M1's test does)."""
    code = r'''
import asyncio, random, sys, uuid
import asyncpg
from pathlib import Path

async def main(sock):
    c = await asyncpg.connect(host=sock, user="postgres", database="postgres")
    org = await c.fetchval("select id from organizations limit 1")
    conn = uuid.uuid4(); doc = uuid.uuid4()
    await c.execute("insert into oauth_connections (id, org_id, provider, workspace_id, workspace_name, access_token) values ($1,$2,'notion','ws','Test','x')", conn, org)
    await c.execute("insert into documents (id, source, external_id, connection_id, title, url) values ($1,'notion','p1',$2,'Vacation policy','https://example.com/vacation')", doc, conn)
    vec = "[" + ",".join(str(random.uniform(-1, 1)) for _ in range(512)) + "]"
    await c.execute("insert into chunks (id, document_id, chunk_index, content, embedding) values ($1,$2,0,'Employees get 25 days of vacation per year.',$3::vector)", uuid.uuid4(), doc, vec)
    await c.close()

asyncio.run(main(sys.argv[1]))
'''
    pid = Path(home, "pgdata", "postmaster.pid").read_text().splitlines()
    sock = pid[4].strip()
    subprocess.run([str(PY), "-I", "-c", code, sock], check=True)


def launch_browser(p):
    return p.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox"])


def path_a() -> None:
    home = tempfile.mkdtemp(prefix="ka-e2e-a-", dir="/tmp")
    os.chmod(home, 0o755)
    fake = None
    launcher = Launcher(home, {"OLLAMA_URL": f"http://127.0.0.1:{FAKE_PORT}"})
    try:
        with sync_playwright() as p:
            b = launch_browser(p)
            page = b.new_page()
            page.goto(launcher.url)
            page.wait_for_url(re.compile(r"/setup/?$"), timeout=30000)
            page.get_by_text("Run models on this computer").click()
            expect(page.get_by_role("heading", name="Install Ollama")).to_be_visible()
            expect(page.get_by_text("Waiting for Ollama")).to_be_visible()
            print("A: install screen shown while Ollama is down")
            fake = fake_ollama(FAKE_PORT)
            t0 = time.time()
            expect(page.get_by_role("heading", name="Download the models")).to_be_visible(timeout=5000)
            print(f"A: advanced on its own after {time.time() - t0:.1f}s")
            cont = page.get_by_role("button", name="Continue")
            expect(cont).to_be_enabled(timeout=30000)
            print("A: both downloads finished")
            cont.click()
            expect(page.get_by_text("FINISHING SETUP")).to_be_visible(timeout=10000)
            print("A: finishing screen shown")
            page.wait_for_url(re.compile(r"/connections/?$"), timeout=90000)
            print("A: landed on /connections/")

            seed_document(home)
            page.goto(launcher.url)
            box = page.get_by_label("Ask a question")
            expect(box).to_be_visible(timeout=20000)
            box.fill("How many vacation days do I get?")
            page.get_by_label("Send").click()
            expect(page.get_by_text("Answer", exact=False).first).to_be_visible(timeout=60000)
            sources = page.get_by_label("Sources")
            expect(sources.get_by_text("Vacation policy")).to_be_visible(timeout=30000)
            expect(sources.get_by_text("CITED · 1 OF 1")).to_be_visible(timeout=30000)
            print("A: answer with citation and source card shown")
            page.screenshot(path=f"{home}/a-chat.png")
            b.close()
    finally:
        launcher.stop()
        if fake:
            fake.terminate()
    print("PASS path A (local models)")


def path_b() -> None:
    home = tempfile.mkdtemp(prefix="ka-e2e-b-", dir="/tmp")
    os.chmod(home, 0o755)
    srv = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    launcher = Launcher(home, {
        "VOYAGE_BASE_URL": f"http://127.0.0.1:{STUB_PORT}",
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{STUB_PORT}/anthropic",
    })
    try:
        with sync_playwright() as p:
            b = launch_browser(p)
            page = b.new_page()
            page.goto(launcher.url)
            page.wait_for_url(re.compile(r"/setup/?$"), timeout=30000)
            page.get_by_text("Use API keys instead").click()
            expect(page.get_by_role("heading", name="Add your keys")).to_be_visible()
            page.get_by_label("Voyage API key").fill("pa-bad")
            page.get_by_role("radio", name="Claude").click()
            page.get_by_label("Anthropic API key").fill(GOOD_ANTHROPIC)
            page.get_by_role("button", name="Check and continue").click()
            expect(page.get_by_text("Provided API key is invalid.")).to_be_visible(timeout=20000)
            print("B: bad Voyage key rejected with the error under its field")
            page.get_by_label("Voyage API key").fill(GOOD_VOYAGE)
            page.get_by_role("button", name="Check and continue").click()
            try:
                page.wait_for_url(re.compile(r"/connections/?$"), timeout=90000)
            except Exception:
                print("B FAILED; page text:", page.locator("body").inner_text()[:800])
                print("launcher output:", *launcher.lines[-40:], sep="\n")
                raise
            print("B: good keys landed on /connections/")
            b.close()
    finally:
        launcher.stop()
        srv.shutdown()
    print("PASS path B (API keys)")


def main() -> int:
    path_a()
    path_b()
    time.sleep(1)
    left = subprocess.run(["pgrep", "-f", "postgres -D"], capture_output=True, text=True).stdout.strip()
    print("leftover postgres processes:", left or "none")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
