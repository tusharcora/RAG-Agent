# Desktop online accounts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One-click Connect for Notion and Jira, and optional Google/GitHub sign-in, in the desktop app, through a small Cloudflare Worker that holds the provider secrets.

**Architecture:** A TypeScript Worker (`desktop/broker/`) runs the OAuth dance and keeps each result encrypted in KV for 120 s. The desktop API starts a flow with a PKCE-style challenge, the browser lands back on the API's loopback `/connect/finish`, and the API redeems the result with the verifier it kept in memory, then saves a connection or links an identity. The UI polls a status endpoint.

**Tech Stack:** Cloudflare Workers + KV, TypeScript, wrangler 3, vitest with `@cloudflare/vitest-pool-workers`; FastAPI, httpx, SQLAlchemy async, pytest; Next.js 16 (desktop static build); Playwright (e2e).

**Spec:** `docs/superpowers/specs/2026-10-15-desktop-online-accounts-design.md` (mockups: the "Desktop sign-in flow" canvas)

## Global Constraints

- Providers: `notion`, `jira`, `google`, `github` — exactly these four strings everywhere (URLs, `purpose`, KV entries).
- TTLs: pending sign-in 600 s; encrypted result 120 s; desktop in-memory state 600 s.
- `state`: 32–128 URL-safe chars; `challenge` = base64url(SHA-256(verifier)), 43 chars, no padding; `verifier` = 32 random bytes, base64url.
- `port` accepted by the Worker: integer 1024–65535. Finish URL: `http://127.0.0.1:{port}/api/connect/finish?state={state}`.
- Scopes: Notion `owner=user`; Jira `read:jira-work read:jira-user offline_access` with `prompt=consent` and `audience=api.atlassian.com`; Google `openid email profile`; GitHub `read:user user:email`.
- Worker secrets: `NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET`, `JIRA_CLIENT_ID`, `JIRA_CLIENT_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `RESULT_KEY` (32 bytes base64). Var: `BROKER_URL`.
- The Worker logs no codes, tokens, verifiers or identities.
- New API routes are desktop-only (404 when `settings.app_mode != "desktop"`), like `/auth/local` and `/setup/*`. Server mode behaviour is unchanged everywhere.
- Setting `auth_broker_url` (env `AUTH_BROKER_URL`, default `""`) in both api and worker configs; empty = feature off (Connect/sign-in hidden, Notion secret form stays).
- Copy (from the spec's error table and mockups): "Couldn't reach the sign-in service. Check your internet connection." · "{Provider} access wasn't granted." · "This sign-in link has expired. Start again from the app." · "Finish signing in in your browser…" · "Connected to Notion ✓" / "Signed in as {name}" · "You can close this tab and go back to Knowledge Agent."
- Provider logos: only official asset files, never redrawn. Buttons render `/brand/google.svg` and `/brand/github.svg` from `services/frontend/public/brand/` when those files exist (the user supplies them); without them the buttons are text-only.
- Commits: author `Tushar <tusharcs0112@gmail.com>`; trailers `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01HD8TDe18vrskpYykhwe7z3`.

## Review Focus

1. **Signing in changes the local user's email**, and `/auth/local` finds the local user *by email* today — it would create a second user and org and the app would look empty. The local user must be found by a stable id. (Task 4 test `test_auth_local_same_user_after_sign_in`.)
2. **The finish link opened twice, or after the app restarted** (in-memory state gone): a friendly "expired" page, no 500, nothing saved twice. (Task 2 tests `test_finish_unknown_state_page`, `test_finish_twice`.)
3. **Two Connect clicks for the same provider** (double click, two windows): both flows stay valid and independent; finishing either saves exactly one connection row. (Task 2 test `test_two_flows_same_provider`.)
4. **The person clicks Deny** on the provider page: card and page show "{Provider} access wasn't granted.", nothing saved. (Task 1 test `test_callback_error_result`, Task 2 test `test_finish_denied`.)
5. **No internet when clicking Connect**: the card must not sit on "Finish signing in…" for 10 minutes while the browser shows a connection error. Start checks the Worker's `/health` (2 s timeout) first and answers 502 with "Couldn't reach the sign-in service. Check your internet connection."; a Worker lost between start and finish gives the same message on the finish page and `error` status. (Task 2 tests `test_start_broker_unreachable`, `test_finish_broker_unreachable`.)

---

## File Structure

```
desktop/broker/
  package.json, tsconfig.json, wrangler.toml, vitest.config.ts
  src/index.ts          router: /start, /callback, /redeem, /refresh/jira
  src/providers.ts      per-provider authorize URL, code exchange, identity fetch
  src/crypto.ts         sha256 base64url, AES-GCM seal/open with RESULT_KEY
  test/broker.test.ts   flows with providers faked via fetchMock
  README.md             register the 4 provider apps, secrets, deploy
desktop/launcher.py     child_env passes AUTH_BROKER_URL
services/api/app/
  core/config.py                    auth_broker_url
  integrations/broker.py            start_url(), redeem(), refresh_jira()
  integrations/connections.py       save_notion_connection(), save_jira_connection()  (moved out of the oauth routes)
  api/routes/connect.py             /connect/{purpose}/start, /connect/finish, /connect/status/{state}, /connect/{state}/cancel
  api/routes/account.py             GET /account, POST /account/sign-out, POST /account/prompt-seen
  api/routes/{notion_oauth,jira_oauth}.py   call the moved save functions
  api/routes/auth.py                /auth/local finds the local user by stable id
  integrations/jira_auth.py         desktop: refresh via broker
services/worker/app/
  core/config.py                    auth_broker_url
  integrations/jira.py              desktop: refresh via broker
services/frontend/
  lib/connect.ts                    startConnect(), pollConnect(), cancelConnect(), getAccount(), signOut(), markPromptSeen()
  components/connections/ConnectionCard.tsx   Connect buttons + waiting state
  components/setup/AccountStep.tsx  "Make it yours" first-open screen
  components/settings/AccountSection.tsx
  components/ProviderButton.tsx     Continue with Google/GitHub (official mark if present)
  app/setup/page.tsx, components/Sidebar.tsx, app/settings/page.tsx
desktop/e2e/fake_broker.py, desktop/e2e/run_setup_e2e.py (path D)
```

---

### Task 1: The sign-in Worker

**Files:** Create everything under `desktop/broker/` listed above. Add `desktop/broker/node_modules/` and `.wrangler/` to `.gitignore`.

**Interfaces:**
- Produces (HTTP contract the API relies on):
  - `GET /start/{provider}?state&challenge&port` → 302 to the provider; 400 HTML page on bad input or reused state.
  - `GET /callback/{provider}?code&state` | `?error&state` → 302 to `http://127.0.0.1:{port}/api/connect/finish?state={state}`; unknown/expired state → 400 HTML page "This sign-in link has expired. Start again from the app."
  - `POST /redeem {state, verifier}` → 200 JSON result; 403 wrong verifier (entry deleted); 404 unknown/expired/already redeemed.
  - Result JSON shapes: notion `{provider:"notion", access_token, workspace_id, workspace_name, bot_id}`; jira `{provider:"jira", access_token, refresh_token, expires_in, cloud_id, site_url, site_name}`; google/github `{provider, provider_user_id, email, name}`; any failure `{provider, error}` (readable message, e.g. "Notion access wasn't granted.").
  - `POST /refresh/jira {refresh_token}` → 200 `{access_token, refresh_token, expires_in}`; 400 `{error}`.
  - `GET /health` → 200 `ok`.
- Provider endpoints: Notion authorize `https://api.notion.com/v1/oauth/authorize`, token `https://api.notion.com/v1/oauth/token` (HTTP Basic client id:secret); Jira authorize `https://auth.atlassian.com/authorize`, token `https://auth.atlassian.com/oauth/token`, sites `https://api.atlassian.com/oauth/token/accessible-resources` (first site); Google authorize `https://accounts.google.com/o/oauth2/v2/auth`, token `https://oauth2.googleapis.com/token`, identity `https://openidconnect.googleapis.com/v1/userinfo`; GitHub authorize `https://github.com/login/oauth/authorize`, token `https://github.com/login/oauth/access_token` (Accept: application/json), identity `https://api.github.com/user` + `https://api.github.com/user/emails` (primary verified), with a `User-Agent` header.

- [ ] **Step 1:** Scaffold (`npm init`, devDependencies `wrangler@3`, `vitest`, `@cloudflare/vitest-pool-workers`, `typescript`, `@cloudflare/workers-types`); `wrangler.toml` with `name = "knowledge-agent-auth"`, `main = "src/index.ts"`, a `PENDING` KV binding (placeholder id, documented in README), `[vars] BROKER_URL = ""`. Test config provides fake secrets and `BROKER_URL = "https://broker.test"`.
- [ ] **Step 2: Write failing tests** in `test/broker.test.ts` (providers faked with the pool's `fetchMock`):
  - `start_redirects_to_provider` (each provider: Location host, `redirect_uri=https://broker.test/callback/{p}`, `state`, scopes from Global Constraints)
  - `start_rejects_bad_input` (unknown provider, port 80, port 70000, short state, bad challenge → 400)
  - `start_rejects_reused_state`
  - `notion_flow_round_trip` (start → callback → 302 to `http://127.0.0.1:47821/api/connect/finish?state=…` → redeem with the right verifier → notion result shape)
  - `jira_flow_includes_site` (cloud_id/site_url/site_name from the first accessible resource)
  - `google_and_github_return_identity_only` (no `access_token` key in the result)
  - `redeem_wrong_verifier_403_then_gone` (a second, correct redeem → 404)
  - `redeem_twice_404`
  - `callback_error_result` (`?error=access_denied` → result `{provider:"notion", error:"Notion access wasn't granted."}`)
  - `callback_unknown_state_400`
  - `result_is_encrypted_in_kv` (raw KV value contains neither the access token nor the email)
  - `refresh_jira` (passes client id/secret to Atlassian, returns the new tokens; Atlassian 400 → 400 `{error}`)
  - `health_ok`
- [ ] **Step 3: Run** `cd desktop/broker && npx vitest run`. Expected: FAIL (no handlers).
- [ ] **Step 4: Implement** `src/crypto.ts` (`challengeOf(verifier): Promise<string>`, `seal(obj, keyB64): Promise<string>`, `open(sealed, keyB64): Promise<unknown>` — AES-GCM, 12-byte random IV prefixed), `src/providers.ts` (`authorizeUrl(provider, env, state): string`, `exchange(provider, env, code): Promise<Result>`), `src/index.ts` (router; KV `pending:{state}` 600 s, `result:{state}` 120 s storing `{challenge, sealed}`; no `console.log` of secrets).
- [ ] **Step 5: Run tests.** Expected: PASS. `npx tsc --noEmit` clean.
- [ ] **Step 6:** Write `desktop/broker/README.md`: create the KV namespace; register each provider app with its callback `https://<your-worker>.workers.dev/callback/{provider}` (Notion: public integration, "read content"; Atlassian developer console: OAuth 2.0 (3LO) with the Jira scopes; Google Cloud: OAuth client "Web application"; GitHub: OAuth App); `wrangler secret put` for each secret (`openssl rand -base64 32` for `RESULT_KEY`); set `BROKER_URL`; `wrangler deploy`; then set the desktop default (Task 2's `DEFAULT_AUTH_BROKER_URL`).
- [ ] **Step 7: Commit** `feat(broker): Cloudflare Worker for desktop sign-in`.

---

### Task 2: Connect flow in the desktop API (Notion and Jira)

**Files:**
- Create: `services/api/app/integrations/broker.py`, `services/api/app/integrations/connections.py`, `services/api/app/api/routes/connect.py`, `services/api/tests/test_connect.py`
- Modify: `services/api/app/core/config.py`, `services/api/app/api/routes/{notion_oauth,jira_oauth}.py`, `services/api/app/main.py` (include `connect.router` at `/connect`), `desktop/launcher.py` (+ test in `desktop/tests/test_paths_config.py`)

**Interfaces:**
- Consumes: Task 1's HTTP contract.
- Produces:
  - `broker.start_url(purpose: str, state: str, challenge: str, port: int) -> str`
  - `broker.check_reachable() -> None` — GET `{auth_broker_url}/health` with a 2 s timeout; raises `BrokerUnavailable`.
  - `broker.redeem(state: str, verifier: str) -> dict` — raises `BrokerUnavailable` (connect/timeout errors), `BrokerExpired` (403/404).
  - `broker.refresh_jira(refresh_token: str) -> dict` — raises `BrokerUnavailable`, `BrokerRefused(message)`.
  - `broker.TRANSPORT: httpx.AsyncBaseTransport | None` for tests.
  - `connections.save_notion_connection(session, org_id, data: dict) -> OAuthConnection` and `connections.save_jira_connection(session, org_id, token_data: dict, site: dict) -> OAuthConnection` — exactly the save blocks from today's callbacks (encryption, upsert by `(org_id, provider)`), which the server callbacks now call.
  - Module state in `connect.py`: `_FLOWS: dict[str, Flow]` where `Flow(purpose, verifier, user_id, org_id, created, status: "pending"|"done"|"error", message: str|None)`; `IDENTITY_HANDLER: Callable | None` hook that Task 4 fills for google/github.
  - Routes: `POST /connect/{purpose}/start` → `{state, url}`; `GET /connect/finish?state` → HTML; `GET /connect/status/{state}` → `{status, message}`; `POST /connect/{state}/cancel` → `{}`.
  - Launcher: `DEFAULT_AUTH_BROKER_URL = ""` constant in `desktop/launcher.py`; `child_env` sets `AUTH_BROKER_URL` from `os.environ.get("AUTH_BROKER_URL", DEFAULT_AUTH_BROKER_URL)`.

Rules: start/status/cancel require `require_auth` + owner; finish needs no session (state is the credential) and is still behind the desktop Host check. `start` 404s for `google`/`github` until Task 4 sets `IDENTITY_HANDLER`, and returns 409 `{"detail": "Sign-in service isn't configured."}` when `auth_broker_url` is empty. `port` = the port the API is serving on (from the request's base URL). Flows older than 600 s are dropped lazily. Finish pages are self-contained HTML (inline CSS, light/dark via `prefers-color-scheme`, no scripts) with the spec copy.

- [ ] **Step 1: Write failing tests** (`test_connect.py`, fake broker via `broker.TRANSPORT`, pgserver DB fixture from `tests/conftest.py`):
  - `test_routes_404_in_server_mode`
  - `test_start_requires_configured_broker` (409 with empty `auth_broker_url`)
  - `test_start_broker_unreachable` (health check fails → 502 with the "Couldn't reach the sign-in service…" detail, no flow kept)
  - `test_start_returns_url_with_challenge` (url host = broker, `challenge == base64url(sha256(verifier kept in _FLOWS))`, `port` = test port)
  - `test_finish_notion_saves_connection` (row with encrypted token, `workspace_name` from result; page contains "Connected to Notion"; status → done)
  - `test_finish_jira_saves_site` (`workspace_id == cloud_id`, `site_url`, `expires_at` ≈ now + expires_in)
  - `test_finish_denied` (result `{error}` → page shows message, status error with that message, no row)
  - `test_finish_unknown_state_page` (200 page with the expired copy, no exception)
  - `test_finish_twice` (second call → expired page; one row)
  - `test_finish_broker_unreachable` (transport raises ConnectError → page "Couldn't reach the sign-in service…", status error)
  - `test_two_flows_same_provider` (two starts, finish the second → one row, first still pending)
  - `test_cancel_forgets_flow` (status after cancel → 404)
  - `test_server_callbacks_unchanged` (existing `/oauth/notion/callback` path still saves via the moved function — call it with a faked token endpoint)
  - In `desktop/tests/test_paths_config.py`: `test_child_env_passes_broker_url` (env override wins; default constant otherwise).
- [ ] **Step 2: Run** `cd services/api && /home/claude/dvenv/bin/python -m pytest tests/test_connect.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the modules and routes; move the save blocks into `connections.py` first (no behaviour change), then build `connect.py` on top.
- [ ] **Step 4: Run** the full api and desktop suites. Expected: PASS.
- [ ] **Step 5: Commit** `feat(desktop): connect Notion and Jira through the sign-in service`.

---

### Task 3: Jira token refresh through the Worker in desktop mode

**Files:** Modify `services/api/app/integrations/jira_auth.py`, `services/worker/app/integrations/jira.py`, `services/worker/app/core/config.py`. Create `services/worker/app/integrations/broker.py` with `refresh_jira(refresh_token) -> dict` (the worker's copy of the api's `broker.refresh_jira`, same contract and exceptions; duplicated per the repo's api/worker convention). Tests: `services/api/tests/test_jira_refresh.py`, `services/worker/tests/test_jira_refresh.py`.

**Interfaces:**
- Consumes: `broker.refresh_jira` (api, Task 2); Worker `/refresh/jira` (Task 1).
- Produces: unchanged signatures `jira_auth.ensure_fresh_token(session, connection) -> str` and worker `_ensure_fresh_token(connection) -> str`; in desktop mode (`app_mode == "desktop"`) the refresh call goes to the broker, in server mode to Atlassian as today.
- Refused refresh (revoked): api raises `HTTPException(400, "Jira needs reconnecting. Use Reconnect on the Connections page.")`; worker raises `RuntimeError` with the same text (becomes the event's error → dead-letter after retries, shown on the card).

- [ ] **Step 1: Failing tests** (both services): `test_desktop_refresh_uses_broker` (expired connection → one POST to `{auth_broker_url}/refresh/jira`, none to Atlassian; new tokens stored encrypted, `expires_at` advanced); `test_server_refresh_uses_atlassian` (unchanged path); `test_desktop_refresh_refused_message`.
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement. Step 4: Run both suites, expect PASS.**
- [ ] **Step 5: Commit** `feat(desktop): refresh Jira tokens through the sign-in service`.

---

### Task 4: Optional app sign-in (identity) in the API

**Files:** Create `services/api/app/api/routes/account.py`, `services/api/tests/test_account.py`. Modify `connect.py` (set `IDENTITY_HANDLER`), `services/api/app/api/routes/auth.py` (`/auth/local`), `services/api/app/core/desktop_state.py` usage (config keys), `services/api/app/main.py` (include `account.router` at `/account`).

**Interfaces:**
- Consumes: `connect.IDENTITY_HANDLER` hook and `Flow` (Task 2); `desktop_state.load_config/save_config`.
- Produces:
  - `account.link_identity(session, user_id, result: dict) -> str` (returns the display name) — upserts `oauth_identities` (`UNIQUE(provider, provider_user_id)`), deletes the user's other identity rows (one at a time), sets `users.email` = result email and `users.display_name` = result name; registered as `connect.IDENTITY_HANDLER`; finish page "Signed in as {name}".
  - `GET /account` → `{signed_in: bool, provider: str|None, email: str|None, name: str|None, broker_configured: bool, prompt_seen: bool}` (owner).
  - `POST /account/sign-out` → deletes identity rows, restores `email = "you@this-computer.local"`, `display_name = None`.
  - `POST /account/prompt-seen` → `save_config(account_prompt_seen=True)`.
  - `/auth/local` stable lookup: `desktop.json` key `local_user_id` (written when the local user is created); fallback for existing installs: the user with `LOCAL_EMAIL`, else the owner of the oldest organization; backfill `local_user_id` when found by fallback.
- Edge: a sign-in whose email already belongs to a *different* `users` row (not possible on a fresh desktop, but guard it) → finish page error "That account is already linked to another profile on this computer." and no change.

- [ ] **Step 1: Failing tests:** `test_finish_google_links_identity_and_updates_user`; `test_github_replaces_google_link`; `test_sign_out_restores_local_profile_keeps_data` (connections and chat rows untouched); `test_auth_local_same_user_after_sign_in` (sign in → `/auth/local` returns the same `user_id`/`org_id`, still one user and one org); `test_auth_local_backfills_local_user_id` (no key in desktop.json → found by email → key written); `test_account_status_and_prompt_seen`; `test_routes_404_in_server_mode`.
- [ ] **Step 2: Run, expect FAIL. Step 3: Implement. Step 4: Run api suite, expect PASS.**
- [ ] **Step 5: Commit** `feat(desktop): optional Google/GitHub sign-in for the local profile`.

---

### Task 5: Desktop UI — Connect buttons, first-open sign-in, Settings → Account

**Files:** Create `services/frontend/lib/connect.ts`, `components/ProviderButton.tsx`, `components/setup/AccountStep.tsx`, `components/settings/AccountSection.tsx`, `public/brand/README.md` (where to get the official files; nothing else in that folder). Modify `components/connections/ConnectionCard.tsx`, `app/setup/page.tsx`, `components/Sidebar.tsx`, `app/settings/page.tsx`, `lib/auth-context.tsx` (expose `account`).

**Interfaces:**
- Consumes: Task 2 and Task 4 routes.
- Produces (`lib/connect.ts`): `startConnect(purpose) -> Promise<{state, url}>`; `pollConnect(state, {intervalMs = 2000, timeoutMs = 600000, signal}) -> Promise<"done">` (rejects with the status message); `cancelConnect(state)`; `getAccount() -> Promise<Account>`; `signOut()`; `markPromptSeen()`.

Behaviour (copy and layout from the canvas screens 1, 4–5, 7–8; existing Tailwind tokens, light/dark):
- Opening the provider page: `window.open(url, "_blank", "noopener")` (Tauri routes it to the system browser).
- Connections (desktop, `broker_configured`): Notion "Connect Notion" + "or paste an integration secret" (reveals the existing form); Jira "Connect Jira"; waiting state per screen 5 with Cancel and "Reopen the sign-in page" (reopens the same `url`); on done → refresh connections and show the existing "Syncing…" badge; on error → message + Try again. Connected Notion: "Choose pages in Notion" reruns Connect. Without `broker_configured`: today's behaviour.
- `/setup`: if `broker_configured && !prompt_seen && !setup_complete`, show `AccountStep` first (screen 1: Continue with Google, Continue with GitHub, Skip for now); Skip or a finished sign-in → `markPromptSeen()` then the existing flow. While waiting: "Finish signing in in your browser…" + Cancel.
- Settings: an Account section (desktop only) per screen 8: signed out → the two buttons; signed in → name, email, provider, Sign out; the "Sharing with teammates — coming later" note.
- Sidebar workspace card: the user's name and initial when signed in (no remote images).
- Jira card: when the connection's last sync failed with "Jira needs reconnecting…" (Task 3), the card's action is "Reconnect Jira" (reruns Connect).
- `ProviderButton`: renders `<img src="/brand/{google|github}.svg" alt="">` only when `NEXT_PUBLIC_BRAND_MARKS=1` (set by the user once the official files are in `public/brand/`); otherwise text only.

- [ ] **Step 1:** Implement `lib/connect.ts`, then the components.
- [ ] **Step 2: Verify** `npx tsc --noEmit -p .` clean; `DESKTOP_BUILD=1` build (Google-fonts stub trick in the container; restore `layout.tsx`, `next-env.d.ts`, `tsconfig.tsbuildinfo`) and the plain server build both succeed.
- [ ] **Step 3: Commit** `feat(frontend): desktop Connect buttons, optional sign-in, account settings`.

UI behaviour is exercised end-to-end in Task 6.

---

### Task 6: End-to-end with a fake Worker; docs

**Files:** Create `desktop/e2e/fake_broker.py`. Modify `desktop/e2e/run_setup_e2e.py` (path D), `desktop/README.md`, `desktop/start-dev.command` (pass through `AUTH_BROKER_URL` if set).

**Interfaces:**
- Consumes: Task 1's HTTP contract (the fake implements `/start/{p}` → immediately 302 to its own `/callback/{p}?code=fake&state=…` → 302 to the finish URL; `/redeem` with the same verifier check; canned results: Notion workspace "E2E Workspace", Google identity name "E2E Person").

- [ ] **Step 1:** Path D: fresh home, fake Ollama with both models, fake broker, launcher with `AUTH_BROKER_URL`; on `/setup`: "Make it yours" shown → Continue with Google → the popup tab lands on "Signed in as E2E Person" → app moves on → finish setup → Connections → Connect Notion → popup lands on "Connected to Notion" → card shows the workspace; sidebar shows "E2E Person"; Settings → Account → Sign out → sidebar back to "My workspace". Also: launcher without `AUTH_BROKER_URL` shows no "Make it yours" and the Notion secret form (existing paths A–C cover the rest).
- [ ] **Step 2: Run** the whole e2e (paths A, B, C, D). Expected: all PASS, "leftover processes from this run: none".
- [ ] **Step 3:** README: a "Connect and sign-in" section (what the Worker does and doesn't see; how to point a dev build at your Worker with `AUTH_BROKER_URL`; link to `desktop/broker/README.md`) and two Mac checklist lines (Connect Notion with the real Worker; sign in with Google).
- [ ] **Step 4: Commit** `test(desktop): end-to-end Connect and sign-in with a fake sign-in service`.
