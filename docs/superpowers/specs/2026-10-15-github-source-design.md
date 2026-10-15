# GitHub as a knowledge source

Date: 2026-10-15 · Branch: `desktop-app` · Builds on `2026-10-15-desktop-online-accounts-design.md` (sign-in Worker,
desktop Connect flow) and the Disconnect work (`014_connection_disconnect.sql`).
Revision 3, after two rounds of independent review (install/state flow, sync cursors and caps, refresh races, deletion rules).

## Goal

Connect GitHub like Notion and Jira, so answers can draw on a person's repositories: Markdown docs, issues and pull
requests, from repositories they choose, private ones included. Desktop and web.

This is separate from **Continue with GitHub** (app sign-in, `github` identity purpose), which stays as it is.

## Decisions

| Question | Decision |
| --- | --- |
| What is synced | Markdown files; issues with comments; pull requests with conversation, reviews, review comments and changed-file names (no diff) |
| Access model | A read-only GitHub App, installed on the repositories the person picks |
| Connect flow | The GitHub App's **OAuth authorize** URL (standard `state`), then the app lists the person's installations; installing on repos is a separate step on GitHub |
| Token | GitHub App **user-to-server** token (8 h) + rotating refresh token, refreshed through the sign-in Worker on desktop |
| Scope of one connection | One GitHub connection per workspace = one GitHub user; it covers **every installation** that user can access (personal and orgs) |
| Repo selection | On GitHub (`https://github.com/apps/{slug}/installations/new`: pick account, pick repos); picked up on the next sync |
| History window | Issues/PRs updated in the last `github_history_days` days (config, default 365; no Settings UI) |
| Disconnect | As Notion/Jira, plus a link to manage or uninstall the app on GitHub |
| Needs-reconnect | A persisted column (`oauth_connections.needs_reconnect_at`), set by api and worker; Jira moves onto it too |

## 1. The GitHub App (one-time setup, documented in `desktop/broker/README.md`)

- GitHub → Settings → Developer settings → **GitHub Apps → New GitHub App**.
- Repository permissions, all **Read-only**: Contents, Issues, Pull requests, Metadata. No account permissions. Webhook
  inactive.
- **Callback URL** `<worker>/callback/github_repos`. **Expire user authorization tokens**: on. **Request user
  authorization (OAuth) during installation**: off (the app starts from the authorize URL instead). "Where can this app
  be installed": Any account.
- Worker secrets `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`; Worker variable `GITHUB_APP_SLUG`. Distinct from
  the sign-in app's `GITHUB_CLIENT_ID/SECRET`.
- Web (server) mode, in **both** api and worker settings: `GITHUB_SOURCE_APP_SLUG`, `GITHUB_SOURCE_CLIENT_ID`,
  `GITHUB_SOURCE_CLIENT_SECRET`; api also `GITHUB_SOURCE_REDIRECT_URI`. Not to be confused with `GITHUB_APP_ID` (code
  review bot placeholder) or `GITHUB_OAUTH_*` (web social login).

## 2. Sign-in Worker (`desktop/broker/`)

New purpose `github_repos`, alongside `notion`, `jira`, `google`, `github`:

- `GET /start/github_repos?state&challenge&port` — same validation and pending entry as the others; redirects to
  `https://github.com/login/oauth/authorize?client_id={GITHUB_APP_CLIENT_ID}&redirect_uri={BROKER_URL}/callback/github_repos&state={state}`.
