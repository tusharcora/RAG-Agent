# GitHub as a Knowledge Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect GitHub (via a read-only GitHub App) like Notion and Jira, syncing Markdown files, issues and PRs from the repos a person picks, in desktop and web mode.

**Architecture:** The sign-in Worker gains a `github_repos` purpose (OAuth authorize → token + installations; refresh). The api saves one GitHub connection per org, enumerates repos/trees/issues into budgeted, version-skipped events; the worker fetches and embeds each item. A persisted `needs_reconnect_at` column replaces Jira's in-memory flag and serves GitHub too.

**Tech Stack:** Cloudflare Worker (TypeScript, vitest), FastAPI + SQLAlchemy async (Python 3.12, pytest, httpx.MockTransport), worker (aio-pika / desktop local queue), Next.js 16 + Tailwind, Playwright e2e.

**Spec:** `docs/superpowers/specs/2026-10-15-github-source-design.md` (revision 3) — read it first; this plan argues from it.

## Global Constraints

- Migrations are append-only: add `infra/postgres/015_github_source.sql`; never edit 001–014. Mirror every 015 statement in `services/api/app/core/schema.py`. Nothing in 015 may *use* the `'github'` enum value (it is added in the same transaction).
- Models are duplicated: change both `services/api/app/models/rag.py` and `services/worker/app/models/rag.py`.
- Tokens are encrypted at rest with `app.core.crypto.encrypt_token/decrypt_token` (api and worker), as for Jira.
- Never log tokens, codes, verifiers, refresh tokens or GitHub user emails.
- Worker secrets/vars: `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET` (secrets), `GITHUB_APP_SLUG` (var). Web settings in api **and** worker: `github_source_app_slug`, `github_source_client_id`, `github_source_client_secret`; api only: `github_source_redirect_uri`. Defaults `""`.
- Config: `github_history_days: int = 365`, `max_github_items_per_sync: int = 500` (api); `github_api_url: str = "https://api.github.com"` (api **and** worker, added in Task 3; every GitHub REST call uses it so the e2e can point at a fake).
- Purpose / provider names: Worker purpose and connect purpose `github_repos`; `oauth_connections.provider = 'github'`; `documents.source = 'github'`; routing keys `rag.github_file_updated`, `rag.github_issue_updated`, `rag.github_pr_updated`.
- external ids: `file:{repo_id}:{path}`, `issue:{repo_id}:{number}`, `pr:{repo_id}:{number}`. Titles: `{full_name} · {path}` / `{full_name} #{n}: {title}`.
- Copy (verbatim): "GitHub access wasn't granted." · "GitHub needs reconnecting. Use Reconnect on the Connections page." · "No repositories yet" · "After choosing them on GitHub, come back here and press Sync now." · "GitHub is disconnected. Connect it again to sync." (via `connection_state.LABELS["github"] = "GitHub"`).
- Choose-repos link: `https://github.com/apps/{slug}/installations/new`.
- Commit as `git -c user.name=Tushar -c user.email=tusharcs0112@gmail.com commit`, trailers `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01HD8TDe18vrskpYykhwe7z3`. Never read `.env`. Kill processes by pid, never `pkill -f`.
- Test commands: api/worker `/home/claude/dvenv/bin/python -m pytest -q` (in `services/api`, `services/worker`); Worker `cd desktop/broker && npx vitest run`; frontend `cd services/frontend && npx tsc --noEmit`.

## Review Focus

1. **A repo with thousands of Markdown files and issues** → the first syncs each enqueue ≤ 500 changed items spread across repos, later syncs converge to zero enqueued; no repo starves. (Task 5: `test_budget_converges_large_repo`, `test_round_robin_rotates`.)
2. **Token rotated by another process mid-sync** → a 401 with a stale token re-reads the row and reuses the newer token without calling refresh; a revoked unexpired token still refreshes. (Task 3: `test_refresh_reuses_rotated_token`, `test_401_with_unexpired_revoked_token_refreshes`.)
3. **GitHub listing fails part-way (rate limit/5xx on page 2 of repos, or of a full scan)** → nothing is deleted. (Task 5: `test_partial_repo_listing_deletes_nothing`, `test_incomplete_full_scan_prunes_nothing`.)
4. **Person connected but hasn't installed the app anywhere** → connect succeeds, card/finish page say "No repositories yet" with the install link; Sync now is a no-op, not an error. (Task 4 `test_finish_github_no_installations`; Task 5 `test_sync_with_no_installations`.)
5. **Disconnect → reconnect same account** → cursors were reset, so the next sync re-enqueues everything still on GitHub (no silent empty sync). (Task 2 `test_reset_github_state_on_every_delete_path`; Task 5 `test_resync_after_remove_documents`.)

