# Dynamic RAG Knowledge Agent

An event-driven RAG agent that connects via OAuth to live data sources — Notion and Jira — rather than static files, with short-term and long-term memory, multi-step retrieval, and response citation tracing.

**Status: built.** OAuth → sync → embed → retrieve → streaming answer, for both sources, with a full dashboard.

Pipeline: webhook/sync event → queue → worker → LLM call → structured output → API → dashboard.

## Architecture

```
                     ┌─────────────┐
  Notion/Jira  ─────►│             │
  (OAuth sync)       │   FastAPI   │◄──── Next.js Dashboard
                      │   (api)     │      Chat · Connections
                      │             │      Knowledge Base · Activity
                      └──────┬──────┘
                             │ publish (+ event_log row)
                             ▼
                      ┌─────────────┐
                      │  RabbitMQ   │
                      └──────┬──────┘
                             │ consume
                             ▼
                      ┌─────────────┐        ┌──────────────┐
                      │   Worker    │───────►│  Postgres +  │
                      │ (consumer)  │        │   pgvector   │
                      └──────┬──────┘        └──────────────┘
                             │
                             ▼
                      ┌─────────────┐
                      │    Redis    │  (idempotency, session memory, rate limits)
                      └─────────────┘
  Voyage AI (embeddings) ◄──── Worker (ingest) & API (query)
  Gemini (Google AI Studio) ◄── API (streaming answer generation)
  All services emit traces/metrics ─────► OTel Collector ─────► Prometheus / Grafana
```

## Services

| Service | Tech | Responsibility |
|---|---|---|
| `services/api` | FastAPI | HTTP entrypoint: OAuth callbacks, sync triggers, streaming query endpoint, dashboard read APIs, publishes events to queue |
| `services/worker` | Python (aio-pika consumer) | Pulls events off the queue, runs ingestion logic (fetch → chunk → embed), writes results |
| `services/frontend` | Next.js | Dashboard — Chat, Connections, Knowledge Base, Activity |
| Postgres (+pgvector) | Datastore | Relational data + vector embeddings |
| Redis | Cache/session | Idempotency keys, rate-limit counters, short-term chat memory + session index |
| RabbitMQ | Queue | Durable event bus between API and worker, with dead-letter queue |
| Voyage AI | Embeddings | `voyage-3-lite` for both document ingestion and query embedding |
| Gemini (Google AI Studio) | LLM | Streams grounded, cited answers over retrieved chunks |
| OTel Collector + Prometheus + Grafana | Observability | Distributed tracing, metrics, dashboards |

## How it works

Four pieces:

- **Connect** — OAuth to Notion and/or Jira (`/oauth/{provider}/authorize`), scoped to your organization: one active connection per provider per org.
- **Sync** — a manual "sync now" trigger (`/sync/notion`, `/sync/jira`) enumerates content and publishes one event per page/issue onto the queue. Notion has no content-change webhooks, so both providers use polling-on-demand to stay symmetric.
- **Ingest** — the worker fetches each page/issue, chunks it (paragraph-aware, heading/comment-aware), embeds each chunk via Voyage AI, and upserts it into pgvector. Unchanged content is skipped on re-sync (content-hash check) rather than re-embedded.
- **Query** — the chat screen streams a grounded answer: embed the question → cosine-similarity search over chunks → stream the answer from Gemini with inline `[n]` citations → resolve which retrieved sources were actually cited vs. just searched.

## Dashboard screens (http://localhost:3000)