- `GET /callback/github_repos?code&state` (or `?error&state`) — exchanges `code` at
  `https://github.com/login/oauth/access_token`. **Any `error` field in the JSON is a failure even with HTTP 200**
  (GitHub's token endpoint does this). On success: `GET /user` (login, id) and `GET /user/installations` (all pages).
  Result: `{provider: "github_repos", access_token, refresh_token, expires_in, refresh_token_expires_in, user_id,
  login, installations: [{id, account_login, account_type}]}`. Denied → `{error: "GitHub access wasn't granted."}`.
  Sealing, pickup and redirect as for the existing purposes.
- `POST /refresh/github_repos {refresh_token}` → `{access_token, refresh_token, expires_in, refresh_token_expires_in}`.
  `error: "bad_refresh_token"` (in a 200 or 4xx) or HTTP 401/403 → **400**; any other error or status → **502**.
- Tests (vitest, GitHub faked): start/callback/refresh, 200-with-error on exchange and refresh, no installations,
  paginated installations.

## 3. Data

New migration `infra/postgres/015_github_source.sql`, mirrored in `services/api/app/core/schema.py` (existing server
databases get schema from there at startup):

- `ALTER TYPE rag_source ADD VALUE IF NOT EXISTS 'github';` — allowed inside the migration transaction on PG ≥ 12,
  **provided nothing else in 015 (or the same transaction) uses `'github'`**.
- `ALTER TABLE documents ADD COLUMN IF NOT EXISTS external_version TEXT;` — the source's own version marker (blob sha
  for files, `updated_at` for issues/PRs), used to skip unchanged items **before** enqueueing.
- `github_failures`: `connection_id UUID → oauth_connections ON DELETE CASCADE, external_id TEXT, version TEXT,
  attempts INT, last_error TEXT, updated_at TIMESTAMPTZ, PRIMARY KEY(connection_id, external_id)` — written when an
  item is dead-lettered; the sync skips an item whose current version equals its recorded failed version (it is tried
  again only once the source changes). Deleted with the item's document and by the cursor reset rule.
- `ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS needs_reconnect_at TIMESTAMPTZ;`
- `github_repos`: `id UUID PK, connection_id UUID → oauth_connections ON DELETE CASCADE, installation_id BIGINT,
  repo_id BIGINT, full_name TEXT, html_url TEXT, default_branch TEXT, private BOOL, tree_sha TEXT NULL,
  issues_cursor TIMESTAMPTZ NULL, last_full_scan_at TIMESTAMPTZ NULL, UNIQUE(connection_id, repo_id)`.
- GitHub `oauth_connections` row: `provider='github'`, `workspace_id` = GitHub user id, `workspace_name` = login,
  `site_url` = `https://github.com/{login}`, tokens (encrypted at rest as today) + `expires_at`.
- Documents: `source='github'`, `external_id` = `file:{repo_id}:{path}` | `issue:{repo_id}:{number}` |
  `pr:{repo_id}:{number}`; title `owner/repo · path` | `owner/repo #N: title`; url = GitHub html URL.
- Models updated in both `services/api/app/models/rag.py` and `services/worker/app/models/rag.py`.
- **Cursor reset rule:** every path that deletes a connection's documents — Disconnect with remove, `remove-documents`,
  and the reconnect-to-a-different-account delete in `app/integrations/connections.py` — also deletes that
  connection's `github_repos` rows (one shared helper), so a later sync starts from scratch.

## 4. Connecting and tokens

- Desktop: `POST /connect/github_repos/start` (owner) and the existing finish flow.
  `save_github_connection(session, org_id, result)` in `app/integrations/connections.py`: reuse the row; keep
  documents only if the GitHub **user id** is unchanged (else delete documents + `github_repos`); clear
  `disconnected_at` and `needs_reconnect_at`. Installation ids are taken only from `GET /user/installations` with the
  user's own token — never from a query parameter.