---

### Task 1: Sign-in Worker `github_repos` purpose

**Files:**
- Modify: `desktop/broker/src/providers.ts` (PROVIDERS, DISPLAY_NAME, Env, result type, `authorizeUrl`, `exchange`, new `refreshGithubRepos`)
- Modify: `desktop/broker/src/index.ts` (route `POST /refresh/github_repos`)
- Modify: `desktop/broker/test/broker.test.ts`, `desktop/broker/README.md` (GitHub App registration section per spec §1), `desktop/broker/wrangler.toml` (comment listing the new secrets; `GITHUB_APP_SLUG = ""` under `[vars]`)

**Interfaces:**
- Produces (HTTP): `GET /start/github_repos` → 302 to `https://github.com/login/oauth/authorize?client_id={GITHUB_APP_CLIENT_ID}&redirect_uri={BROKER_URL}/callback/github_repos&state={state}`.
- Produces (redeem result): `{provider:"github_repos", access_token, refresh_token, expires_in, refresh_token_expires_in, user_id:number, login:string, app_slug:string (env.GITHUB_APP_SLUG), installations:[{id:number, account_login:string, account_type:string}]}` or `{provider:"github_repos", error}`.
- Produces: `POST /refresh/github_repos {refresh_token}` → 200 `{access_token, refresh_token, expires_in, refresh_token_expires_in}` | 400 `{error}` | 502 `{error}`.

- [ ] **Step 1: Write failing tests** in `broker.test.ts` (fetchMock for github.com/api.github.com):
  - `start github_repos redirects to OAuth authorize with state` — Location host `github.com`, path `/login/oauth/authorize`, params `client_id`, `redirect_uri` ends `/callback/github_repos`, `state` equals the input.
  - `callback github_repos returns token, login and all installation pages` — `/user/installations?per_page=100&page=1` returns 100 items + `Link: rel="next"`, page 2 returns 1; redeemed result has `installations.length === 101`, `user_id`, `login`.
  - `callback github_repos treats 200 with error as denied` — token endpoint 200 `{error:"bad_verification_code"}` → result `{error:"GitHub access wasn't granted."}`.
  - `callback github_repos with ?error=access_denied` → same error result.
  - `callback github_repos with zero installations` → `installations: []`, no error.
  - `refresh github_repos maps bad_refresh_token (200 body) to 400`, `401 to 400`, `500 to 502`, `network error to 502`, success passes the four fields through.
- [ ] **Step 2: Run** `cd desktop/broker && npx vitest run` — new tests FAIL.
- [ ] **Step 3: Implement.** Token-endpoint calls send `Accept: application/json`; any `error` field ⇒ failure regardless of status. Installations: follow `Link` `rel="next"` until absent. `exchange("github_repos", …)` returns the result above; `refreshGithubRepos(env, refresh_token): Promise<RefreshOutcome>` mirrors `refreshJira`'s outcome type.
- [ ] **Step 4: Run** `npx vitest run` and `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(broker): github_repos purpose (GitHub App OAuth + installations, refresh)`.

### Task 2: Schema 015, models, cursor-reset helper

**Files:**
- Create: `infra/postgres/015_github_source.sql`
- Modify: `services/api/app/core/schema.py`, `services/api/app/models/rag.py`, `services/worker/app/models/rag.py`
- Create: `services/api/app/integrations/github_state.py`
- Modify: `services/api/app/integrations/connections.py` (`_reuse_row`), `services/api/app/api/routes/connections.py` (`_delete_documents`, disconnect, remove-documents)
- Test: `services/api/tests/test_github_schema.py`

