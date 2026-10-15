# Desktop app

The same Knowledge Agent, running entirely on one computer: no Docker, no
hosting, no accounts. This folder holds the launcher that runs the
backend in local mode, the portable backend build, the Tauri shell
(`tauri/`) and the end-to-end setup check (`e2e/`).

## What changes in desktop mode

| Server version | Desktop version |
| --- | --- |
| Postgres + pgvector in Docker | Embedded Postgres 16 + pgvector ([pgserver](https://pypi.org/project/pgserver/)), stored in the app's data folder |
| RabbitMQ | The worker polls `event_log` (`FOR UPDATE SKIP LOCKED`), with the same retry/backoff/dead-letter rules |
| Redis | In-memory per process (fakeredis) |
| Voyage embeddings | Ollama `nomic-embed-text`, cut to 512 dims so the schema is unchanged |
| Gemini answers by default | Local Ollama model by default (`llama3.2`); Claude, OpenAI or Gemini keys still work from Settings |
| Email/Google/GitHub sign-in | None: one local account, signed in automatically |
| Notion OAuth | Notion internal integration secret, pasted on the Connections page |
| Jira OAuth | Not available yet |
| Next.js server + `/api` proxy | Static export served by the API process, API at `/api` on the same origin |

The server version is untouched: every switch defaults to the server
behaviour (`APP_MODE=server`, `QUEUE_BACKEND=rabbitmq`, `EMBEDDING_PROVIDER=voyage`).

## Run it on a Mac

```bash
# once
brew install python@3.12 ollama        # or install the Ollama app from ollama.com
ollama pull nomic-embed-text
ollama pull llama3.2
python3.12 -m venv .venv-desktop
.venv-desktop/bin/pip install -r desktop/requirements.txt
(cd services/frontend && npm install && DESKTOP_BUILD=1 npm run build)

# every time (Ollama must be running)
.venv-desktop/bin/python desktop/launcher.py
```

It opens http://127.0.0.1:47821 in your browser. Ctrl-C stops everything,
Postgres included. Python 3.12 is the newest version pgserver has wheels for.

Data lives in `~/Library/Application Support/Knowledge Agent` (macOS),
`%LOCALAPPDATA%\tusharcora\Knowledge Agent` (Windows; the publisher folder keeps it
out of the installer's `%LOCALAPPDATA%\Knowledge Agent`) or
`~/.local/share/Knowledge Agent` (Linux): the database and a
`secrets.json` with this install's signing and encryption keys. Delete the
folder to start over. Set `KNOWLEDGE_AGENT_HOME` to use a different folder.

## Build the app

Prerequisites on macOS: Xcode Command Line Tools (`xcode-select --install`), Rust
(via [rustup](https://rustup.rs)), Node 20+, and Python 3.12.

```bash
python3.12 -m venv .venv-desktop && .venv-desktop/bin/pip install -r desktop/requirements.txt
.venv-desktop/bin/python desktop/build_backend.py     # backend bundle + static UI
cd desktop/tauri && npm install && npm run tauri build
```

`build_backend.py` writes `desktop/tauri/src-tauri/resources/backend/` (CPython 3.12,
dependencies, services, UI). The `.dmg` lands in
`desktop/tauri/src-tauri/target/release/bundle/dmg/`. Each platform has to be built
on its own machine.

## End-to-end check (Linux/macOS, no real models or keys)

Build the backend bundle first (`python desktop/build_backend.py`, above).

```bash
pip install playwright && playwright install chromium    # or set CHROMIUM=/path/to/chromium
python desktop/e2e/run_setup_e2e.py
```

Runs the bundled launcher and drives the first-run setup in a browser: path A (local
models, against `e2e/fake_ollama.py`), path B (API keys, against local Voyage and
Anthropic stubs), path C (models already installed) and path D (Connect and sign-in
against `e2e/fake_broker.py`, a stand-in for the sign-in Worker, plus the feature-off
case). All should print `PASS`, and the final "leftover processes" line should say `none`.

## Connect and sign-in

Connecting Notion or Jira, and the optional Google/GitHub sign-in, go through a small
sign-in service (a Cloudflare Worker, source in `desktop/broker/`, see
[`broker/README.md`](broker/README.md)). Your browser opens the provider's page; the
Worker holds the provider client secrets and exchanges the code, then hands the result
back to the app on `127.0.0.1` once, and only to the app that started the flow.

- The Worker sees the provider's sign-in redirect (so it handles the one-time code and the
  resulting token or name and email for about two minutes, encrypted) and nothing else.
  It never sees your documents, chats, queries or models, and it logs no codes, tokens or identities.
- The app stores tokens on this computer, encrypted. Signing in with Google or GitHub only
  sets your name and email on the local profile; it is not access control.
- To use your own Worker (or a local `wrangler dev`) with a dev build, set `AUTH_BROKER_URL`
  before starting: `AUTH_BROKER_URL=https://your-worker.example.workers.dev desktop/start-dev.command`.
  Unset or empty turns the feature off: no sign-in offer, and Notion uses the pasted-secret form.

## Mac checklist (manual, before releasing)

1. Open the `.dmg` and drag Knowledge Agent to Applications.
2. First open: right-click the app, choose Open, to get past the unidentified-developer prompt.
3. A loading screen appears, then Welcome.
4. Choose the local path with Ollama quit (the install screen waits), then start Ollama and it moves on by itself.
5. The models download; Continue lands on Connections.
6. Connect Notion, Sync now, ask a question.
7. Quit with Cmd+Q; `pgrep postgres` prints nothing.
8. Reopen: no setup, straight to the app with history intact.
9. With the real Worker: Connect Notion, pick pages, the card shows your workspace and syncs.
10. With the real Worker: sign in with Google from Settings, the sidebar shows your name; Sign out puts it back.

Troubleshooting: the backend's output is in `backend.log` in the app's log folder
(macOS: `~/Library/Logs/com.tusharcora.knowledgeagent/backend.log`).

## Security model

The desktop app is meant for a single-user machine.

- The app listens only on 127.0.0.1, and refuses requests whose `Host` is anything
  other than `127.0.0.1` or `localhost` (so a web page that re-points its own
  domain at your machine, DNS rebinding, gets a 400).
- There's no sign-in: the app gives an owner session to whatever reaches it locally.
  Any program running as you can therefore use it, read your synced pages and chat
  history, and change its settings.
- On Windows, the embedded Postgres accepts local TCP connections without a
  password, so other accounts on the same machine could reach the database directly.
  On macOS and Linux it only listens on a socket in a folder private to you.
  Hardening (a generated database password, `scram-sha-256` auth) is planned for M3.

## Notes

- Embeddings from Voyage and from Ollama live in different vector spaces.
  A database is either one or the other; switching means re-syncing.
- Running the launcher twice reuses the running instance rather than
  starting a second one on the same database.