- **Chat** — ask questions, watch the answer stream in with clickable citation markers, see a sources panel split into "cited" vs. "also searched, not cited," and revisit past conversations from the sidebar.
- **Connections** — per-provider card showing connected state, workspace name, last-synced time, a "Sync now" button, and a "Connect" button that kicks off OAuth for disconnected providers.
- **Knowledge Base** — searchable/filterable table of every ingested document; click one to see its chunks (useful for debugging why something did or didn't retrieve).
- **Activity** — a live (5s-polled) feed of the `event_log` table: every sync/embed event and its status (received → processing → succeeded/failed/dead_lettered), with retry backoff visible when something fails.

## End-to-end workflow

1. Fill in `.env` with real credentials (see [Getting API credentials](#getting-api-credentials) below), then `docker compose up --build`.
2. Open the dashboard at http://localhost:3000 → Sign up (email+password, or Continue with Google/GitHub if those are configured — both create your organization and log you in as its owner) → Connections → click Connect under Notion or Jira → complete the provider's OAuth consent screen → you're redirected back with a success banner.
3. Click **Sync now**. This enumerates content and publishes one event per page/issue — check Activity to watch each one move from `received` to `succeeded` in near-real-time as the worker processes them.
4. Check **Knowledge Base** — every synced document now has a title, source, last-synced time, and chunk count. Click one to see exactly what got chunked and embedded.
5. Go to **Chat** and ask a question about the synced content. The answer streams token-by-token with `[n]` citation markers; the Sources panel on the right shows which retrieved chunks were cited and which were retrieved but not used — proof retrieval actually ran even when the model didn't cite everything.
6. Ask a follow-up in the same conversation (multi-turn works via Redis-backed short-term memory), or start a New chat. Past conversations are listed in the sidebar and can be reopened (text only — retrieval metadata isn't persisted for historical turns).

## Why this design

- **Queue decoupling**: sync bursts (a full Notion re-index) don't block the API or get dropped — they're durably queued and processed at the worker's pace. The query endpoint, by contrast, is synchronous/streamed directly — it's a request/response interaction the caller is blocked on, not a fire-and-forget ingestion event.
- **Idempotency**: every event carries a dedupe key (Notion page ID + version, or Jira issue ID + updated timestamp) so re-delivery doesn't duplicate work. On a processing failure, the dedupe claim is explicitly released so a retry gets a genuine second attempt rather than being silently skipped as a false duplicate.
- **Dead-letter queue with real bounded retries**: failed jobs retry up to `MAX_RETRIES` times with exponential backoff (via republish-with-incremented-header, since AMQP's native requeue can't modify headers), then land in a DLQ for inspection/manual replay — never silently dropped, never retried forever.
- **`event_log` is populated end-to-end**: every published event gets a row that the worker updates through its lifecycle, which is what powers the Activity dashboard — this is real instrumentation, not a placeholder.
- **Streaming answers with citation reconciliation**: the query endpoint emits retrieved sources before calling the LLM, so the UI can show what was searched immediately; citations are reconciled against the final answer text once streaming completes, which is what makes "searched but not cited" visible rather than just claimed.
- **Defense in depth on cost/abuse**: per-IP rate limiting on `/query` and `/sync/*` (both cost real money or fan out many external calls), an optional shared-secret header (off by default for local dev), and a system prompt that explicitly frames retrieved content as untrusted data rather than instructions.

## Getting API credentials

| Credential | Where to get it |
|---|---|
| `VOYAGE_API_KEY` | voyageai.com → dashboard → API keys |
| `GOOGLE_API_KEY` | aistudio.google.com/apikey |
| `NOTION_CLIENT_ID` / `NOTION_CLIENT_SECRET` | notion.so/my-integrations → New integration → enable public distribution (required for the OAuth flow) → set the redirect URI to `http://localhost:8000/oauth/notion/callback` |
| `JIRA_CLIENT_ID` / `JIRA_CLIENT_SECRET` | developer.atlassian.com/console/myapps → Create app → add the OAuth 2.0 (3LO) feature → callback URL `http://localhost:8000/oauth/jira/callback` → scopes `read:jira-work`, `read:jira-user`, `offline_access` |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | console.cloud.google.com/apis/credentials → Create OAuth client ID (Web application) → authorized redirect URI `http://localhost:8000/auth/google/callback`. Not `GOOGLE_API_KEY` above — that's the unrelated Gemini key. |
| `GITHUB_OAUTH_CLIENT_ID` / `GITHUB_OAUTH_CLIENT_SECRET` | github.com/settings/developers → New OAuth App → callback URL `http://localhost:8000/auth/github/callback`. Used for "Continue with GitHub" login only. |

## Running locally

```
cp .env.example .env
# fill in the credentials above
docker compose up --build
```

- Dashboard: http://localhost:3000
- API + Swagger docs: http://localhost:8000/docs
- RabbitMQ management UI: http://localhost:15672 (guest/guest)
- Grafana: http://localhost:3001 (admin/admin)
- Prometheus: http://localhost:9090

The agent works with partial credentials too — e.g. Voyage + Google keys alone let `/query` run (with an empty-results fallback until something's synced), and Notion or Jira alone is enough to exercise the full connect → sync → ingest → query loop without both.

**Note on schema changes**: `infra/postgres/*.sql` files only run against an empty Postgres data volume. If you edit one after already running `docker compose up` once, either remove the `pgdata` volume (destructive — confirm you don't need the existing data first) or apply the new file manually: `docker exec -i backbone-postgres psql -U backbone -d backbone < infra/postgres/00X_whatever.sql`.

## Testing it works

No automated test suite exists yet — verification is manual, via the dashboard or the API directly:

- **Smoke test without real OAuth**: `POST /events/publish` (Swagger UI at `/docs`) with a fabricated `rag.notion_page_updated` payload, then watch Activity — you should see the row move through `received` → `processing` and end at `succeeded` or `failed` depending on whether the payload references a real connection.
- **Full loop**: follow the end-to-end workflow above with real Notion/Jira credentials.
- **Retry/DLQ path**: publish an event with a payload that will fail processing (e.g. missing a required field) — Activity should show it retry a few times with visible backoff, then land at `dead_lettered`.
- **Rate limiting**: fire more than `RATE_LIMIT_QUERY_PER_MINUTE` requests at `/query` inside a minute — you should get a 429.

## Known limitations (deliberate, not oversights)

- OAuth tokens are stored in plaintext in Postgres — fine for local use, not for production.
- The app is multi-tenant (organizations + JWT-based login, `POST /auth/signup` / `/auth/login`), but in-org access control is connection-level, not per-document — an org admin can mark a whole Notion/Jira connection "restricted" and pick which members see it, but can't restrict individual pages/issues. Notion's public API has no per-page ACL endpoint at all, and Jira's real per-issue permissions need OAuth scopes most integrations won't get, so neither is mirrored.
- JWT sessions can't be revoked — `POST /auth/logout` only clears the client-side cookie; a stolen token stays valid for its 24h life.
- No Jira webhook receiver yet — both providers use manual "sync now" polling to stay symmetric (Notion has no webhook option regardless).
- Trace IDs in `event_log` reflect the API's publish-time trace only — there's no W3C trace-context propagation across the RabbitMQ header yet, so it doesn't (yet) connect to the worker's own processing trace in Grafana.
- Revisiting a past chat session replays the text only — per-turn sources/citations aren't persisted for historical turns, only the currently-streaming one (Redis short-term memory by design, not a new database table).