**Interfaces:**
- Produces (SQL, exactly per spec §3): enum value `'github'`; `documents.external_version TEXT`; `oauth_connections.needs_reconnect_at TIMESTAMPTZ`; table `github_repos(id UUID PK DEFAULT uuid_generate_v4(), connection_id UUID NOT NULL REFERENCES oauth_connections ON DELETE CASCADE, installation_id BIGINT NOT NULL, repo_id BIGINT NOT NULL, full_name TEXT NOT NULL, html_url TEXT NOT NULL, default_branch TEXT NOT NULL, private BOOLEAN NOT NULL DEFAULT false, tree_sha TEXT, issues_cursor TIMESTAMPTZ, last_full_scan_at TIMESTAMPTZ, UNIQUE(connection_id, repo_id))`; table `github_failures(connection_id UUID REFERENCES oauth_connections ON DELETE CASCADE, external_id TEXT, version TEXT NOT NULL, attempts INT NOT NULL DEFAULT 1, last_error TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(connection_id, external_id))`; `oauth_connections.github_rr_offset INT NOT NULL DEFAULT 0` (round-robin start, spec §5.2 "stored per connection").
- Produces (models, both services): `GithubRepo`, `GithubFailure`; `Document.external_version: str | None`; `OAuthConnection.needs_reconnect_at: datetime | None`, `OAuthConnection.github_rr_offset: int`.
- Produces: `async def reset_github_state(session: AsyncSession, connection_id: uuid.UUID) -> None` in `github_state.py` — deletes that connection's `github_repos` and `github_failures` rows (no commit). Called wherever a connection's documents are deleted: `_delete_documents` (disconnect-remove, remove-documents) and `_reuse_row`'s different-workspace branch.

- [ ] **Step 1: Write failing tests** (`test_github_schema.py`, using the existing pgserver fixture/conftest pattern):
  - `test_migration_015_applies_on_fresh_db_and_schema_py_is_idempotent` — apply all `infra/postgres/*.sql` then run `schema.py`'s statements twice; `'github'` in `enum_range(NULL::rag_source)`; both tables and three columns exist.
  - `test_reset_github_state_on_every_delete_path` — seed a GitHub connection with `github_repos`/`github_failures` rows; for each of (disconnect remove_documents=true, remove-documents after keep, `_reuse_row` with a different workspace id) the rows are gone; disconnect with keep leaves them.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** the SQL, schema.py mirror, models, helper and the three call sites.
- [ ] **Step 4: Run** api and worker suites — PASS (existing tests unaffected).
- [ ] **Step 5: Commit** `feat(db): GitHub source schema (015), cursor reset on document deletion`.

### Task 3: Persisted needs-reconnect (Jira moves onto it) + GitHub token refresh

**Files:**
- Create: `services/api/app/integrations/github_auth.py`, `services/worker/app/integrations/github.py` (refresh part; client in Task 6)
- Modify: `services/api/app/integrations/jira_auth.py` (drop the in-memory set), `services/worker/app/integrations/jira.py`, `services/api/app/api/routes/connections.py` (report from column; disconnect clears it), `services/api/app/integrations/connections.py` (reconnect clears it), `services/api/app/integrations/broker.py` + `services/worker/app/integrations/broker.py` (`refresh_github_repos`), `services/api/app/core/config.py` + `services/worker/app/core/config.py` (spec §1 settings and `github_api_url`), `services/api/app/integrations/connection_state.py` (`LABELS["github"]`)
- Test: `services/api/tests/test_github_refresh.py`, update `services/api/tests/test_jira_refresh.py`, `services/worker/tests/test_jira_refresh.py`, new `services/worker/tests/test_github_refresh.py`

