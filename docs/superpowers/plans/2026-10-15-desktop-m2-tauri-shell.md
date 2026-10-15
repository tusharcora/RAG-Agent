# Desktop M2: Tauri shell and first-run setup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package M1's local backend into an installable Tauri app with a first-run setup that gets a non-developer from "opened the app" to "asked a cited question".

**Architecture:** Tauri v2 spawns a bundled portable CPython 3.12 running `desktop/launcher.py`, which supervises embedded Postgres, the API (serving the static UI) and the worker. A new `/setup` page and `/setup/*` API routes pick local Ollama or API keys, write `desktop.json`, and ask the launcher to restart the children (exit code 75). Tauri keeps a stdin pipe to the launcher as a lifeline for clean shutdown.

**Tech Stack:** Python 3.12, FastAPI, httpx, pytest + pytest-asyncio, Next.js 16 static export, Tauri v2 (Rust), python-build-standalone, Playwright (tests only).

**Spec:** `docs/superpowers/specs/2026-10-15-desktop-m2-tauri-shell-design.md`

## Global Constraints

- Python 3.12 exactly for the bundle (pgserver 0.1.4 wheels stop at 3.12).
- Every switch defaults to server behaviour; the server version's Docker setup must not change behaviour. All new API routes 404 unless `settings.app_mode == "desktop"`.
- App name `Knowledge Agent`; bundle id `com.tusharcora.knowledgeagent`.
- Restart exit code: `75`. Health poll timeout in Tauri: 90 s. Quit grace period: 15 s, then kill.
- Ollama re-check interval on the setup page: 2 s. Ollama status timeout: 2 s.
- Required local models: `nomic-embed-text` (embeddings) and `llama3.2` (default answer model).
- `desktop.json` shape: `{"setup_complete": bool, "embedding_provider": "ollama" | "voyage"}`. Missing file = `{"setup_complete": false, "embedding_provider": "ollama"}`.
- Keys from the keys path live in `secrets.json` (owner-only): `voyage_api_key`, and `google_api_key` when Gemini answers. Claude/OpenAI keys go through the existing encrypted `org_llm_credentials`.
- Default answer models for the keys path: `{"anthropic": "claude-sonnet-5-5", "openai": "gpt-5-mini", "gemini": settings.query_model}`. The check rejects a model the provider's model list doesn't contain and returns that list.
- UI copy follows the published flow canvas ("Desktop first-run flow"); keys copy says keys "stay on this computer", never "encrypted".
- Commits: author `Tushar <tusharcs0112@gmail.com>`, trailers `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01HD8TDe18vrskpYykhwe7z3`.

## Review Focus

1. **App quit halfway through setup.** Reopening must land on the right step: Ollama still missing → install screen; models partly pulled → models screen with finished ones shown done. (Task 3 test `test_status_partial_models`.)
2. **Restart or crash while the worker holds a job.** A row left in `processing` would never run again; the worker must reset `processing` rows to `received` on startup. (Task 2 test `test_stale_processing_rows_requeued`.)
3. **Two pulls of the same model** (double-click, two windows). The second must get a 409, not a second Ollama download. (Task 3 test `test_pull_conflict`.)
4. **Slow or failed first start** (first initdb on a slow disk, port taken). The window must show the launcher's last log lines and Try again, never a blank or endless spinner. (Task 7 manual check + Task 2 test `test_startup_failure_exits_nonzero`.)
5. **Bad key on the keys path.** Nothing is written to `secrets.json`, `desktop.json` or the DB, and no restart happens. (Task 4 test `test_complete_keys_bad_voyage_key_writes_nothing`.)

---

## File Structure

