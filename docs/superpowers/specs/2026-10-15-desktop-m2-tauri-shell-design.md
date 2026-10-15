# Desktop M2: Tauri shell and first-run setup

Date: 2026-10-15 · Branch: `desktop-app` · Builds on M1 (`desktop/launcher.py`, commit d760ebb)

## Goal

Someone with no developer tools installs Knowledge Agent like any other app,
opens it, gets through a first-run setup, connects Notion and asks a cited
question. No terminal, Python, Docker or hosting.

Targets: macOS (Apple Silicon) and Windows. Builds are unsigned for now.
Installers built in CI (`.dmg`, `.exe`) are M3, not this milestone.

## Decisions

| Question | Decision |
| --- | --- |
| Shell | Tauri v2 |
| Backend packaging | Portable CPython 3.12 + installed packages + source, run via the existing launcher (not PyInstaller) |
| Models | Local-first: guided Ollama install; an "use API keys instead" path |
| Name / bundle id | Knowledge Agent / `com.tusharcora.knowledgeagent` |

Why not PyInstaller: pgserver's bundled Postgres binaries, dynamic imports in
pydantic / OpenTelemetry / google-genai, and both services' packages being
named `app` all make freezing fragile. A portable interpreter runs exactly the
code M1 already tested. Cost: ~250–300 MB installed, source readable in the
bundle.

## 1. Architecture

```
Knowledge Agent.app / .exe     (Tauri v2, Rust)
├─ loading screen              static HTML in the Tauri bundle
└─ resources/backend/
   ├─ python/                  portable CPython 3.12 with desktop/requirements.txt installed
   ├─ api/   worker/           copies of services/api, services/worker
   ├─ sql/                     infra/postgres/*.sql
   ├─ ui/                      static frontend export (DESKTOP_BUILD=1)
   └─ launcher.py
```

### Tauri shell (`desktop/tauri/`)

On launch:
1. Open the main window on the bundled loading screen.
2. Pick a free port on 127.0.0.1.
3. Spawn `python launcher.py --port N --no-browser --resources <backend dir>`
   with stdin piped and stdout/stderr captured.
4. Poll `http://127.0.0.1:N/api/health` (timeout 90 s), then navigate the
   window to `http://127.0.0.1:N/`.
5. If the launcher exits or the timeout passes, show its last log lines on
   the loading screen with a "Try again" button.

On quit: close the launcher's stdin and wait up to 15 s for it to exit, then
kill it.

Single instance: Tauri's single-instance plugin focuses the existing window
on a second launch. The launcher's own `running.json` guard stays as a
backstop.

### Launcher changes (`desktop/launcher.py`)

- `--resources DIR`: where `api/`, `worker/`, `sql/`, `ui/` live. Without it,
  the repo layout is used (`services/api`, `infra/postgres`,
  `services/frontend/out`), so `python desktop/launcher.py` keeps working
  for development.
- **Stdin lifeline**: when started with `--lifeline`, a thread reads stdin
  and triggers shutdown on EOF. Tauri always passes it. This gives a clean
  shutdown (API, worker, then `pg_ctl stop`) on quit and when the Tauri
  process dies, which matters on Windows where terminating a process runs no
  cleanup.
- **Restart code**: if the API exits with code 75, the launcher re-reads
  `desktop.json`, restarts the API and worker with the new environment and
  keeps Postgres running. Any other exit of either child still shuts down.
- **Desktop config**: `<data dir>/desktop.json` holds
  `{"setup_complete": bool, "embedding_provider": "ollama" | "voyage"}`.
  The Voyage key, when used, is stored in the existing owner-only
  `secrets.json` as `voyage_api_key`. The launcher maps these to
  `EMBEDDING_PROVIDER` / `VOYAGE_API_KEY` for both children.
- Children get `DESKTOP_DATA_DIR` so the API can write `desktop.json`.

### Build (`desktop/build_backend.py`)

For the current platform:
1. Download the python-build-standalone CPython 3.12 `install_only` archive
   for the target triple (pinned version + sha256).
2. `pip install -r desktop/requirements.txt` into it.
3. Copy `services/api`, `services/worker` (excluding `__pycache__`, `.env`,
   tests), `infra/postgres/*.sql`, `desktop/launcher.py`, and
   `services/frontend/out` into `desktop/tauri/src-tauri/resources/backend/`.
4. Strip caches and test directories to keep the bundle small.

