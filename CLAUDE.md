# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Reusable event-driven infrastructure meant to power two *separate* downstream projects:

1. **RAG Knowledge Agent** — OAuth-synced Notion/Jira retrieval agent with memory + citation tracing. **Implemented** (first vertical slice): OAuth → sync → embed → retrieve → answer, for both sources.
2. **AI Code Review Bot** — GitHub webhook-triggered style/security analysis. **Not implemented yet** — `services/worker/app/tasks/__init__.py`'s `HANDLERS` only has the RAG agent's two entries, and no `github_webhook`/`notion_oauth`-style router exists for it in `services/api/app/main.py`.

Both projects share the same pipeline: `webhook/sync event -> queue -> worker -> LLM call -> structured output -> API -> dashboard`. Only the event source and the worker's task logic differ between the two projects — the queue wiring, idempotency, observability, and dashboard shell are shared plumbing.

## Commands

There is no test suite, linter config, or CI in this repo yet.

```bash
# Run the whole stack (Postgres+pgvector, Redis, RabbitMQ, api, worker, frontend, OTel collector, Prometheus, Grafana)
cp .env.example .env
docker compose up --build

# Run a single service's container in isolation (still needs its deps up)
docker compose up --build api
docker compose up --build worker

# Frontend, run outside Docker
cd services/frontend && npm install && npm run dev    # http://localhost:3000
cd services/frontend && npm run lint

# API/worker, run outside Docker (point DATABASE_URL/REDIS_URL/RABBITMQ_URL at localhost via .env)
cd services/api && pip install -r requirements.txt && uvicorn app.main:app --reload
cd services/worker && pip install -r requirements.txt && python -m app.main
```

Local endpoints once `docker compose up` is running:
- API + Swagger docs: http://localhost:8000/docs
- Frontend: http://localhost:3000
- RabbitMQ management UI: http://localhost:15672 (guest/guest)
- Grafana: http://localhost:3001 (admin/admin)
- Prometheus: http://localhost:9090

To use the RAG agent, `.env` needs real `NOTION_CLIENT_ID`/`SECRET`, `JIRA_CLIENT_ID`/`SECRET`, `GOOGLE_API_KEY` (Google AI Studio, used for query generation via Gemini), and `VOYAGE_API_KEY` — then use the frontend at http://localhost:3000 (Connections screen to connect + sync, Chat screen to ask), or hit the API directly: `/oauth/notion/authorize` and `/oauth/jira/authorize` in a browser, `POST /sync/notion` / `/sync/jira` to ingest, `POST /query {"question": "..."}` (SSE response) to ask.

**`infra/postgres/*.sql` files run in filename-sort order** (digits sort before letters, so `001_init.sql` must stay numbered lower than any file that depends on its extensions/tables — `002_rag_schema.sql` needs `vector`/`uuid-ossp` from `001_init.sql`). `docker-entrypoint-initdb.d` only runs against an *empty* Postgres data volume — after adding/editing a `.sql` file here, an existing local `pgdata` volume needs `docker compose down -v` (destructive — confirm before running) or a manual `psql -f infra/postgres/00X_whatever.sql` against the running container to pick up the change.

## Architecture

```
Notion/Jira (OAuth sync) ─┐
                           ├─► FastAPI (api) ◄──── Next.js Dashboard
GitHub Webhook ────────────┘        │
                                     │ publish
                                     ▼
                               RabbitMQ (topic exchange)
                                     │ consume
                                     ▼
                                  Worker  ───────► Postgres + pgvector
                                     │
                                     ▼
                                  Redis (idempotency / short-term memory)

All services emit traces/metrics ──► OTel Collector ──► Prometheus / Grafana
```