```
desktop/
  launcher.py                 modify: Paths, desktop config→env, Supervisor (restart, lifeline)
  desktop_config.py           create: read/write desktop.json + secrets.json (shared by launcher and api via copy at build)
  build_backend.py            create: assemble resources/backend
  requirements-dev.txt        create: pytest, pytest-asyncio, playwright
  tests/
    test_paths_config.py      create
    test_supervisor.py        create
    fake_child.py             create: scriptable stand-in for api/worker
  e2e/
    run_setup_e2e.py          create: Playwright walk of both setup paths
    fake_ollama.py            create: /api/tags, /api/pull, /api/embed, /v1/chat/completions
  tauri/                      create: Tauri v2 project
    package.json
    src/loading.html
    src-tauri/{Cargo.toml, tauri.conf.json, build.rs, src/main.rs, src/backend.rs, capabilities/default.json}
  README.md                   modify: build + Mac checklist
services/api/app/
  core/desktop_state.py       create: desktop.json/secrets.json access for the api + schedule_restart()
  integrations/ollama.py      create: list_models(), pull() async iterator
  api/routes/setup.py         create: /setup/status, /setup/pull, /setup/complete-local, /setup/complete-keys
  api/routes/llm_settings.py  modify: extract apply_llm_choice(), check_llm()
  main.py                     modify: include setup router
services/api/tests/
  conftest.py                 create
  test_setup_routes.py        create
services/worker/app/local_queue.py   modify: requeue stale processing rows at start
services/worker/tests/test_local_queue.py  create
services/frontend/
  app/setup/page.tsx          create
  components/setup/{Welcome,OllamaStep,ModelsStep,KeysStep,Finishing}.tsx  create
  lib/setup.ts                create: typed client + pull SSE reader
  lib/auth-context.tsx        modify: desktop setup gate
```

`desktop/desktop_config.py` is the single owner of the two JSON files' format. The launcher imports it directly; `build_backend.py` copies it into the bundle next to `launcher.py`; the API reaches it through `DESKTOP_DATA_DIR` with its own thin reader in `app/core/desktop_state.py` (the API can't import from `desktop/` — different cwd and package root), which must use the same keys and defaults listed in Global Constraints.

---

### Task 1: Launcher paths and desktop config

**Files:**
- Create: `desktop/desktop_config.py`, `desktop/requirements-dev.txt`, `desktop/tests/test_paths_config.py`
- Modify: `desktop/launcher.py`

**Interfaces:**
- Produces:
  - `desktop_config.load(home: Path) -> dict` — returns `desktop.json` merged over the defaults.
  - `desktop_config.save(home: Path, **changes) -> dict` — atomic write (temp file + `os.replace`), returns the new dict.
  - `desktop_config.load_secrets(home: Path) -> dict` (moved from launcher; generates `jwt_secret` and `token_encryption_key` once) and `desktop_config.save_secrets(home: Path, **changes) -> None` (atomic, keeps mode 0600).
  - `launcher.Paths` dataclass: `api_dir, worker_dir, sql_dir, ui_dir: Path`; `launcher.resolve_paths(resources: Path | None) -> Paths`. With `resources`: `<r>/api`, `<r>/worker`, `<r>/sql`, `<r>/ui`. Without: repo layout.
  - `launcher.child_env(base_url, db_uri, home: Path, paths: Paths) -> dict` — now reads keys and config itself; sets `EMBEDDING_PROVIDER` from config, `VOYAGE_API_KEY`/`GOOGLE_API_KEY` from secrets when present, `DESKTOP_DATA_DIR=str(home)`, `DESKTOP_STATIC_DIR=str(paths.ui_dir)`.

- [ ] **Step 1: Write the failing tests** in `desktop/tests/test_paths_config.py`:
  - `test_load_defaults_when_missing`: `load(tmp)` == `{"setup_complete": False, "embedding_provider": "ollama"}`.
  - `test_save_roundtrip_and_merge`: `save(tmp, setup_complete=True)` then `load(tmp)["setup_complete"] is True` and `embedding_provider == "ollama"`.
  - `test_secrets_generated_once_and_private`: two `load_secrets` calls return equal dicts; on POSIX `stat.S_IMODE(mode) == 0o600`.
  - `test_save_secrets_keeps_existing_keys`: after `save_secrets(tmp, voyage_api_key="pa-x")`, `jwt_secret` is unchanged.
  - `test_resolve_paths_resources` and `test_resolve_paths_repo_default` (repo default ends with `services/api`, `infra/postgres`, `services/frontend/out`).
  - `test_child_env_maps_config`: with `embedding_provider="voyage"` and `voyage_api_key` saved, env has `EMBEDDING_PROVIDER=voyage`, `VOYAGE_API_KEY=pa-x`, `DESKTOP_DATA_DIR=str(tmp)`; with defaults, no `VOYAGE_API_KEY` key at all.
  - `test_child_env_ignores_inherited_provider`: with `EMBEDDING_PROVIDER=voyage` in `os.environ` (monkeypatch) and default config, env has `EMBEDDING_PROVIDER=ollama` (config wins — M1 let the environment override; that would bypass setup).

