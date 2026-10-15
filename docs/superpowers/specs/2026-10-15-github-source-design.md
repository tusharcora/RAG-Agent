# GitHub as a knowledge source

Date: 2026-10-15 · Branch: `desktop-app` · Builds on `2026-10-15-desktop-online-accounts-design.md` (sign-in Worker,
desktop Connect flow) and the Disconnect work (`014_connection_disconnect.sql`).

## Goal

Connect GitHub like Notion and Jira, so answers can draw on a person's repositories: Markdown docs, issues and pull
requests, from repositories they choose, private ones included. Desktop and web.

This is separate from **Continue with GitHub** (app sign-in, `github` identity purpose), which stays as it is.

## Decisions

| Question | Decision |
| --- | --- |
| What is synced | Markdown files, issues (with comments), pull requests (description, review comments, changed-file list; no diff) |
| Access model | A GitHub App with read-only permissions, installed on the repositories the person picks |
| Token | GitHub App **user-to-server** token (expires after 8 h) plus refresh token, refreshed through the sign-in Worker like Jira |
| Repo selection | On GitHub's install page ("All" or "Only select repositories"); changed later on GitHub |
| History window | Issues/PRs updated in the last 365 days (setting `github_history_days`, editable in Settings) |
| Disconnect | Same as Notion/Jira (remove or keep documents) plus a link to uninstall the app on GitHub |

## 1. The GitHub App (one-time setup, documented in `desktop/broker/README.md`)

- GitHub → Settings → Developer settings → **GitHub Apps → New GitHub App**.
- Repository permissions, all **Read-only**: Contents, Issues, Pull requests, Metadata. No account permissions, no
  webhook (uncheck "Active").
- **Callback URL** `<worker>/callback/github_repos`; **Request user authorization (OAuth) during installation**: on;
  **Expire user authorization tokens**: on. "Where can this app be installed": Any account.
- Worker secrets `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`; Worker variable `GITHUB_APP_SLUG` (the app's URL
  name). Distinct from the sign-in app's `GITHUB_CLIENT_ID/SECRET`.
- Web (server) mode: settings `GITHUB_SOURCE_APP_SLUG`, `GITHUB_SOURCE_CLIENT_ID`, `GITHUB_SOURCE_CLIENT_SECRET`,
  `GITHUB_SOURCE_REDIRECT_URI` (its own GitHub App, callback on the web API). Not to be confused with `GITHUB_APP_ID`
  (code review bot placeholder) or `GITHUB_OAUTH_*` (web social login).

## 2. Sign-in Worker (`desktop/broker/`)

New purpose `github_repos`, alongside `notion`, `jira`, `google`, `github`:

- `GET /start/github_repos?state&challenge&port` — same validation and pending entry as the others; redirects to
  `https://github.com/apps/{GITHUB_APP_SLUG}/installations/new?state={state}`.
- `GET /callback/github_repos?code&installation_id&setup_action&state` — exchanges `code` at
  `https://github.com/login/oauth/access_token` (client id/secret). Result:
  `{provider: "github_repos", access_token, refresh_token, expires_in, refresh_token_expires_in, installation_id,
  account_login, account_type}` (`account_*` from `GET /user/installations`, matched by `installation_id`). If GitHub
  returns no `code` (install without authorize, or cancelled): `{error: "GitHub access wasn't granted."}`.
  Everything else (sealing, pickup, redirect) as for the existing purposes.
- `POST /refresh/github_repos {refresh_token}` → `{access_token, refresh_token, expires_in}`; 400 only when GitHub
  rejects the refresh token (`bad_refresh_token`, 400/401/403), 502 otherwise — same rule as `/refresh/jira`.
- Tests (vitest, GitHub faked) mirror the Jira ones.

## 3. Data

New migration `infra/postgres/015_github_source.sql`:

- `ALTER TYPE rag_source ADD VALUE IF NOT EXISTS 'github';`
- `github_repos` — one row per repository the connection can read:
  `id UUID PK, connection_id UUID → oauth_connections ON DELETE CASCADE, repo_id BIGINT, full_name TEXT,
  default_branch TEXT, private BOOL, tree_sha TEXT NULL, issues_synced_at TIMESTAMPTZ NULL,
  pulls_synced_at TIMESTAMPTZ NULL, UNIQUE(connection_id, repo_id)`.
- `oauth_connections` row for GitHub: `provider='github'`, `workspace_id` = installation id, `workspace_name` =
  account login, `site_url` = `https://github.com/{login}`, tokens + `expires_at` as for Jira.
- Documents: `source='github'`, `external_id` = `file:{repo_id}:{path}` | `issue:{repo_id}:{number}` |
  `pr:{repo_id}:{number}`; title `owner/repo · path` | `owner/repo #N: title`; url = the GitHub html URL.
- Models updated in both `services/api/app/models/rag.py` and `services/worker/app/models/rag.py`.

## 4. Connecting

- Desktop: `POST /connect/github_repos/start` (new purpose; owner only) and the existing finish flow;
  `save_github_connection(session, org_id, result)` in `app/integrations/connections.py`, same reconnect rule as
  Notion/Jira (keep documents only if the installation id is unchanged). Finish page: GitHub tile, "GitHub is
  *connected*.", card = account login + "N repositories".
