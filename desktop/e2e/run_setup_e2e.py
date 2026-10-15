"""End-to-end check of the first-run setup flow against the bundled backend.

    /home/claude/dvenv/bin/python desktop/e2e/run_setup_e2e.py

Needs the bundle (python desktop/build_backend.py) and Playwright's Chromium.
Path A: local models via a fake Ollama.  Path B: API keys against local stubs.
Path C: models already installed.  Path D: Connect and sign-in via a fake sign-in Worker.
Path G: connect GitHub through the fake sign-in Worker and a fake GitHub API, sync, disconnect.
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
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "tauri" / "src-tauri" / "resources" / "backend"
PY = BACKEND / "python" / "bin" / "python"
CHROMIUM = os.environ.get("CHROMIUM")  # path to a Chromium binary; unset = Playwright's own
HOMES: list[str] = []  # every data folder this run created, for the leak check
FAKE_PORT, APP_PORT, STUB_PORT, BROKER_PORT, GITHUB_PORT = 11499, 47831, 47841, 47851, 47861
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


def leftovers(home: str) -> list[int]:
    """Processes whose command line mentions this run's data folder (Postgres, children)."""
    out = subprocess.run(["pgrep", "-f", home], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out if int(p) != os.getpid()]


def kill_leftovers(home: str) -> list[int]:
    pids = leftovers(home)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return pids


class Launcher:
    def __init__(self, home: str, env_extra: dict):
        self.home = home
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
            ready.set()  # launcher exited: stop waiting

        threading.Thread(target=pump, daemon=True).start()
        if not ready.wait(float(os.environ.get("E2E_READY_TIMEOUT", 120))) or not self.url:
            self.stop()
            raise RuntimeError("launcher never became ready:\n" + "\n".join(self.lines[-30:]))

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                kill_leftovers(self.home)  # kill() reaches only the launcher, not its Postgres/children


def fake_ollama(port: int, empty: bool = True, models: str = "") -> subprocess.Popen:
    cmd = [sys.executable, str(HERE / "fake_ollama.py"), "--port", str(port)]
    cmd += ["--models", models] if models else (["--start-empty"] if empty else [])
    p = subprocess.Popen(cmd)
    try:
        wait_http(f"http://127.0.0.1:{port}/api/tags")
    except BaseException:
        p.terminate()
        p.wait()
        raise
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


def seed_connection_document(home: str) -> None:
    """One document + chunk under the existing Notion connection (path D's connected one)."""
    code = r'''
import asyncio, sys, uuid
import asyncpg

async def main(sock):
    c = await asyncpg.connect(host=sock, user="postgres", database="postgres")
    conn = await c.fetchval("select id from oauth_connections where provider = 'notion'")
    doc = uuid.uuid4()
    await c.execute("insert into documents (id, source, external_id, connection_id, title, url) values ($1,'notion','e2e-p1',$2,'Kept page','https://example.com/kept')", doc, conn)
    await c.execute("insert into chunks (id, document_id, chunk_index, content) values ($1,$2,0,'Kept content.')", uuid.uuid4(), doc)
    await c.close()

asyncio.run(main(sys.argv[1]))
'''
    pid = Path(home, "pgdata", "postmaster.pid").read_text().splitlines()
    sock = pid[4].strip()
    subprocess.run([str(PY), "-I", "-c", code, sock], check=True)


def github_state(home: str) -> dict:
    """Chunk text of the synced GitHub documents and the GitHub events that didn't succeed."""
    code = r'''
import asyncio, json, sys
import asyncpg

async def main(sock):
    c = await asyncpg.connect(host=sock, user="postgres", database="postgres")
    rows = await c.fetch("select d.title, string_agg(ch.content, ' ') as text from documents d join chunks ch on ch.document_id = d.id where d.source = 'github' group by d.title")
    bad = await c.fetch("select routing_key, status from event_log where routing_key like 'rag.github%' and status not in ('succeeded', 'received', 'processing')")
    print(json.dumps({"chunks": {r["title"]: r["text"] for r in rows}, "unsuccessful": [dict(r) for r in bad]}))
    await c.close()

asyncio.run(main(sys.argv[1]))
'''
    pid = Path(home, "pgdata", "postmaster.pid").read_text().splitlines()
    sock = pid[4].strip()
    return json.loads(subprocess.run([str(PY), "-I", "-c", code, sock], check=True, capture_output=True, text=True).stdout)