- [ ] **Step 2: Run** `cd desktop && ../.venv-desktop/bin/python -m pytest tests/test_paths_config.py -v` (container: `/home/claude/dvenv/bin/python`). Expected: FAIL, `ModuleNotFoundError: desktop_config`.

- [ ] **Step 3: Implement** `desktop_config.py`, move `load_secrets` out of the launcher, add `Paths`/`resolve_paths`, new `child_env` signature, and `--resources DIR` to argparse. Tests import the launcher as a module (`sys.path.insert(0, desktop_dir)`), so keep all side effects inside `main()`.

- [ ] **Step 4: Run tests.** Expected: PASS. Also run `python desktop/launcher.py --no-browser` against the repo layout and `curl /api/health` → 200 (the M1 behaviour still works).

- [ ] **Step 5: Commit** `feat(desktop): resources paths and desktop.json config`.

---

### Task 2: Supervisor — restart code, stdin lifeline, stale-job recovery

**Files:**
- Modify: `desktop/launcher.py`, `services/worker/app/local_queue.py`
- Create: `desktop/tests/fake_child.py`, `desktop/tests/test_supervisor.py`, `services/worker/tests/test_local_queue.py`

**Interfaces:**
- Consumes: `child_env`, `Paths` (Task 1).
- Produces:
  - `launcher.RESTART_CODE = 75`.
  - `launcher.Supervisor(commands: dict[str, tuple[list[str], Path]], env_factory: Callable[[], dict], health_url: str, on_stop: Callable[[], None])` with `.start() -> None` (raises `RuntimeError` with the API's last 40 output lines if health doesn't answer within `startup_timeout`, default 60 s), `.run(lifeline: bool) -> int` (blocks; returns exit status), `.stop() -> None` (terminate, wait 10 s, kill; then `on_stop()`).
  - Children's stdout/stderr are piped through to the launcher's stdout line by line and the last 40 lines per child kept in a `deque` for error reporting.
  - `local_queue.requeue_stale(session) -> int` — `UPDATE event_log SET status='received', available_at=now() WHERE status='processing'`, returns rowcount; `run()` calls it once before polling.

Rules for `run()`:
- API exits with 75 → stop the worker, rebuild env with `env_factory()`, start both again, wait for health, keep going. Postgres is untouched.
- Any other child exit → `stop()`, return 1.
- `lifeline=True` → a daemon thread blocks on `sys.stdin.read()`; EOF triggers `stop()` and `run()` returns 0.
- SIGINT/SIGTERM → `stop()`, return 0.

- [ ] **Step 1: Write `fake_child.py`**: a script that serves `GET /api/health` on `--port` with `http.server` and reacts to env `FAKE_MODE` (`ok`, `exit75-once` — exits 75 the first time a marker file in `FAKE_DIR` is absent, creating it; `crash` — exits 3 after 1 s; `never-healthy` — never binds), printing `FAKE started pid=<pid> provider=<EMBEDDING_PROVIDER>` on start.