- Finish page: GitHub tile, "GitHub is *connected*.", card = login + "N repositories" (or "No repositories yet —
  choose them on GitHub" with the install link when there are none).
- Web: `services/api/app/api/routes/github_source_oauth.py` — `/oauth/github_source/authorize` (require_auth; Redis
  CSRF state carrying org_id, like `jira_oauth.py`; redirects to the same OAuth authorize URL) and `/callback`
  (exchange, list installations, save, redirect to `{FRONTEND_URL}/connections?connected=github`).
- **Refresh (api `app/integrations/github_auth.py` and the worker's client)**, when `expires_at` is within 5 min or a
  call returned 401:
  Runs in its **own short transaction/session**, never the caller's (so worker handlers aren't blocked for a whole sync).
  1. `SELECT … FOR UPDATE` the connection row; re-read `expires_at` and the tokens. Reuse the stored token and stop
     (releasing the lock) only if it is still valid **and**, on the 401 path, differs from the token that just failed.
  2. Otherwise refresh (desktop: Worker `/refresh/github_repos`; web: GitHub directly with the server's client
     secret), write the rotated tokens only if `disconnected_at IS NULL`, clear `needs_reconnect_at`, commit.
  3. Refused (400 / `bad_refresh_token`) → set `needs_reconnect_at`, message "GitHub needs reconnecting. Use Reconnect
     on the Connections page." Unavailable (502, network) → transient error, no flag.
- `GET /connections` reports `needs_reconnect` from the column. **Jira switches to the same column** (api and worker
  refresh paths set it; any successful refresh, reconnect and Disconnect clear it), replacing the in-memory set in
  `jira_auth.py`. Syncing still attempts a refresh while the flag is set, so a false positive clears itself.

## 5. Syncing

`POST /sync/github` and auto_sync, with the same auth, rate limit and disconnected/needs-reconnect checks as
`/sync/jira`. A sync:

1. **Repositories.** For each installation from `GET /user/installations`, list
   `GET /user/installations/{id}/repositories` (all pages). Only if **every** listing succeeded: upsert
   `github_repos` (refreshing `full_name`, `html_url`, `default_branch`) and delete repos no longer listed together
   with their documents. A failed listing leaves repos and documents alone.
2. **Budget.** At most `max_github_items_per_sync` (default 500) items are enqueued per sync. Each repo contributes
   two candidate streams (changed files, changed issues/PRs); the sync takes **one item per stream per pass**,
   cycling through all repos, and **rotates the starting repo** each sync (stored per connection), so no repo or
   stream starves. Unchanged items, and items whose version matches a recorded failure, never count. Items enqueued
   but not yet processed still look changed to the next sync and may be enqueued again (dedupe makes that cheap); this
   converges.
3. **Files** per repo: `GET /repos/{full_name}/git/trees/{default_branch}?recursive=1`. 409 (empty repo) → nothing to
   do. If the tree sha equals `tree_sha`, skip. Otherwise, for each blob ending in `.md`/`.mdx` (any directory, incl.
   `README.md`, `docs/…`) with size ≤ 1 MB: enqueue `rag.github_file_updated` only when the stored document's
   `external_version` differs from the blob sha (or there is none). When the response is **not** `truncated`, delete
   documents whose path is no longer in the tree; when truncated, delete nothing. Set `tree_sha` only once no changed
   file of this tree remains unenqueued.
4. **Issues and PRs** per repo: `GET /repos/{full_name}/issues?state=all&sort=updated&direction=asc&since=…`
   (returns PRs too; `since` filters by update time).
   - Normal sync: `since` = `issues_cursor` minus 1 hour of overlap (or now − `github_history_days` when unset).
   - **Full scan** when `last_full_scan_at` is unset or older than 7 days: `since` = now − `github_history_days`.
     It re-enqueues anything whose stored `external_version` ≠ its `updated_at` (this recovers items lost in transit)
     and — **only when it paged through the entire window without stopping early** (budget, rate limit or error) —
     deletes issue/PR documents updated inside the window that GitHub no longer returned (deleted or transferred).
     `last_full_scan_at` is set under the same completion condition; an incomplete full scan resumes next sync.
   - Enqueue `rag.github_pr_updated` for items with `pull_request`, else `rag.github_issue_updated`, only when
     `external_version` ≠ `updated_at`. Advance `issues_cursor` to the `updated_at` of the last item **examined**
     (enqueued or unchanged) before the budget ran out.
   - Shrinking the window doesn't delete older documents; growing it backfills on the next full scan.
5. Dedupe keys embed `connection_id`, the external id and the version (blob sha / `updated_at`).

Worker (`services/worker/app/tasks/github.py`, registered in `HANDLERS`):

- `rag.github_file_updated`: fetch the blob, chunk, embed, upsert; set `external_version` to the blob sha.
- `rag.github_issue_updated`: issue + `/issues/{n}/comments` → one text (title, state, labels, body, comments with
  authors).
- `rag.github_pr_updated`: PR body + `/issues/{n}/comments` (conversation) + `/pulls/{n}/reviews` (review bodies) +
  `/pulls/{n}/comments` (review comments) + `/pulls/{n}/files` (names only).
- Always refresh `title`/`url` from the current repo name, even when content is unchanged (repo renames).
- `external_version` is written only after a successful upsert. When an item is dead-lettered, the worker records it
  in `github_failures` with its version, so syncs stop retrying it until it changes on GitHub. A dead-lettered **file**
  is likewise retried only once its blob changes (the tree sha moves on); stated, accepted.
- 404/410 on the item itself → delete its document and ack.
- Disconnected connection or NULL token → ack and skip (as Notion/Jira).
- GitHub client: tenacity with backoff on 5xx, 429 and secondary rate limits (honour `Retry-After` /
  `x-ratelimit-reset`); a 401 triggers the locked refresh in §4 once, then fails. User-to-server tokens get 5,000
  requests/hour; a full 500-item sync costs roughly 1,500–2,500 calls.

Retrieval, citations and the knowledge-base filter need no change beyond recognising `source='github'` (label
"GitHub"; the citation shows the document title and links its url).

## 6. UI

- Connections page — GitHub card: Connect GitHub · waiting state · connected: login, "N repositories" (names in a
  tooltip), last synced, document count, Sync now, **Choose repositories on GitHub**
  (`https://github.com/apps/{slug}/installations/new`), Disconnect. "No repositories yet" + the same link when the
  list is empty. Reconnect state from `needs_reconnect`.
- Disconnect dialog adds "You can also uninstall the app on GitHub" with the same link.
- Knowledge base / chat: source label "GitHub" and the official logo from `public/brand/github.svg` when present.

## 7. Errors

| Situation | What the person sees |
| --- | --- |
| Authorize denied | Finish page and card: "GitHub access wasn't granted." |
| No installation / no repositories | Card and finish page: "No repositories yet" + link to choose on GitHub. GitHub doesn't send you back to the app after installing, so the copy says "After choosing them on GitHub, come back here and press Sync now." |
| Refresh refused (app uninstalled, revoked) | Card: Reconnect GitHub; syncing stops; documents stay until Disconnect |
| Rate limited / GitHub down | Sync stops early and resumes next run; no Reconnect |
| Repo access removed on GitHub | Its documents are removed on the next complete sync |

## 8. Testing

- Sign-in Worker (vitest): §2's cases.
- API (pytest, fake GitHub via `httpx.MockTransport`): save + reconnect rule (same/different user); cursor reset on
  every delete path; repo listing (all-or-nothing removal, multiple installations); budget round-robin and "unchanged
  doesn't count" (a tree larger than the cap converges over several syncs); tree diff (new/changed/deleted, `.md`
  filter, size limit, truncated → no deletes, empty repo 409); issue cursor with overlap, full scan recovery and
  pruning only after a complete scan; failed-version skip; round-robin rotation (one large repo can't take the whole
  budget); refresh lock (two concurrent refreshers → one GitHub call, both get the new token; a 401 with an unexpired
  but revoked token still refreshes); 200-with-error;
  needs-reconnect column for GitHub and Jira; disconnected 409; web OAuth routes; schema.py mirror.
- Worker (pytest): each handler's text assembly, `external_version` only after success, rename refresh, 404 delete,
  disconnected skip, 401 → locked refresh.
- E2E: fake broker gains `github_repos`; a fake GitHub API serves one repo with a README and an issue; Connect GitHub
  → finish page → Sync now → both documents in the knowledge base → Disconnect (remove).
- Manual, after registering the GitHub App: connect a real private repo, sync, ask a question that cites it.

## Out of scope

- Source code files other than Markdown; diffs.
- Webhooks / real-time updates (polling like Notion and Jira).
- GitHub Discussions, wikis, releases.
- Per-repo access control inside the workspace (connection-level, like Notion/Jira).
- A Settings control for the history window (config value only).