**Interfaces:**
- Consumes: `broker.BrokerRefused` / `BrokerUnavailable` (existing classes).
- Produces: `async def refresh_github_repos(refresh_token: str) -> dict` in both broker clients (400 → `BrokerRefused`, else non-200 → `BrokerUnavailable`).
- Produces (api): `async def github_access_token(connection_id: uuid.UUID, failed_token: str | None = None) -> str` in `github_auth.py`. Opens its **own** session (`app.core.db` session factory), `SELECT … FOR UPDATE` the row, reuse rule per spec §4 step 1, refresh (desktop: broker; web: `POST https://github.com/login/oauth/access_token` with `grant_type=refresh_token`, client id/secret, `Accept: application/json`, any `error` field = refused if `bad_refresh_token` else unavailable), write tokens only if `disconnected_at IS NULL`, clear `needs_reconnect_at`, commit. Refused → set `needs_reconnect_at`, commit, raise `HTTPException(409, "GitHub needs reconnecting. Use Reconnect on the Connections page.")`; unavailable → `HTTPException(502, …)`. Disconnected → `require_connected` 409.
- Produces (worker): `async def github_access_token(connection_id: uuid.UUID, failed_token: str | None = None) -> str` in `services/worker/app/integrations/github.py`, same rules; raises `GithubNeedsReconnect` / `GithubUnavailable` / `GithubDisconnected` (module exceptions) instead of HTTPException.
- Produces: `needs_reconnect` in `GET /connections` = `connection.needs_reconnect_at is not None` for every provider. Jira: both refresh paths set/clear the column with the same lock-and-reread pattern.

- [ ] **Step 1: Write failing tests:**
  - `test_refresh_reuses_rotated_token` — row holds token B valid for 1 h; `github_access_token(id, failed_token="A")` returns B with zero GitHub/broker calls.
  - `test_401_with_unexpired_revoked_token_refreshes` — row holds A (valid); `failed_token="A"` → one refresh call, returns new token, row updated.
  - `test_concurrent_refreshers_call_github_once` — two `asyncio.gather` calls on an expired row → exactly one refresh call; both return the same new token.
  - `test_refused_sets_needs_reconnect_and_get_connections_reports_it`; `test_successful_refresh_clears_needs_reconnect`; `test_disconnect_clears_needs_reconnect`; `test_refresh_does_not_write_tokens_onto_disconnected_row`.
  - `test_desktop_refresh_goes_through_broker` / `test_web_refresh_goes_to_github_directly`; `test_200_with_bad_refresh_token_is_refused`.
  - Jira: update existing in-memory-flag tests to assert the column; `test_jira_worker_refusal_sets_column` (worker).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** api + worker suites — PASS.
- [ ] **Step 5: Commit** `feat: persisted needs-reconnect (Jira + GitHub), locked GitHub token refresh`.

### Task 4: Connecting GitHub (desktop Connect + web OAuth + finish page)

**Files:**
- Modify: `services/api/app/integrations/connections.py` (`save_github_connection`), `services/api/app/api/routes/connect.py` (`PURPOSES` adds `github_repos`; `DISPLAY_NAME["github_repos"]="GitHub"`; finish branch), `services/api/app/api/connect_page.py` (`PROVIDER_NAME["github_repos"]="GitHub"`, logo file `github`)
- Create: `services/api/app/api/routes/github_source_oauth.py`; Modify: `services/api/app/main.py` (include router at `/oauth/github_source`)
- Test: `services/api/tests/test_github_connect.py`

**Interfaces:**
- Consumes: Task 1 result shape; Task 2 `reset_github_state`.
- Produces: `async def save_github_connection(session, org_id: uuid.UUID, result: dict) -> OAuthConnection` — row `provider="github"`, `workspace_id=str(result["user_id"])`, `workspace_name=result["login"]`, `site_url=f"https://github.com/{login}"`, `bot_id=result["app_slug"]` in desktop mode / `settings.github_source_app_slug` in web mode (the app slug, used for the choose-repos link; `bot_id` is otherwise Notion-only), encrypted tokens, `expires_at=now+expires_in`; reuse rule via `_reuse_row` (different user id ⇒ documents + GitHub state deleted); clears `needs_reconnect_at`. Installations are **not** stored here (Task 5 lists them each sync).
- Produces: finish page for `github_repos`: kind `connected`, provider tile `github`, card title = login, subtitle `"{n} repositories"` where n is summed from `GET /user/installations/{id}/repositories?per_page=1` `total_count` per installation (best effort; on error omit subtitle), or `"No repositories yet"` + lead text "After choosing them on GitHub, come back here and press Sync now." with a link to the choose-repos URL when there are no installations.
- Produces (web): `GET /oauth/github_source/authorize` (require_auth, owner/admin; Redis state `oauth_state:github_source:{state}` = org_id, TTL `oauth_state_ttl_seconds`) → 302 to the OAuth authorize URL with `github_source_redirect_uri`; `GET /oauth/github_source/callback?code&state` → exchange (Accept json; `error` field = denied), `GET /user`, `GET /user/installations`, `save_github_connection`, 302 `{frontend_url}/connections?connected=github`; bad/expired state → 400 like `jira_oauth.py`.