- [ ] **Step 2: Write the failing tests** in `test_supervisor.py` (each runs `Supervisor` in a thread with fake children; `on_stop` records a call):
  - `test_restart_code_restarts_children_with_fresh_env`: `exit75-once`; `env_factory` returns `EMBEDDING_PROVIDER=ollama` then `voyage`; assert two "FAKE started" lines for the api, the second with `provider=voyage`, and `on_stop` not called.
  - `test_child_crash_stops_everything`: worker `crash` → `run()` returns 1, `on_stop` called once.
  - `test_lifeline_eof_stops_cleanly`: run the real launcher as a subprocess with `--lifeline` against fake children (add `KA_FAKE_CHILDREN=1` to swap commands and skip pgserver), close its stdin → it exits 0 within 15 s and no fake child pid is still alive (`os.kill(pid, 0)` raises).
  - `test_startup_failure_exits_nonzero`: api `never-healthy`, `startup_timeout=3` → `start()` raises `RuntimeError` whose message contains "FAKE started".
- Write `services/worker/tests/test_local_queue.py::test_stale_processing_rows_requeued` against a pgserver instance in `tmp_path` (fixture applies `infra/postgres/*.sql` with the launcher's `migrate()`): insert one `processing` row, call `requeue_stale`, assert status `received` and return value 1.

- [ ] **Step 3: Run** both test files. Expected: FAIL (`Supervisor` / `requeue_stale` undefined).

- [ ] **Step 4: Implement** `Supervisor` and rewrite `main()` to use it (`--lifeline` flag; `running.json` written after first healthy start; Postgres stopped only from `on_stop`). Implement `requeue_stale` and call it in `run()`.

- [ ] **Step 5: Run tests.** Expected: PASS. Manual: start the real launcher, `kill -TERM` it, confirm `pgrep -f "postgres -D"` is empty.

- [ ] **Step 6: Commit** `feat(desktop): supervisor with restart code, stdin lifeline, stale job recovery`.

---

### Task 3: Setup status and model pull API

**Files:**
- Create: `services/api/app/core/desktop_state.py`, `services/api/app/integrations/ollama.py`, `services/api/app/api/routes/setup.py`, `services/api/tests/conftest.py`, `services/api/tests/test_setup_routes.py`
- Modify: `services/api/app/main.py` (include router at prefix `/setup`, tag `setup`)

**Interfaces:**
- Produces:
  - `desktop_state.load_config() -> dict`, `desktop_state.save_config(**changes) -> None`, `desktop_state.save_secrets(**changes) -> None` — same file formats as Task 1, rooted at `settings.desktop_data_dir` (new `Settings` field, env `DESKTOP_DATA_DIR`, default `""`).
  - `desktop_state.schedule_restart(delay: float = 0.5) -> None` — `loop.call_later(delay, os._exit, 75)`.
  - `ollama.list_models() -> list[str] | None` — `None` when unreachable (2 s timeout); names with the `:latest` suffix stripped.
  - `ollama.pull(model: str) -> AsyncIterator[dict]` — yields Ollama's JSON lines (`status`, `completed`, `total`, `error`).
  - `ollama.TRANSPORT: httpx.AsyncBaseTransport | None = None` — tests set it to an `httpx.MockTransport`.
  - `GET /setup/status` → `SetupStatus{setup_complete: bool, embedding_provider: str, ollama: {running: bool, models: list[str]}, required_models: list[str], answer_model_candidates: list[str]}`; candidates = installed models minus embedding models (names containing `embed`).
  - `POST /setup/pull {model}` → SSE events `progress {completed, total, status}`, then `done {}` or `error {message}`. Only models in `required_models` or already in `answer_model_candidates` are accepted (400 otherwise). A module-level `set[str]` of in-flight pulls → 409 `"Already downloading <model>."`.
  - All `/setup/*` routes: 404 unless desktop mode; require `require_auth` with role owner.

- [ ] **Step 1: Write the failing tests** (`conftest.py` builds the FastAPI app with `app_mode="desktop"`, `desktop_data_dir=tmp_path`, overrides `require_auth` with an owner context, sets `ollama.TRANSPORT`):
  - `test_routes_404_in_server_mode`
  - `test_status_ollama_not_running` (transport raises `httpx.ConnectError`) → `running: false`, `models: []`.
  - `test_status_partial_models` (tags lists `nomic-embed-text:latest`, `qwen3:8b`) → `models` contains `nomic-embed-text`; `answer_model_candidates == ["qwen3:8b"]`; `setup_complete: false`.
  - `test_pull_relays_progress`: fake pull returns 3 JSON lines then `{"status":"success"}` → response body has 2+ `event: progress` and ends with `event: done`.
  - `test_pull_error_surfaces_message`: fake line `{"error":"no space left on device"}` → `event: error` with that message.
  - `test_pull_conflict`: put `"llama3.2"` in the in-flight set, POST → 409.
  - `test_pull_rejects_unknown_model` → 400.

- [ ] **Step 2: Run** `cd services/api && python -m pytest tests -v`. Expected: FAIL (routes missing).

- [ ] **Step 3: Implement** the three modules and the router. Pull uses `client.stream("POST", f"{ollama_url}/api/pull", json={"model": m, "stream": True}, timeout=None)` and `aiter_lines()`; the SSE framing reuses the `event:`/`data:` format `query.py` already emits. Remove the model from the in-flight set in a `finally`.

- [ ] **Step 4: Run tests.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(api): desktop setup status and model pull`.

---

### Task 4: Completing setup (local and keys paths)

**Files:**
- Modify: `services/api/app/api/routes/llm_settings.py`, `services/api/app/api/routes/setup.py`, `services/api/tests/test_setup_routes.py`, `services/api/app/integrations/voyage.py`

**Interfaces:**
- Consumes: `desktop_state.*`, `schedule_restart` (Task 3).
- Produces:
  - `llm_settings.apply_llm_choice(session, org_id, request: LlmSettingsIn) -> LlmSettingsOut` — the body of `put_llm_settings` after the role check; `put_llm_settings` calls it.
  - `llm_settings.check_llm(session, org_id, request: LlmSettingsIn) -> TestResult` — the body of `test_llm_settings` after the role check; the endpoint calls it.
  - `voyage.check_key(api_key: str) -> None` — one-input embed against Voyage with this key (not `settings.voyage_api_key`); raises `ValueError(<provider message>)` on 401/403.
  - `POST /setup/complete-local {answer_model: str}` → requires `nomic-embed-text` and `answer_model` both in `ollama.list_models()` (400 naming the missing one otherwise); `apply_llm_choice(provider="local", model=answer_model, base_url=f"{settings.ollama_url}/v1")`; `save_config(setup_complete=True, embedding_provider="ollama")`; `schedule_restart()`; returns `{"restarting": true}`.
  - `POST /setup/complete-keys {voyage_api_key, provider: "anthropic"|"openai"|"gemini", api_key, model: str | None}` → model defaults per Global Constraints; validates Voyage with `check_key`, then the answer key: anthropic/openai via `check_llm`, gemini via `genai.Client(api_key=...).aio.models.list()` and the same model-in-list rule. On any failure: 400 with `{"field": "voyage_api_key"|"api_key"|"model", "message": ...}` and nothing saved. On success: Claude/OpenAI → `apply_llm_choice`; Gemini → `save_secrets(google_api_key=...)` and `apply_llm_choice(provider="gemini")`; then `save_secrets(voyage_api_key=...)`, `save_config(setup_complete=True, embedding_provider="voyage")`, `schedule_restart()`.

- [ ] **Step 1: Write the failing tests** (`schedule_restart` monkeypatched to record calls; DB via the pgserver fixture from Task 2, shared through `conftest.py`):
  - `test_complete_local_saves_and_restarts`: tags include both models → 200; `desktop.json` has `setup_complete: true`; `org_llm_settings.provider == "local"`; restart recorded once.
  - `test_complete_local_missing_model` → 400 mentioning `nomic-embed-text`, no restart.
  - `test_complete_keys_bad_voyage_key_writes_nothing`: Voyage mock 401 → 400 `field == "voyage_api_key"`; `desktop.json` absent; `secrets.json` has no `voyage_api_key`; no `org_llm_settings` row; no restart.
  - `test_complete_keys_unknown_model` (anthropic model list lacks the model) → 400 `field == "model"`, message lists available models.
  - `test_complete_keys_anthropic_success` → `embedding_provider == "voyage"`, secrets has `voyage_api_key`, active provider `anthropic`, restart recorded.
  - `test_put_llm_unchanged_after_refactor`: existing `PUT /settings/llm` with a local model still returns the same `LlmSettingsOut` shape.

- [ ] **Step 2: Run tests.** Expected: FAIL.

- [ ] **Step 3: Implement** the refactor first (no behaviour change), then the two endpoints and `check_key`.

- [ ] **Step 4: Run tests.** Expected: PASS.

- [ ] **Step 5: Commit** `feat(api): finish desktop setup via local models or API keys`.

---

### Task 5: Setup UI

**Files:**
- Create: `services/frontend/lib/setup.ts`, `services/frontend/app/setup/page.tsx`, `services/frontend/components/setup/{Welcome,OllamaStep,ModelsStep,KeysStep,Finishing}.tsx`
- Modify: `services/frontend/lib/auth-context.tsx`

**Interfaces:**
- Consumes: Task 3/4 endpoints; `ApiError` and `post`/`get` helpers from `lib/api.ts`.
- Produces:
  - `lib/setup.ts`: `getSetupStatus(): Promise<SetupStatus>`, `pullModel(model, onProgress: (p: {completed?: number; total?: number; status: string}) => void, signal?: AbortSignal): Promise<void>` (rejects with the `error` event's message), `completeLocal(answerModel)`, `completeKeys(body)` (rejects with an `ApiError` whose `detail` is the `{field, message}` object), `waitForRestart(): Promise<void>` (polls `/api/health` every 1 s; resolves after it has failed once and then answered, or after 30 s of answering).
  - `AuthContext` gains `setupComplete: boolean | null`.

Behaviour:
- `AuthProvider` (desktop only): after `localSession()`, call `getSetupStatus()`; path `/setup` while incomplete → stay; incomplete on any other path → `router.replace("/setup")`; complete on `/setup` → `router.replace("/")`.
- `/setup` is a client state machine: `welcome → ollama → models → finishing` and `welcome → keys → finishing`. On mount it reads status and skips ahead: Ollama running → `models`. Screens, copy and layout follow the canvas (no sidebar on `/setup`: `Sidebar` returns `null` when `pathname` starts with `/setup`).
- `OllamaStep` polls `getSetupStatus()` every 2 s and advances on `ollama.running`; "Download Ollama" is an `<a href="https://ollama.com/download" target="_blank">` (Tauri opens it in the system browser — Task 7).
- `ModelsStep` pulls missing models one at a time; each row shows `completed/total` as GB and a bar; a failed row shows the message and Retry; Continue is enabled when `nomic-embed-text` and the chosen answer model are installed, then calls `completeLocal`.
- `KeysStep` maps `{field, message}` errors under the matching input.
- `Finishing` calls `waitForRestart()` then `window.location.assign("/connections/")` (full reload so the auth/setup state is fetched fresh).

- [ ] **Step 1:** Implement `lib/setup.ts` and the auth gate.
- [ ] **Step 2:** Implement the page and the five components.
- [ ] **Step 3: Verify** `npx tsc --noEmit -p .` → no errors; `DESKTOP_BUILD=1 npx next build` lists `/setup` as static. (In the container, stub `next/font/google` in `app/layout.tsx` for the build only and restore it — Google Fonts is unreachable there.)
- [ ] **Step 4:** Verify the server build still has no `/setup` redirect: `npx next build` (no `DESKTOP_BUILD`) and the existing login flow unchanged.
- [ ] **Step 5: Commit** `feat(frontend): desktop first-run setup`.

UI behaviour is exercised end-to-end in Task 8.

---

### Task 6: Backend bundle builder

**Files:**
- Create: `desktop/build_backend.py`
- Modify: `.gitignore` (`desktop/tauri/src-tauri/resources/`)

**Interfaces:**
- Produces: `python desktop/build_backend.py [--target TRIPLE] [--skip-ui]` → `desktop/tauri/src-tauri/resources/backend/` with `python/`, `api/`, `worker/`, `sql/`, `ui/`, `launcher.py`, `desktop_config.py`, `BUILD_INFO.json` (`{"target", "python_version", "git_sha"}`).
- `PYTHON_BUILDS: dict[str, tuple[str, str]]` — target triple → (url, sha256) for python-build-standalone CPython 3.12 `install_only` archives: `aarch64-apple-darwin`, `x86_64-pc-windows-msvc`, `x86_64-unknown-linux-gnu`. Pin the newest 3.12 release at implementation time and copy each sha256 from that release's `SHA256SUMS`.

Steps inside the script:
1. Default target from `platform`; download (cache in `desktop/.cache/`), verify sha256 (abort on mismatch), extract to `backend/python`.
2. `backend/python/bin/python3 -m pip install --no-compile -r desktop/requirements.txt` (`python\python.exe` on Windows).
3. Copy `services/api` and `services/worker` excluding `__pycache__`, `tests`, `.env*`, `*.pyc`, `Dockerfile`; copy `infra/postgres/*.sql`, `desktop/launcher.py`, `desktop/desktop_config.py`.
4. Unless `--skip-ui`: run `npm run build` in `services/frontend` with `DESKTOP_BUILD=1`, copy `out/` to `ui/`.
5. Delete `site-packages/**/tests`, `**/__pycache__`, `pip`, `setuptools` and `ensurepip` from the bundle.

- [ ] **Step 1:** Implement the script.
- [ ] **Step 2: Verify** in the container: `python3 desktop/build_backend.py --target x86_64-unknown-linux-gnu --skip-ui`, then copy the M1 UI build into `ui/`, then `KNOWLEDGE_AGENT_HOME=$(mktemp -d) backend/python/bin/python3 backend/launcher.py --resources backend --no-browser` → `/api/health` 200 and `/` serves the UI. Record `du -sh backend` in the commit message.
- [ ] **Step 3: Verify** a tampered sha256 in `PYTHON_BUILDS` makes the script exit non-zero with "checksum".
- [ ] **Step 4: Commit** `feat(desktop): build portable backend bundle`.

---

### Task 7: Tauri shell

**Files:**
- Create: `desktop/tauri/package.json` (scripts `tauri`, devDependency `@tauri-apps/cli@^2`), `desktop/tauri/src/loading.html`, `desktop/tauri/src-tauri/{Cargo.toml, build.rs, tauri.conf.json, capabilities/default.json, src/main.rs, src/backend.rs}`, `desktop/tauri/src-tauri/icons/` (generated with `npx tauri icon` from a 1024 px PNG: dark rounded square, white serif "K"; commit the generated set)

**Interfaces:**
- Consumes: `launcher.py --port N --no-browser --lifeline --resources <dir>` (Tasks 1–2), resources from Task 6.
- Produces: `backend.rs`: `pub struct Backend { child: std::process::Child, port: u16 }`, `pub fn spawn(resource_dir: &Path) -> Result<Backend, String>`, `pub fn wait_healthy(&self, timeout: Duration) -> Result<(), String>`, `pub fn shutdown(&mut self)` (drop stdin, wait 15 s, then kill), `pub fn last_log_lines(&self) -> Vec<String>` (keep the last 40 lines from a reader thread).

`tauri.conf.json`: `productName` "Knowledge Agent", `identifier` "com.tusharcora.knowledgeagent", `bundle.resources` `["resources/backend/**/*"]`, `bundle.targets` `["app", "dmg", "nsis"]`, one window (1280×820, min 960×640, title "Knowledge Agent") loading `loading.html`; CSP allowing `http://127.0.0.1:*`. Plugins: `tauri-plugin-single-instance` (focus the existing window), `tauri-plugin-opener` (external links).

`main.rs` flow:
1. In `setup`: resolve `resource_dir()/resources/backend`; on Unix, set `0o755` on `python/bin/python3` if not executable; spawn on a background thread so the window paints first.
2. Port: bind `127.0.0.1:0`, read the port, drop the listener, pass `--port`.
3. On Windows spawn with `CREATE_NO_WINDOW` (`0x08000000`).
4. After `wait_healthy(90 s)`: `window.navigate("http://127.0.0.1:{port}/")`. On failure: emit `backend-failed` with `last_log_lines()`; `loading.html` shows them in a `<pre>` with a Try again button that invokes a `retry_backend` command.
5. `on_navigation`: allow `http://127.0.0.1:{port}/*` and the bundled loading page; any other http(s) URL → `opener::open_url` and cancel.
6. `RunEvent::Exit` (and window close on macOS, since quitting = closing here): `shutdown()`.

`loading.html`: the "Starting up" screen from the canvas (serif title, thin progress bar, mono caption), plus the error state. Inline CSS only, light and dark via `prefers-color-scheme`.

- [ ] **Step 1:** Scaffold the project and config files; `cargo check` in `src-tauri` (container: install `libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev` via apt; if apt is unavailable, note it in the commit and rely on the Mac build).
- [ ] **Step 2:** Implement `backend.rs` with a unit test `port_is_free_after_pick` and a test spawning `python3 -c "import sys; sys.stdin.read()"` as a stand-in: `shutdown()` returns within 2 s and the process is gone.
- [ ] **Step 3:** Implement `main.rs` and `loading.html`.
- [ ] **Step 4: Verify** `cargo test` passes; if the Linux toolchain installed, `npm run tauri build -- --bundles deb` succeeds and the app starts under `xvfb-run`, reaching `/api/health` (check via `curl` on the logged port).
- [ ] **Step 5: Commit** `feat(desktop): Tauri shell that runs and supervises the backend`.

---

### Task 8: End-to-end check and Mac checklist

**Files:**
- Create: `desktop/e2e/fake_ollama.py`, `desktop/e2e/run_setup_e2e.py`
- Modify: `desktop/README.md`

**Interfaces:**
- Consumes: bundle from Task 6, launcher flags from Tasks 1–2, UI from Task 5.
- `fake_ollama.py --port 11434 --start-empty`: `/api/tags` initially empty; `/api/pull` streams 5 progress lines over ~2 s then adds the model; `/api/embed` returns random 768-d vectors; `/v1/chat/completions` streams "Answer [1]".

- [ ] **Step 1:** Write `run_setup_e2e.py` (Playwright, Chromium at `/opt/pw-browsers/chromium` in the container): fresh `KNOWLEDGE_AGENT_HOME`; start the bundled launcher; path A — fake Ollama stopped at first, assert the install screen and "Waiting for Ollama", start the fake, assert it advances on its own within 5 s, Continue through both downloads, assert the finishing screen then `/connections/`; seed one document + chunk via psql as in M1's test; ask a question on `/` and assert a `[1]` citation and a source card. Path B (second fresh home) — keys with Voyage/Anthropic calls stubbed by pointing `VOYAGE_BASE_URL`/`ANTHROPIC_BASE_URL` overrides at a local stub (add those two settings with the real URLs as defaults if they don't exist); assert a bad Voyage key shows the error under its field, then a good one lands on `/connections/`.
- [ ] **Step 2: Run** it. Expected: both paths print `PASS`, and after the script stops the launcher, `pgrep -f "postgres -D"` is empty.
- [ ] **Step 3:** Add to `desktop/README.md`: how to build the app (`build_backend.py`, then `cd desktop/tauri && npm install && npm run tauri build`), where the `.dmg` lands, and the Mac checklist: (1) open the `.dmg`, drag to Applications; (2) first open: right-click → Open to get past the unidentified-developer prompt; (3) loading screen then Welcome; (4) local path with Ollama quit, then started; (5) models download, Continue, lands on Connections; (6) connect Notion, Sync now, ask a question; (7) quit with ⌘Q, `pgrep postgres` prints nothing; (8) reopen: no setup, straight to the app with history intact.
- [ ] **Step 4: Commit** `test(desktop): end-to-end setup check and build docs`.