- `services/api` (FastAPI) — HTTP entrypoint: webhook receivers, OAuth callbacks, query endpoints. Never talks to Postgres/business logic directly for event processing — it only validates and calls `publish_event()` (`app/core/queue.py`) to hand off onto the queue.
- `services/worker` (aio-pika consumer, `app/main.py`) — the only thing that does domain logic. Binds a single queue to both `rag.#` and `codereview.#` routing-key patterns (one consumer serves both projects; split into separate queues/workers later if one workload needs independent scaling). Dispatches by exact routing key via `app/tasks/__init__.py`'s `HANDLERS` dict — add new project logic by writing a task module and registering it there.
- `services/frontend` (Next.js 14, App Router) — dashboard for both projects; the RAG agent's 4 screens (chat, connections, knowledge base, activity) are built — see the Frontend section below.

### Event contract

Everything flows through `backbone.events`, a durable topic exchange declared identically in both `services/api/app/core/queue.py` and `services/worker/app/main.py`. Routing keys are `<project>.<event_type>`, e.g. `rag.notion_page_updated`, `codereview.pull_request_opened`. When adding a new event type: pick a routing key matching one of the two project prefixes, publish via `publish_event(routing_key, payload, dedupe_key)`, and register a handler in the worker's `HANDLERS` dict — no other wiring needed, the topic binding (`rag.#` / `codereview.#`) already covers it.

Every published message carries a `dedupe_key` (e.g. commit SHA + file path, or Notion page ID + version) as a header. The worker checks it against Redis (`already_processed()` in `app/core/idempotency.py`, SETNX with a 24h TTL) before dispatching, so retried webhook deliveries or re-triggered syncs are safe to process twice at the transport layer — real work happens at most once per dedupe key within the TTL window.