- [ ] **Step 1: Write failing tests:** `test_connect_github_repos_start_and_finish_saves_connection`; `test_finish_github_no_installations` (page contains "No repositories yet" and the choose link); `test_reconnect_same_user_keeps_documents`; `test_reconnect_other_user_deletes_documents_and_state`; `test_finish_github_denied_shows_not_granted`; web: `test_web_authorize_requires_auth_and_redirects`, `test_web_callback_bad_state_400`, `test_web_callback_saves_and_redirects`; `test_github_source_routes_404_without_config` (empty client id → 404).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** api suite — PASS.
- [ ] **Step 5: Commit** `feat(api): connect GitHub (desktop Connect purpose, web OAuth, finish page)`.

### Task 5: Sync enumeration (`POST /sync/github`, auto_sync)

**Files:**
- Create: `services/api/app/integrations/github_sync.py`
- Modify: `services/api/app/api/routes/sync.py` (route), `services/api/app/core/auto_sync.py` (provider branch), `services/api/app/core/config.py` (Global Constraints values)
- Test: `services/api/tests/test_github_sync.py` (fake GitHub via `httpx.MockTransport`, injected through a module-level `TRANSPORT` like `broker.py`)

**Interfaces:**
- Consumes: Task 3 `github_access_token(connection_id, failed_token)`; Task 2 models; `publish_event(session, routing_key, payload, dedupe_key, org_id)`.
- Produces: `async def sync_github_connection(session: AsyncSession, connection: OAuthConnection) -> dict` returning `{"published": int, "truncated": bool, "removed": int}` (same keys as Notion/Jira so auto_sync logging works).
- Produces (event payloads): files `{"connection_id", "org_id", "repo_id", "full_name", "path", "sha", "html_url"}`; issues/PRs `{"connection_id", "org_id", "repo_id", "full_name", "number", "updated_at", "html_url"}`. Dedupe keys `github:{connection_id}:{external_id}:{sha|updated_at}`.
- Algorithm (spec §5, decisions the spec leaves to here): build per-repo lazy candidate iterators for files and for issues; take one candidate per stream per pass in repo order starting at `connection.github_rr_offset % n_repos`; stop at `max_github_items_per_sync`; then set `github_rr_offset += 1`. Skip candidates whose version equals `documents.external_version` or the `github_failures.version` for that external id. A file stream marks its repo's `tree_sha` only when exhausted. An issue stream advances `issues_cursor` to the last examined `updated_at`; a full-scan stream sets `last_full_scan_at` and prunes only when it reached the last page. Return `truncated=True` when the budget stopped any stream.