def launch_browser(p):
    kwargs = {"executable_path": CHROMIUM} if CHROMIUM else {}
    return p.chromium.launch(args=["--no-sandbox"], **kwargs)


def path_a() -> None:
    home = tempfile.mkdtemp(prefix="ka-e2e-a-", dir="/tmp")
    os.chmod(home, 0o755)
    HOMES.append(home)
    fake = None
    launcher = None
    try:
        launcher = Launcher(home, {"OLLAMA_URL": f"http://127.0.0.1:{FAKE_PORT}"})
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
            expect(page.get_by_role("heading", name="Choose your models")).to_be_visible(timeout=5000)
            print(f"A: advanced on its own after {time.time() - t0:.1f}s")
            page.wait_for_timeout(1500)
            pulls = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{FAKE_PORT}/_pulls").read())["pulls"]
            assert pulls == [], f"downloads started before the user asked: {pulls}"
            print("A: nothing downloads until the user presses the button")
            page.get_by_role("button", name=re.compile(r"^Download .* and continue$")).click()
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
        if launcher:
            launcher.stop()
        if fake:
            fake.terminate()
            fake.wait()
    print("PASS path A (local models)")


def path_c() -> None:
    """Regression: models already installed (search model under a non-latest tag)
    means no downloads at all, and one of the user's own models is preselected."""
    home = tempfile.mkdtemp(prefix="ka-e2e-c-", dir="/tmp")
    os.chmod(home, 0o755)
    HOMES.append(home)
    fake = None
    launcher = None
    try:
        fake = fake_ollama(FAKE_PORT, models="nomic-embed-text:v1.5,qwen3.6:35b")
        launcher = Launcher(home, {"OLLAMA_URL": f"http://127.0.0.1:{FAKE_PORT}"})
        with sync_playwright() as p:
            b = launch_browser(p)
            page = b.new_page()
            page.goto(launcher.url)
            page.wait_for_url(re.compile(r"/setup/?$"), timeout=30000)
            # Ollama is already running, so setup opens straight on the models step.
            expect(page.get_by_role("heading", name="Your models are ready")).to_be_visible(timeout=10000)
            expect(page.get_by_role("combobox")).to_have_value("qwen3.6:35b")
            page.get_by_role("button", name="Continue", exact=True).click()
            expect(page.get_by_text("FINISHING SETUP")).to_be_visible(timeout=10000)
            page.wait_for_url(re.compile(r"/connections/?$"), timeout=90000)
            pulls = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{FAKE_PORT}/_pulls").read())["pulls"]
            assert pulls == [], f"unexpected downloads: {pulls}"
            config = json.loads(Path(home, "desktop.json").read_text())
            assert config["ollama_embed_model"] == "nomic-embed-text:v1.5", config
            print("C: installed models used as-is, no downloads, tagged search model recorded")
            b.close()
    finally:
        if launcher:
            launcher.stop()
        if fake:
            fake.terminate()
            fake.wait()
    print("PASS path C (models already installed)")


def path_b() -> None:
    home = tempfile.mkdtemp(prefix="ka-e2e-b-", dir="/tmp")
    os.chmod(home, 0o755)
    HOMES.append(home)
    srv = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    launcher = None
    try:
        launcher = Launcher(home, {
            "VOYAGE_BASE_URL": f"http://127.0.0.1:{STUB_PORT}",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{STUB_PORT}/anthropic",
        })
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
        if launcher:
            launcher.stop()
        srv.shutdown()
    print("PASS path B (API keys)")


def fake_broker(port: int) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, str(HERE / "fake_broker.py"), "--port", str(port)])
    try:
        wait_http(f"http://127.0.0.1:{port}/health")
    except BaseException:
        p.terminate()
        p.wait()
        raise
    return p