Then `npm run tauri build` (in `desktop/tauri`) packages the app. M3's CI runs
these same steps on macOS and Windows runners.

## 2. First-run setup

### Where it lives

A new `/setup` page in the existing Next.js frontend (desktop build only).
In desktop mode `AuthProvider` calls `GET /api/setup/status` after the local
sign-in and redirects to `/setup` while `setup_complete` is false; `/setup`
redirects to `/` once it's true. The server build never routes there.

### API (`services/api/app/api/routes/setup.py`, desktop mode only, 404 otherwise)

- `GET /setup/status` →
  `{setup_complete, embedding_provider, ollama: {running, models: [..]}, required_models: ["nomic-embed-text", ...]}`.
  Ollama status comes from `GET {ollama_url}/api/tags` with a 2 s timeout.
- `POST /setup/pull {model}` → SSE stream relaying Ollama's
  `POST /api/pull {"stream": true}` progress as
  `progress {completed, total, status}` events, then `done` or `error`.
- `POST /setup/complete-local {answer_model}` → saves the answer model through
  the existing LLM settings (provider `local`, base URL `{ollama_url}/v1`),
  writes `desktop.json` with `embedding_provider: "ollama"`, then schedules a
  restart.
- `POST /setup/complete-keys {voyage_api_key, provider, api_key, model}` →
  validates the Voyage key with a one-input embed call and the answer key
  with the existing LLM test call, saves the answer provider through the
  existing LLM settings, stores the Voyage key in `secrets.json`, writes
  `desktop.json` with `embedding_provider: "voyage"`, then schedules a
  restart.
- "Schedules a restart": the response is sent, then the process exits with
  code 75 about 0.5 s later.

### Screens

1. **Welcome**: two choices, "Run models on this computer (recommended)"
   and "Use API keys instead".
2. **Local · Ollama check**: if Ollama isn't reachable, show "Install Ollama"
   (opens ollama.com/download in the system browser) and re-check every 2 s.
   Moves on by itself once Ollama answers.
3. **Local · Models**: one row per required model with its size and a
   progress bar. Models already installed show as done. If other chat models
   are installed, the user can pick one as the answer model instead of
   downloading `llama3.2`. "Continue" starts the pulls, one at a time.
4. **Keys**: Voyage key field, then a provider choice (Claude, OpenAI,
   Gemini) with a key field and a model field defaulting to that provider's
   usual model. "Check and continue" validates both keys.
5. **Restarting**: brief "Finishing setup…" while the backend restarts; the
   page polls `/api/health`, then goes to `/connections`.

### Errors

- Ollama stops mid-pull: that row shows the error and a Retry button;
  finished models stay finished.
- Disk full or network failure: Ollama's error message shown verbatim.
- Invalid key: the field shows the provider's error; nothing is saved.
- Ollama stopped after setup: queries already return the "Ollama isn't
  running" error from M1.

### Out of scope for M2

- Switching embedding provider after setup (needs clear-and-re-sync).
- Jira in the desktop app.
- Code signing, notarization, auto-update.
- Bundling Ollama itself.

## 3. Testing

The repo has no test suite; M2 adds `desktop/tests/` and
`services/api/tests/` with pytest, covering only new desktop code.

Unit:
- Launcher: migrations apply once; `uuid-ossp` shim; socket-dir workaround
  for paths with spaces; exit code 75 restarts children and keeps Postgres;
  stdin EOF shuts everything down including Postgres; `--resources` path
  resolution; `desktop.json` → environment mapping.
- Setup routes against a fake Ollama (httpx MockTransport): status for
  not-running / models-missing / complete; pull progress relayed and errors
  surfaced; complete-local and complete-keys write the right config and
  reject bad keys; every route 404s outside desktop mode.

End-to-end (Linux container):
- `build_backend.py` produces a Linux bundle whose own Python runs the
  launcher.
- Playwright walks the local setup path against a fake Ollama, then asks a
  question and gets a cited answer; separately walks the keys path with
  stubbed providers.
- `tsc --noEmit` and the `DESKTOP_BUILD=1` static build.

Not verifiable from the container:
- Tauri: compile the shell on Linux if its system libraries install.
- macOS: Tushar runs `npm run tauri build` and a short checklist (first
  launch and the unsigned-app prompt, setup, quit and reopen, no leftover
  `postgres` processes).
- Windows: first real run is M3's CI build. Until then, Windows-specific
  paths (pgserver over TCP, process shutdown) rely on review and unit tests.