- [ ] **Step 1: Write failing tests:**
  - `test_sync_lists_repos_across_installations_and_upserts` (2 installations, paginated).
  - `test_partial_repo_listing_deletes_nothing` (page 2 → 500): no repo rows or documents removed.
  - `test_removed_repo_deletes_its_documents` (complete listing without repo X).
  - `test_files_filter_md_mdx_and_size` (`.png`, 2 MB `.md` skipped; `docs/a.md`, `README.md`, `x.mdx` enqueued).
  - `test_unchanged_blob_not_enqueued` (`external_version` == sha).
  - `test_truncated_tree_deletes_nothing`; `test_complete_tree_deletes_removed_paths`; `test_empty_repo_409_is_skipped`; `test_unchanged_tree_sha_skips_listing`.
  - `test_budget_converges_large_repo` (1,200 changed files, cap 500: syncs enqueue 500, 500, 200, then 0 after versions are written by a fake worker step).
  - `test_round_robin_rotates` (3 repos, cap 4: first item of each repo appears; next sync starts at repo 2).
  - `test_issue_cursor_overlap_and_routing` (PRs → `rag.github_pr_updated`, `since` = cursor − 1 h).
  - `test_full_scan_recovers_missing_version_and_prunes_deleted`; `test_incomplete_full_scan_prunes_nothing` (budget hit mid-window).
  - `test_failed_version_is_skipped_until_changed`.
  - `test_sync_with_no_installations` (published 0, no error); `test_disconnected_409`; `test_needs_reconnect_409_message`.
  - `test_resync_after_remove_documents` (after remove-documents, next sync re-enqueues everything).
  - `test_auto_sync_runs_github`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `github_sync.py` (GitHub calls with tenacity on 5xx/429/secondary limits honouring `Retry-After`/`x-ratelimit-reset`; a 401 calls `github_access_token(id, failed_token)` once and retries), the route `POST /sync/github` (same deps/rate limiter as `/sync/jira`), auto_sync branch.
- [ ] **Step 4: Run** api suite — PASS.
- [ ] **Step 5: Commit** `feat(api): GitHub sync enumeration (budgeted, versioned, round-robin)`.

### Task 6: Worker handlers and dead-letter hook

**Files:**
- Modify: `services/worker/app/integrations/github.py` (client: `GithubClient` with retries + 401 → refresh once)
- Create: `services/worker/app/tasks/github.py`
- Modify: `services/worker/app/tasks/__init__.py` (register three handlers; add `ON_DEAD_LETTER: dict[str, Callable[[dict, str], Awaitable[None]]]` and `async def on_dead_letter(routing_key, payload, error) -> None`), `services/worker/app/main.py` and `services/worker/app/local_queue.py` (call `on_dead_letter` where they dead-letter; failures inside it are logged, never raised)
- Test: `services/worker/tests/test_github_tasks.py`

**Interfaces:**
- Consumes: Task 5 payloads; Task 3 worker `github_access_token`; existing `_sync_document(...)` in `embed.py` (reuse for chunk/embed/upsert — read its signature and extend it with an optional `external_version` argument rather than duplicating it).
- Produces: `handle_github_file_updated`, `handle_github_issue_updated`, `handle_github_pr_updated` (each `async (payload: dict) -> None`); `record_github_failure(payload, error)` registered in `ON_DEAD_LETTER` for all three keys (upsert `github_failures` with `version` = payload `sha`/`updated_at`, `attempts += 1`).
- Text assembly (spec §5 worker): issue = `# {title}\nState: {state} · Labels: {a, b}\n\n{body}\n\n` + one `**{login}:** {comment}` block per comment; PR = same header + body, then "Conversation", "Reviews", "Review comments", "Changed files" sections from the four endpoints. Files: blob content decoded from base64 (`GET /repos/{full_name}/git/blobs/{sha}`).

- [ ] **Step 1: Write failing tests:** `test_file_handler_embeds_and_sets_external_version`; `test_issue_text_includes_comments`; `test_pr_text_includes_conversation_reviews_review_comments_and_files`; `test_external_version_not_written_on_failure`; `test_404_deletes_document_and_acks`; `test_rename_updates_title_and_url_when_content_unchanged`; `test_disconnected_connection_skips`; `test_401_refreshes_once_then_succeeds`; `test_dead_letter_records_failure` (via `on_dead_letter`, both main.py path helper and local_queue path).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** worker suite — PASS.
- [ ] **Step 5: Commit** `feat(worker): GitHub file/issue/PR handlers, dead-letter failure record`.

### Task 7: Frontend — GitHub card, connect purpose, labels