def path_d() -> None:
    """Connect and optional sign-in through a fake sign-in Worker; then the feature-off case."""
    home = tempfile.mkdtemp(prefix="ka-e2e-d-", dir="/tmp")
    os.chmod(home, 0o755)
    HOMES.append(home)
    ollama = broker = launcher = None
    try:
        ollama = fake_ollama(FAKE_PORT, models="nomic-embed-text:latest,qwen3.6:35b")
        broker = fake_broker(BROKER_PORT)
        launcher = Launcher(home, {
            "OLLAMA_URL": f"http://127.0.0.1:{FAKE_PORT}",
            "AUTH_BROKER_URL": f"http://127.0.0.1:{BROKER_PORT}",
        })
        with sync_playwright() as p:
            b = launch_browser(p)
            ctx = b.new_context()
            page = ctx.new_page()
            page.goto(launcher.url)
            page.wait_for_url(re.compile(r"/setup/?$"), timeout=30000)
            expect(page.get_by_text("yours", exact=True)).to_be_visible(timeout=10000)
            print("D: 'Make it yours' shown first")
            with ctx.expect_page() as popup_info:
                page.get_by_role("button", name="Continue with Google").click()
            popup = popup_info.value
            expect(popup.get_by_role("heading", name="Welcome, E2E.")).to_be_visible(timeout=20000)
            expect(popup.get_by_text("E2E Person", exact=True)).to_be_visible()
            expect(popup).to_have_title(re.compile(r"^Signed in as E2E Person"))
            popup.screenshot(path=os.environ.get("E2E_SHOTS", "/tmp") + "/finish-signed-in.png")
            popup.close()
            print("D: Google popup landed on 'Signed in as E2E Person'")
            # The app moves on to the next setup step by itself (models are already installed).
            expect(page.get_by_role("heading", name="Your models are ready")).to_be_visible(timeout=20000)
            page.get_by_role("button", name="Continue", exact=True).click()
            expect(page.get_by_text("FINISHING SETUP")).to_be_visible(timeout=10000)
            page.wait_for_url(re.compile(r"/connections/?$"), timeout=90000)
            print("D: setup finished, on /connections/")

            with ctx.expect_page() as popup_info:
                page.get_by_role("button", name="Connect Notion").click()
            popup = popup_info.value
            expect(popup.get_by_role("heading", name="Notion is connected.")).to_be_visible(timeout=20000)
            popup.screenshot(path=os.environ.get("E2E_SHOTS", "/tmp") + "/finish-connected.png")
            popup.close()
            expect(page.get_by_text("E2E Workspace").first).to_be_visible(timeout=20000)
            print("D: Notion popup landed on 'Connected to Notion'; card shows the workspace")

            # Disconnect, keeping the (one seeded) document.
            seed_connection_document(home)
            page.reload()
            page.get_by_role("button", name="Disconnect", exact=True).click()
            dialog = page.get_by_role("dialog", name="Disconnect Notion?")
            expect(dialog.get_by_role("radio", name=re.compile(r"Remove its 1 synced document too"))).to_be_checked()
            dialog.get_by_text("Keep them searchable").click()  # react-aria's radio input is visually hidden
            expect(dialog.get_by_role("radio", name=re.compile(r"Keep them searchable"))).to_be_checked()
            dialog.get_by_role("button", name="Disconnect", exact=True).click()
            expect(page.get_by_text("Disconnected · 1 document kept")).to_be_visible(timeout=20000)
            expect(page.get_by_role("button", name="Remove them")).to_be_visible()
            print("D: disconnected keeping documents; card shows 'Disconnected · 1 document kept'")

            # Connect again (same workspace): connected, document kept.
            with ctx.expect_page() as popup_info:
                page.get_by_role("button", name="Connect again").click()
            popup = popup_info.value
            expect(popup.get_by_role("heading", name="Notion is connected.")).to_be_visible(timeout=20000)
            popup.close()
            expect(page.get_by_role("button", name="Disconnect", exact=True)).to_be_visible(timeout=20000)
            expect(page.get_by_text("Disconnected · 1 document kept")).to_have_count(0)
            print("D: Connect again reconnected Notion")

            # Disconnect, removing the documents: the card is back to plain Connect.
            page.get_by_role("button", name="Disconnect", exact=True).click()
            dialog = page.get_by_role("dialog", name="Disconnect Notion?")
            dialog.get_by_role("button", name="Disconnect", exact=True).click()
            expect(page.get_by_role("button", name="Connect Notion")).to_be_visible(timeout=20000)
            expect(page.get_by_text("Disconnected ·")).to_have_count(0)
            expect(page.get_by_text("E2E Workspace")).to_have_count(0)
            print("D: disconnected removing documents; card back to 'Connect Notion'")

            expect(page.get_by_text("E2E Person").first).to_be_visible(timeout=10000)
            print("D: sidebar shows E2E Person")

            page.goto(launcher.url.rstrip("/") + "/settings/")
            expect(page.get_by_role("heading", name="Account")).to_be_visible(timeout=20000)
            expect(page.get_by_text("e2e@example.test")).to_be_visible()
            page.get_by_role("button", name="Sign out").click()
            expect(page.get_by_text("Continue with Google")).to_be_visible(timeout=10000)
            expect(page.get_by_text("My workspace").first).to_be_visible(timeout=10000)
            expect(page.get_by_text("E2E Person")).to_have_count(0)
            print("D: signed out; sidebar back to 'My workspace'")

            # Sign in again from Settings → Account (not only the first-open screen).
            page.on("console", lambda m: print("  [console]", m.type, m.text))
            with ctx.expect_page() as popup_info:
                page.get_by_role("button", name="Continue with GitHub").click()
            popup = popup_info.value
            expect(popup.get_by_role("heading", name="Welcome, E2E.")).to_be_visible(timeout=20000)
            popup.close()
            # Same verified email as the kept Google identity: the profile comes back with both linked.
            linked = page.get_by_role("list", name="Linked accounts")
            expect(linked.get_by_role("button", name="Unlink GitHub")).to_be_visible(timeout=20000)
            expect(linked.get_by_role("button", name="Unlink Google")).to_be_visible()
            expect(linked.get_by_text("Linked · e2e@example.test")).to_have_count(2)
            expect(page.get_by_text("E2E Person").first).to_be_visible()
            print("D: signed in again from Settings with GitHub; Google and GitHub both linked, name restored")

            linked.get_by_role("button", name="Unlink GitHub").click()
            expect(linked.get_by_role("button", name="Link GitHub")).to_be_visible(timeout=10000)
            expect(linked.get_by_role("button", name="Unlink Google")).to_be_visible()
            expect(linked.get_by_text("Linked · e2e@example.test")).to_have_count(1)
            expect(page.get_by_text("E2E Person").first).to_be_visible()
            print("D: unlinked GitHub; Google remains")
            b.close()
        launcher.stop()
        launcher = None

        # Same data folder, broker not configured: no sign-in offer, the Notion secret form stays.
        launcher = Launcher(home, {"OLLAMA_URL": f"http://127.0.0.1:{FAKE_PORT}", "AUTH_BROKER_URL": ""})
        with sync_playwright() as p:
            b = launch_browser(p)
            page = b.new_page()
            page.goto(launcher.url.rstrip("/") + "/settings/")
            expect(page.get_by_role("heading", name="Account")).to_have_count(0, timeout=10000)
            print("D: without AUTH_BROKER_URL no Account/sign-in section")
            b.close()
    finally:
        if launcher:
            launcher.stop()
        if broker:
            broker.terminate()
            broker.wait()
        if ollama:
            ollama.terminate()
            ollama.wait()
    print("PASS path D (Connect and sign-in)")