Failures: on a handler exception, the worker acks the failed message and manually republishes a copy with `x-retry-count` incremented (`services/worker/app/main.py`'s `_republish_with_retry`) after an exponential backoff (`min(2**retry_count, 30)`s). **Not `reject(requeue=True)`** — AMQP's native requeue redelivers the message unchanged with no way to bump a header on it, so that would retry forever without ever reaching `max_retries`. Once `retry_count >= settings.max_retries` (default 3), the message is `reject(requeue=False)`'d instead, which the queue's `x-dead-letter-exchange` argument routes to `backbone.events.dlq`. A handler exception does not lose the event — it always ends up ack'd+republished, or dead-lettered, never silently dropped.

**`event_log` is populated end-to-end**: `publish_event()` (`services/api/app/core/queue.py`) inserts a `received` row (committed before the RabbitMQ publish, so it's durably visible before the message could possibly be consumed — see the RAG agent section for what happens if the publish itself then fails) and threads the row's id through as an `event_log_id` message header. The worker (`services/worker/app/main.py`'s `handle_message`) reads that header and moves the row through `processing` → `succeeded`/`failed`/`dead_lettered` via `services/worker/app/core/event_log.py`. The header is optional/defensive — anything published without it just no-ops those calls.

**Dedupe-key/retry interaction (subtle, worth knowing before touching either):** `already_processed()` (`services/worker/app/core/idempotency.py`) claims a dedupe key via Redis `SETNX` *before* processing succeeds. On failure, `handle_message` calls the new `clear_dedupe()` to release that claim — **without this, a retried message gets skipped as a false "duplicate" and reported `succeeded` even though it never actually ran**, silently masking the original failure. Any new failure path added to `handle_message` needs to call `clear_dedupe()` too, or it'll reintroduce this masking bug.

`infra/postgres/001_init.sql` defines the shared `event_log` table (one row per event, for the dashboard's activity feed and debugging/replay) plus the `vector` and `uuid-ossp` extensions. `infra/postgres/002_rag_schema.sql` adds the RAG agent's tables (see below). New project-specific tables get added as new numbered `.sql` files in this directory, not by editing the shared init script — see the filename-ordering caveat above.

### RAG agent specifics

- **Schema** (`infra/postgres/002_rag_schema.sql` + `003_orgs_and_access.sql`): `oauth_connections` (one row per org+provider, `UNIQUE(org_id, provider)` — reconnecting overwrites that org's connection), `documents` (one row per synced Notion page / Jira issue, `UNIQUE(connection_id, source, external_id)` — scoped by connection rather than a separate `org_id` column, since a connection belongs to exactly one org), `chunks` (embedded content, `vector(512)` for `voyage-3-lite` — **changing the embedding model means a migration + full re-embed**, the dimension is baked into the column). `003_orgs_and_access.sql` adds `organizations`/`users`/`org_members`/`org_invites`/`service_tokens`/`connection_members` and org-scopes `oauth_connections`/`documents`/`event_log` — see "Multi-tenancy and access control" below. Both `services/api/app/models/rag.py` and `services/worker/app/models/rag.py` declare the same SQLAlchemy models against these tables (duplicated rather than shared — the two services have independent `requirements.txt` and no shared package exists in this repo, same reasoning as `app/core/db.py` below).
- **`services/worker/app/core/db.py`** — worker-side counterpart to the api's `app/core/db.py`, duplicated for the same reason. Exposes an async context manager (`async with get_session() as session:`) instead of a FastAPI dependency, since the worker isn't a FastAPI app.
- **Ingestion flow**: `POST /sync/notion` or `/sync/jira` (`services/api/app/api/routes/sync.py`) enumerate content and `publish_event()` one `rag.notion_page_updated` / `rag.jira_issue_updated` per item (capped by `max_pages_per_sync`/`max_issues_per_sync`) — no embedding work happens in the API process. The worker's `services/worker/app/tasks/embed.py` does the actual fetch → chunk (`app/tasks/chunking.py`) → embed (`app/integrations/voyage.py`) → upsert, skipping re-embedding when `documents.content_hash` is unchanged. `publish_event()` commits its `event_log` row before publishing (see the Event contract section above) — if the publish itself then fails, it updates that same row to `status="failed"` rather than leaving it orphaned at `received` forever.
- **Streaming `/query`**: `services/api/app/api/routes/query.py` returns `StreamingResponse(media_type="text/event-stream")`, not a single JSON response. Answer generation uses **Gemini** (`google-genai` SDK, `genai.Client(api_key=settings.google_api_key)`, model = `settings.query_model` = `gemini-2.5-flash` by default), not Claude — this repo's own dashboard is a Claude Code project, but the RAG agent's LLM call is provider-agnostic and currently wired to whatever key the user has (a Google AI Studio key in this case). Event sequence: `sources` (all top-k retrieved chunks, right after retrieval, before the LLM call) → repeated `delta` (answer text as Gemini streams via `client.aio.models.generate_content_stream()`) → `done` (`cited_indices` computed by scanning the full answer for `[n]` markers, plus `session_id`) → or `error` on failure. The `sources`-before-`delta` ordering is what makes "searched but not cited" work client-side — the frontend has every retrieved chunk from the first event and reconciles which were actually cited from `done`. Retry (`tenacity`, predicate-based on `genai.errors.APIError.code == 429`) wraps only opening the stream, not the `async for` loop — once a `delta` may already be on the wire there's no safe way to silently retry, so a mid-stream failure surfaces as an `error` event instead. Conversation history stored in Redis keeps `role: "assistant"` internally; `_to_gemini_contents()` translates that to Gemini's `role: "model"` only at the API-call boundary. The system prompt explicitly tells the model to treat retrieved chunks as untrusted data (not instructions) and to respond in plain prose (no markdown) — the frontend renders `[n]` citation markers via a plain regex split, which only holds if answers are actually plain text.
- **Session listing**: `services/api/app/core/session_store.py` extends the existing Redis short-term memory (`rag:session:{id}:history`) with a sorted-set index (`rag:sessions:index`, scored by last-active time) and a preview key, so `GET /sessions`/`GET /sessions/{id}` (`services/api/app/api/routes/sessions.py`) can list/replay conversations without a new persistence layer. Stale index entries (history TTL'd out) are pruned lazily on read, not via a cleanup job. Revisiting a past session only replays `{role, content}` text — per-turn sources/citations were never persisted for historical turns, only the currently-streaming one.
- **Jira token refresh**: Jira access tokens expire (~1h). Both `services/worker/app/integrations/jira.py` and `services/api/app/integrations/jira_auth.py` independently refresh via the stored `refresh_token` before each call (duplicated for the same reason as `db.py` — the api's `/sync/jira` calls Jira directly to enumerate issues, while the worker's `JiraClient` calls it to fetch issue content). Notion tokens don't expire, so no equivalent exists for Notion.
- **Rate limiting / retries**: every external call (Notion, Jira, Voyage, Gemini) is wrapped in a `tenacity` retry with exponential backoff, honoring `Retry-After` on 429s where the API sends one (Notion, Jira, Voyage; Gemini's retry is predicate-based on `APIError.code`, no `Retry-After` header). `services/api/app/core/ratelimit.py`'s `rate_limiter(route, per_minute)` is a Redis-backed per-IP fixed-window limiter applied to `/query` and `/sync/*` — this guards against runaway cost (`/query` spends real Gemini + Voyage money per call), not a security boundary.
- **Auth**: `services/api/app/core/auth.py`'s `require_auth` dependency resolves `(user_id, org_id, role)` from a JWT in the httpOnly `session` cookie set by `POST /auth/login` (see "Multi-tenancy and access control" below) — the old shared `X-API-Key`/`API_SHARED_SECRET`/`require_api_key` mechanism has been removed entirely. `/sync/*` additionally accepts `require_auth_or_service_token`, which falls back to an org-scoped `service_tokens` bearer token for non-interactive automation. `/oauth/*/callback` stays intentionally unguarded (hit by the provider's redirect, not a direct caller) — org attribution for it flows through `/authorize`'s CSRF state instead, see below.
- **Prompt injection**: `services/api/app/api/routes/query.py`'s system prompt explicitly frames retrieved chunks as untrusted data, not instructions — retrieved Notion/Jira content sits in the same context window as the system prompt, so this framing matters.
- **Not built yet** (deliberate follow-ups, not oversights): a Jira webhook receiver (Jira supports one; Notion doesn't, so both currently use manual "sync now" polling to keep the two providers symmetric), OAuth token encryption at rest (plaintext in Postgres), true W3C trace-context propagation across the RabbitMQ header (an `event_log` row's `trace_id` is the API's publish-time trace only, not the worker's later processing trace — they're currently two disconnected traces), JWT revocation (logout only clears the client-side cookie; a stolen token stays valid for its 24h life), invite emails (`POST /auth/invite` returns the token directly rather than sending mail).

### Multi-tenancy and access control

Each organization gets its own isolated knowledge base; within an org, connection-level (not per-document) access control is admin-curated rather than mirrored from Notion/Jira, since Notion's public API exposes no per-page ACL and Jira's real per-issue permissions need admin-tier OAuth scopes most integrations won't get.

- **Schema** (`infra/postgres/003_orgs_and_access.sql`): `organizations`, `users`, `org_members` (role: `owner`/`admin`/`member`), `org_invites` (pending invites — `org_members.user_id` is `NOT NULL`, so an invite to someone without an account yet needs its own row), `service_tokens` (org-scoped machine credential, sha256-hashed), `connection_members` (allow-list used only when a connection's `visibility_mode` is `'restricted'`; default is `'org_wide'` — visible to every org member).
- **Auth flow**: `services/api/app/api/routes/auth.py` — `POST /auth/signup` (creates an org as `owner`, or redeems an `invite_token`), `POST /auth/login` (rate-limited by submitted email via `check_rate_limit` in `app/core/ratelimit.py`), `POST /auth/logout`, `GET /auth/me`, `POST /auth/select-org` (a user in multiple orgs re-issues a token scoped to a different org — no live per-request org switching), `POST/GET/DELETE /auth/service-tokens` (owner/admin only, plaintext shown once at creation).
- **Social login** (`infra/postgres/004_social_login.sql`, `services/api/app/api/routes/social_auth.py`): `GET /auth/google/authorize`+`/callback` and `GET /auth/github/authorize`+`/callback` are an alternative to email+password — **not** the same thing as `notion_oauth.py`/`jira_oauth.py`, which connect Notion/Jira as data sources for an org you're already logged into. Social `/authorize` has no `require_auth` precondition (there's no session yet); it reuses the same Redis CSRF-state pattern as Notion/Jira's `/authorize`, but the state payload carries an optional `invite_token` instead of `org_id`, since there's no org to attribute to yet. The callback links-or-creates a `users` row (matched to an existing account by email if one exists, else a brand-new user+org as `owner`, mirroring `/auth/signup`'s two paths) via a new `oauth_identities` table (`UNIQUE(provider, provider_user_id)`), then issues the same session cookie `/auth/login` does — `users.password_hash` is nullable for social-only accounts. New settings `GOOGLE_OAUTH_CLIENT_ID`/`SECRET`/`REDIRECT_URI` and `GITHUB_OAUTH_CLIENT_ID`/`SECRET`/`REDIRECT_URI` are deliberately separate from `GOOGLE_API_KEY` (Gemini) and `GITHUB_APP_ID` (the not-yet-built code review bot's GitHub App) — don't conflate any of these.
- **Session cookie**: `SameSite=None; Secure; HttpOnly`, not `Lax` — the frontend (`localhost:3000`) and API (`localhost:8000`) are different origins even in local dev, and `Lax` cookies aren't sent on cross-site `fetch`/XHR. `Secure` works over plain `http://localhost` since browsers treat it as a secure context; this needs revisiting (unify origins behind a reverse proxy, or real TLS) before any deployment reachable at a non-localhost domain. No separate CSRF token is used — justified by every mutating endpoint being JSON-body-only behind an explicit `cors_origins` allow-list, which blocks the cross-site `fetch` a CSRF attack would need.
- **OAuth org attribution**: `/oauth/{provider}/authorize` requires `require_auth` (it's hit directly by the frontend, unlike `/callback`) and stores the caller's `org_id` — not just a CSRF marker — as the Redis `oauth_state:{provider}:{state}` value. `/callback` reads `org_id` back out of that key before deleting it; if the key's expired (`oauth_state_ttl_seconds`), org attribution and CSRF validity are lost together and it 400s, same as a CSRF failure would.
- **Retrieval + knowledge-base-browser filtering**: `query.py`'s retrieval `SELECT` and `documents.py`'s list/detail endpoints apply the *same* filter — join to `oauth_connections`, require `org_id` match, and (for `member` role only; `owner`/`admin` bypass) require `visibility_mode == 'org_wide' OR connection_id ∈ connection_members for this user`. This has to be enforced in both places or a restricted connection's content stays readable through the knowledge-base browser even when `/query` correctly hides it. The filter runs before `ORDER BY ... LIMIT`, not as a post-fetch step, so a restricted member gets the true top-k among visible chunks rather than a truncated one.
- **Cross-org collision fix**: `documents`'s uniqueness and the worker's upsert lookup (`services/worker/app/tasks/embed.py`) are scoped by `connection_id`, and event `dedupe_key`s embed `connection_id` too — Jira issue IDs are small ints local to one site, so two orgs' sites can collide on `external_id`/`issue_id` without this scoping, silently merging one org's content into another's or dropping one org's sync event as a false duplicate of another's.

### Frontend (`services/frontend`)

Next.js 14 App Router, Tailwind (wired up in this pass — `tailwind.config.js`/`postcss.config.js`/`app/globals.css`, previously listed in `package.json` but unconfigured), 6 screens under `app/`: `/` (chat), `/connections`, `/knowledge-base`, `/activity`, `/login`, `/signup`. Shared nav in `components/Nav.tsx` via `app/layout.tsx`; `lib/auth-context.tsx`'s `AuthProvider` (wrapping `<Nav>`+children in `layout.tsx`) calls `GET /auth/me` on mount and redirects to `/login` when unauthenticated — the four main screens only render nav links once a user is loaded.

- `lib/api.ts` — typed fetch helpers against `NEXT_PUBLIC_API_URL`. No API key header anymore: every call sends `credentials: "include"` so the httpOnly `session` cookie (set by `POST /auth/login`) rides along automatically — replaces the old static build-time `NEXT_PUBLIC_API_KEY`, which was identical for every visitor since it was baked into the JS bundle at build time.
- `lib/sse.ts` — hand-rolled SSE client for `/query` (`streamQuery()`). Uses a single `TextDecoder("utf-8")` with `{stream: true}` reused across all chunks, not a fresh decoder per chunk — a raw byte chunk boundary can land mid multi-byte UTF-8 character, and a fresh decoder per chunk would corrupt it. `EventSource` isn't used since it can't send the POST body `/query` needs.
- Chat page (`app/page.tsx` + `components/chat/`) holds retrieval `sources` and `cited_indices` only for the turn currently being streamed (component state, not persisted) — historical turns loaded from `GET /sessions/{id}` render citation markers but no sources panel, per the session-listing limitation noted above.
- Polling intervals, chosen per what each feed actually needs: session sidebar 15s, connections page 3s-for-30s right after a manual sync (since sync only enqueues — the timestamp won't move until the worker processes events), activity feed 5s (tightest — this is the explicit "live feed" requirement). No websocket/SSE push infra for these; plain `setInterval` via `hooks/useInterval.ts`.
- OAuth "Connect" is a real `<a href>` browser navigation to `{API_URL}/oauth/{provider}/authorize`, not a `fetch` — needs an actual redirect chain through the provider's consent screen. The callback routes (`notion_oauth.py`/`jira_oauth.py`) redirect back to `{FRONTEND_URL}/connections?connected={provider}` on success (changed from returning raw JSON, specifically to make this flow land somewhere sensible).

### Observability

`api` and `worker` each call an identical `setup_telemetry()` (`app/core/telemetry.py` in each service) that configures an OTel `TracerProvider` exporting to the collector at `OTEL_EXPORTER_OTLP_ENDPOINT`. The worker starts a `process_event` span per message tagged with `routing_key`/`dedupe_key`; combined with FastAPI auto-instrumentation on the API side, one GitHub PR or Notion sync is traceable end-to-end (API receipt → queue publish → worker consume → DB write) through a single trace ID in Grafana. When adding spans in new task/handler code, follow this same pattern rather than introducing a separate tracer setup.

### Config

Both `api` and `worker` use near-identical `pydantic-settings` `Settings` classes (`app/core/config.py`) reading from `.env` — `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, the RAG agent's `GOOGLE_API_KEY`/`VOYAGE_API_KEY`/`NOTION_*`/`JIRA_*`, etc. `.env.example` documents every var. `GITHUB_APP_ID`/`GITHUB_WEBHOOK_SECRET` are still unused placeholders for the not-yet-started code review bot. (`anthropic==0.36.0` is still pinned in `services/worker/requirements.txt` from the original scaffold — unused there, pre-dates this session's work, left alone.)

## Where project-specific work plugs in

- **RAG agent** (implemented): OAuth routes (`services/api/app/api/routes/{notion_oauth,jira_oauth}.py`), sync routes (`.../sync.py`), query route (`.../query.py`), worker embed task (`services/worker/app/tasks/embed.py`) — see "RAG agent specifics" above for the cross-file patterns.
- **Code review bot** (not started): `services/worker/app/tasks/analyze_diff.py`, GitHub App auth + webhook signature verification, a findings schema/table (as `infra/postgres/003_*.sql`, following the RAG agent's numbered-migration pattern).

New API routers get included in `services/api/app/main.py`; new worker task handlers get registered in `services/worker/app/tasks/__init__.py`'s `HANDLERS` dict (there's a comment there showing where the code review bot's entries go).