**Files:**
- Modify: `services/frontend/lib/connect.ts` (`ConnectPurpose` adds `"github_repos"`), `services/frontend/lib/api.ts` (`syncProvider("github")`, connection type fields already present), `services/frontend/app/connections/page.tsx` (render GitHub card), `services/frontend/components/connections/ConnectionCard.tsx` (GitHub variant), knowledge-base source label/icon mapping, chat citation source label.
- API addition (this task): `GET /connections` for the GitHub row adds `github: {"repo_count": int, "repo_names": list[str], "choose_url": str}` (from `github_repos` rows; `choose_url` = `https://github.com/apps/{connection.bot_id}/installations/new`). Test: `services/api/tests/test_github_connect.py::test_connections_reports_github_repos`.

**Interfaces:**
- Consumes: `POST /connect/github_repos/start` (desktop) or `/oauth/github_source/authorize` (web, `<a href>` navigation like Jira); `POST /sync/github`; disconnect endpoints; `needs_reconnect`.
- Produces (UI per spec §6): not connected → **Connect GitHub**; waiting state reused; connected → login, "N repositories" with names in a `title` tooltip, last synced, document count, Sync now, **Choose repositories on GitHub** (`choose_url`, new tab), Disconnect; empty list → "No repositories yet" + "After choosing them on GitHub, come back here and press Sync now." + the link; Disconnect dialog extra line "You can also uninstall the app on GitHub" linking `choose_url`; needs-reconnect → **Reconnect GitHub**.

- [ ] **Step 1: Write the failing api test** above; run — FAIL; implement; PASS.
- [ ] **Step 2: Implement the UI** following `ConnectionCard.tsx`'s existing Jira variant and styling tokens.
- [ ] **Step 3: Run** `npx tsc --noEmit` — PASS.
- [ ] **Step 4: Commit** `feat(frontend): GitHub connection card`.

### Task 8: End-to-end path G (fake broker + fake GitHub)

**Files:**
- Modify: `desktop/e2e/fake_broker.py` (`github_repos` canned result: `user_id: 4242`, `login: "e2e-octo"`, `app_slug: "e2e-app"`, `installations: [{"id": 99, "account_login": "e2e-octo", "account_type": "User"}]`; `/refresh/github_repos`)
- Create: `desktop/e2e/fake_github.py` (stdlib HTTP server: `/user/installations`, `/user/installations/99/repositories` → one repo `e2e-octo/notes` id 7 default branch `main`; `/repos/e2e-octo/notes/git/trees/main?recursive=1` → `README.md` blob; `/git/blobs/{sha}` → base64 "# Notes\nThe launch codename is BLUEBIRD."; `/repos/e2e-octo/notes/issues` → issue #1 "Ship it" with one comment; `/issues/1/comments`)
- Modify: `desktop/e2e/run_setup_e2e.py` (new `path_g()`, called from `main()`); the launcher passes `GITHUB_API_URL` through to both children (add it to `desktop/launcher.py`'s `child_env` alongside `AUTH_BROKER_URL`).

- [ ] **Step 1: Write `path_g`:** fresh home, fake Ollama + fake broker + fake GitHub, launcher with `AUTH_BROKER_URL` and `GITHUB_API_URL`; complete setup; Connections → **Connect GitHub** → popup heading "GitHub is connected." → card shows "1 repositories"/"1 repository" text containing `1 repositor` → **Sync now** → Knowledge base lists `e2e-octo/notes · README.md` and `e2e-octo/notes #1: Ship it` within 60 s → Disconnect (remove) → card back to **Connect GitHub**.
- [ ] **Step 2: Rebuild the bundle** (`/home/claude/dvenv/bin/python desktop/build_backend.py`, with the temporary `next/font/google` stub in `services/frontend/app/layout.tsx` restored exactly afterwards) and run `cd desktop/e2e && CHROMIUM=/opt/pw-browsers/chromium /home/claude/dvenv/bin/python -c "import run_setup_e2e as e; e.path_d(); e.path_d_off(); e.path_g()"` — PASS, no leftover processes.
- [ ] **Step 3: Commit** `test(desktop): e2e connect, sync and disconnect GitHub`.
- [ ] **Step 4: Update `CLAUDE.md`** ("RAG agent specifics": GitHub source, 015, needs_reconnect column; "Config": new settings) and **commit** `docs: GitHub source in CLAUDE.md`.