def path_d_off() -> None:
    """Fresh home, no AUTH_BROKER_URL: setup has no 'Make it yours' and Notion shows the secret form."""
    home = tempfile.mkdtemp(prefix="ka-e2e-e-", dir="/tmp")
    os.chmod(home, 0o755)
    HOMES.append(home)
    ollama = launcher = None
    try:
        ollama = fake_ollama(FAKE_PORT, models="nomic-embed-text:latest,qwen3.6:35b")
        launcher = Launcher(home, {"OLLAMA_URL": f"http://127.0.0.1:{FAKE_PORT}", "AUTH_BROKER_URL": ""})
        with sync_playwright() as p:
            b = launch_browser(p)
            page = b.new_page()
            page.goto(launcher.url)
            page.wait_for_url(re.compile(r"/setup/?$"), timeout=30000)
            expect(page.get_by_role("heading", name="Your models are ready")).to_be_visible(timeout=20000)
            assert page.get_by_text("Make it", exact=False).count() == 0
            page.get_by_role("button", name="Continue", exact=True).click()
            page.wait_for_url(re.compile(r"/connections/?$"), timeout=90000)
            expect(page.get_by_label("Notion integration secret")).to_be_visible(timeout=20000)
            assert page.get_by_role("button", name="Connect Notion").count() == 0
            print("D(off): no 'Make it yours'; Notion shows the secret form")
            b.close()
    finally:
        if launcher:
            launcher.stop()
        if ollama:
            ollama.terminate()
            ollama.wait()
    print("PASS path D-off (feature off)")