- Web: `services/api/app/api/routes/github_source_oauth.py` — `/oauth/github_source/authorize` (require_auth, Redis
  CSRF state carrying org_id, like `jira_oauth.py`) and `/callback` (exchange, save, redirect to
  `{FRONTEND_URL}/connections?connected=github`).
- Token refresh: `app/integrations/github_auth.py` (api) and the worker's GitHub client refresh before calls when
  `expires_at` is within 5 minutes — desktop through the Worker's `/refresh/github_repos`, web directly with the
  server's client secret. Refusal → needs-reconnect flag (same mechanism as Jira) and message
  "GitHub needs reconnecting. Use Reconnect on the Connections page."

## 5. Syncing

`POST /sync/github` (and auto_sync), same auth, rate limit and disconnected checks as `/sync/jira`:

1. Refresh the repo list: `GET /user/installations/{installation_id}/repositories` (paginated). Upsert `github_repos`;
   skip archived repos and forks; **delete documents of repos no longer listed** and their `github_repos` rows.
2. Per repo, enqueue up to `max_github_items_per_sync` (default 500) items in total, oldest work first:
   - **Files:** `GET /repos/{full_name}/git/trees/{default_branch}?recursive=1`; if the tree sha equals
     `tree_sha`, skip. Otherwise enqueue `rag.github_file_updated` for each blob that is `README*`, under `docs/`, or
     `*.md`/`*.mdx`, size ≤ 1 MB, with the blob sha; documents for paths no longer in the tree are deleted. Store
     `tree_sha` only once every file of that tree has been enqueued.
   - **Issues / PRs:** `GET /repos/{full_name}/issues?state=all&since=…&sort=updated&direction=asc` (since =
     `issues_synced_at` or now − `github_history_days`); items with `pull_request` go to `rag.github_pr_updated`,
     others to `rag.github_issue_updated`. Advance the cursor to the last enqueued item's `updated_at`.
3. Dedupe keys embed `connection_id`, the external id and the blob sha / `updated_at`.

Worker (`services/worker/app/tasks/github.py`, registered in `HANDLERS`):

- `rag.github_file_updated`: fetch the blob (raw), chunk, embed, upsert (skip when `content_hash` is unchanged).
- `rag.github_issue_updated`: issue + comments → one text (title, state, labels, body, comments with authors).
- `rag.github_pr_updated`: PR + review comments + changed-file names (`/pulls/{n}/files`, names only).
- Disconnected connection or NULL token → ack and skip (as the Notion/Jira handlers do).
- GitHub client: tenacity retries with backoff on 5xx/429 and secondary rate limits (honour `Retry-After` and
  `x-ratelimit-reset`); never retries a 401 (→ refresh once, then needs-reconnect).

Retrieval, citations and the knowledge-base filter need no change beyond recognising `source='github'` (label
"GitHub", citation shows the document title and links its url).

## 6. UI

- Connections page: a GitHub card (Connect GitHub · waiting state · connected: account, "N repositories" with names
  in a tooltip, last synced, document count, Sync now, **Choose repositories on GitHub**
  (`https://github.com/settings/installations/{installation_id}` for a user account,
  `https://github.com/organizations/{login}/settings/installations/{installation_id}` for an org), Disconnect).
  "No repositories selected yet" when the list is empty. Reconnect state as for Jira.
- Disconnect dialog adds "You can also uninstall the app on GitHub" with the same link.
- Settings: "GitHub history" (days, default 365).
- Knowledge base / chat: source label and icon slot "GitHub" (official logo from `public/brand/github.svg` when present).

## 7. Errors

| Situation | What the person sees |
| --- | --- |
| Install cancelled / not authorized | Finish page and card: "GitHub access wasn't granted." |
| No repositories selected | Card: "No repositories selected yet" + link to choose |
| Refresh refused (uninstalled, revoked) | Card: Reconnect GitHub; syncing stops; documents stay until Disconnect |
| Rate limited / GitHub down | Sync stops early and resumes next run; no Reconnect |
| Repo access removed on GitHub | Its documents are removed on the next sync |

## 8. Testing

- Worker (vitest): start/callback/refresh for `github_repos`, missing code → error result, refresh 400 vs 502.
- API (pytest, fake GitHub via `httpx.MockTransport`): connect save + reconnect rule; repo list upsert/removal; tree
  diff (new/changed/deleted files, unchanged tree skipped, size and path filters); issue/PR cursoring and caps; history
  window; refresh desktop vs web; needs-reconnect; disconnected 409; web OAuth routes.
- Worker (pytest): each handler's text assembly, skip unchanged, skip disconnected, 401 → refresh path.
- E2E: fake broker gains `github_repos`; a fake GitHub API serves one repo with a README and an issue; Connect GitHub
  → finish page → Sync now → both documents in the knowledge base → Disconnect (remove).
- Manual, after registering the GitHub App: connect a real private repo, sync, ask a question that cites it.

## Out of scope

- Source code files other than Markdown; diffs.
- Webhooks / real-time updates (polling like Notion and Jira).
- GitHub Discussions, wikis, releases.
- Per-repo access control inside the workspace (connection-level, like Notion/Jira).