def fake_github(port: int) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, str(HERE / "fake_github.py"), "--port", str(port)])
    try:
        wait_http(f"http://127.0.0.1:{port}/_requests")
    except BaseException:
        p.terminate()
        p.wait()
        raise
    return p


def github_hits(path: str | None = None):
    """How often the fake GitHub was asked for `path` (or every path asked, in order)."""
    asked = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{GITHUB_PORT}/_requests").read())["requests"]
    return asked if path is None else asked.count(path)


def stop_proc(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def path_g() -> None:
    """Connect GitHub through the fake sign-in Worker and a fake GitHub API, sync, see the documents, disconnect."""
    home = tempfile.mkdtemp(prefix="ka-e2e-g-", dir="/tmp")
    os.chmod(home, 0o755)
    HOMES.append(home)
    shots = os.environ.get("E2E_SHOTS", "/tmp")
    ollama = broker = github = launcher = None
    try:
        ollama = fake_ollama(FAKE_PORT, models="nomic-embed-text:latest,qwen3.6:35b")
        broker = fake_broker(BROKER_PORT)
        github = fake_github(GITHUB_PORT)
        launcher = Launcher(home, {
            "OLLAMA_URL": f"http://127.0.0.1:{FAKE_PORT}",
            "AUTH_BROKER_URL": f"http://127.0.0.1:{BROKER_PORT}",
            "GITHUB_API_URL": f"http://127.0.0.1:{GITHUB_PORT}",
        })
        with sync_playwright() as p:
            b = launch_browser(p)
            ctx = b.new_context()
            page = ctx.new_page()
            page.goto(launcher.url)
            page.wait_for_url(re.compile(r"/setup/?$"), timeout=30000)
            expect(page.get_by_text("yours", exact=True)).to_be_visible(timeout=10000)
            page.get_by_role("button", name="Skip for now").click()
            expect(page.get_by_role("heading", name="Your models are ready")).to_be_visible(timeout=20000)
            page.get_by_role("button", name="Continue", exact=True).click()
            page.wait_for_url(re.compile(r"/connections/?$"), timeout=90000)
            print("G: setup finished, on /connections/")

            card = page.locator("section").filter(has_text=re.compile(r"^GitHub"))
            with ctx.expect_page() as popup_info:
                card.get_by_role("button", name="Connect GitHub").click()
            popup = popup_info.value
            expect(popup.get_by_role("heading", name="GitHub is connected.")).to_be_visible(timeout=20000)
            expect(popup.get_by_text("e2e-octo", exact=True)).to_be_visible()
            expect(popup.get_by_text("1 repository", exact=True)).to_be_visible()
            popup.screenshot(path=f"{shots}/github-finish.png")
            popup.close()
            print("G: GitHub popup landed on 'GitHub is connected.' with '1 repository'")

            # Connecting starts the first sync, which lists the repositories.
            expect(card.get_by_text(re.compile(r"^1 repositor"))).to_be_visible(timeout=30000)
            expect(card.get_by_text("e2e-octo").first).to_be_visible()
            expect(card.get_by_text("repository", exact=True)).to_be_visible()  # the big stat's label, singular
            print("G: card shows e2e-octo and 1 repository")
            # The connect's own sync lists the tree and the issues once each; wait for both, then Sync now
            # must list them again (its POST answers only after enumerating).
            tree, issues = "/repos/e2e-octo/notes/git/trees/main", "/repos/e2e-octo/notes/issues"
            deadline = time.time() + 30
            while not all(github_hits(q) for q in (tree, issues)):
                assert time.time() < deadline, f"the connect's sync never listed the repo: {github_hits()}"
                time.sleep(0.5)
            before = {q: github_hits(q) for q in (tree, issues)}
            with page.expect_response(lambda r: "/sync/github" in r.url and r.request.method == "POST",
                                      timeout=60000) as resp_info:
                card.get_by_role("button", name="Sync now").click()
            resp = resp_info.value
            assert resp.ok, f"POST /sync/github answered {resp.status}: {resp.text()}"
            after = {q: github_hits(q) for q in (tree, issues)}
            assert all(after[q] > before[q] for q in (tree, issues)), (before, after)
            print(f"G: Sync now answered {resp.status} and listed the tree and issues again ({before} -> {after})")

            page.goto(launcher.url.rstrip("/") + "/knowledge-base/")
            titles = ["e2e-octo/notes · README.md", "e2e-octo/notes #1: Ship it"]
            deadline = time.time() + 60
            while True:
                # Right after a (re)load the page hasn't fetched its documents yet: give it a moment.
                try:
                    for t in titles:
                        expect(page.get_by_text(t, exact=True)).to_be_visible(timeout=5000)
                    break
                except AssertionError:
                    pass
                if time.time() > deadline:
                    print("G FAILED; page text:", page.locator("body").inner_text()[:1500])
                    print("launcher output:", *launcher.lines[-60:], sep="\n")
                    raise AssertionError(f"knowledge base never listed {titles}")
                page.wait_for_timeout(2000)
                page.reload()
            print("G: knowledge base lists the README and issue #1")
            deadline = time.time() + 15
            while True:  # titles can show before the chunk text is readable from another connection
                state = github_state(home)
                if ("BLUEBIRD" in state["chunks"].get(titles[0], "")
                        and "Approved for Friday." in state["chunks"].get(titles[1], "")) or time.time() > deadline:
                    break
                time.sleep(1)
            assert "BLUEBIRD" in state["chunks"].get(titles[0], ""), state
            assert "Approved for Friday." in state["chunks"].get(titles[1], ""), state
            assert state["unsuccessful"] == [], state
            print("G: README and issue (with its comment) embedded; no GitHub event failed")

            page.goto(launcher.url.rstrip("/") + "/connections/")
            card = page.locator("section").filter(has_text=re.compile(r"^GitHub"))
            expect(card.get_by_role("button", name="Disconnect", exact=True)).to_be_visible(timeout=20000)
            card.screenshot(path=f"{shots}/github-card-connected.png")
            card.get_by_role("button", name="Disconnect", exact=True).click()
            dialog = page.get_by_role("dialog", name="Disconnect GitHub?")
            expect(dialog.get_by_role("radio", name=re.compile(r"Remove its 2 synced documents too"))).to_be_checked()
            dialog.get_by_role("button", name="Disconnect", exact=True).click()
            expect(card.get_by_role("button", name="Connect GitHub")).to_be_visible(timeout=20000)
            expect(page.get_by_text("Disconnected ·")).to_have_count(0)
            print("G: disconnected removing documents; card back to 'Connect GitHub'")

            page.goto(launcher.url.rstrip("/") + "/knowledge-base/")
            page.wait_for_timeout(2000)
            for t in titles:
                expect(page.get_by_text(t, exact=True)).to_have_count(0)
            print("G: knowledge base no longer lists the GitHub documents")
            b.close()
        asked = github_hits()
        for path in ("/user/installations", "/repos/e2e-octo/notes/git/trees/main", "/repos/e2e-octo/notes/issues/1/comments"):
            assert path in asked, f"fake GitHub never asked for {path}: {asked}"
    finally:
        if launcher:
            launcher.stop()
        for proc in (github, broker, ollama):
            if proc:
                stop_proc(proc)
    print("PASS path G (GitHub source)")


def main() -> int:
    try:
        path_a()
        path_c()
        path_b()
        path_d()
        path_d_off()
        path_g()
    finally:
        time.sleep(1)
        left = sorted({pid for h in HOMES for pid in leftovers(h)})
        print("leftover processes from this run:", left or "none")
        if left:
            for h in HOMES:
                kill_leftovers(h)
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
