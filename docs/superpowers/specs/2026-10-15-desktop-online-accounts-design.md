# Desktop online accounts: Connect for Notion/Jira, optional app sign-in

Date: 2026-10-15 · Branch: `desktop-app` · Builds on desktop M2 (`2026-10-15-desktop-m2-tauri-shell-design.md`)
Mockups: the "Desktop sign-in flow" design canvas (8 screens).

## Goal

From the installed desktop app, a person can:

- **A.** Connect Notion and Jira with one click: approve on the provider's own page in their browser and land back
  connected. No integration secrets to paste, and Jira works in the desktop app again.
- **B.** Optionally sign in to the app with Google or GitHub, so the workspace carries their real identity.

Both without running any server themselves, and without their documents, chats or search index ever leaving the
computer. Only the sign-in exchange touches the internet.

Later (not in this spec), **D**: share a workspace with other people. A and B must not box D in.

## Decisions

| Question | Decision |
| --- | --- |
| Where the provider secrets live | A small sign-in service on Cloudflare Workers (free tier) |
| How the result reaches the app | One-time pickup: browser lands on the app's loopback address with only an id; the app redeems the result from the service with a secret only it holds (PKCE-style) |
| App sign-in | Optional; identity only (no lock screen, no password); offered once on first open and kept in Settings → Account |
| Providers | Notion, Jira (Atlassian), Google, GitHub |

## 1. The sign-in service (`desktop/broker/`)

A TypeScript Cloudflare Worker deployed with `wrangler`, with one KV namespace, `PENDING`.

### Endpoints

1. `GET /start/{provider}?state&challenge&port`
   - `provider` ∈ {`notion`, `jira`, `google`, `github`}; `port` an integer 1024–65535; `state` 32–128 URL-safe
     characters; `challenge` a base64url SHA-256 (43 characters). Anything else → 400 page.
   - Saves `{provider, challenge, port}` under `state` in KV for 10 minutes; refuses a `state` that already exists.
   - Redirects to the provider's authorize URL with `redirect_uri = {BROKER_URL}/callback/{provider}` and `state`.
     Scopes: Notion `owner=user`; Jira `read:jira-work read:jira-user offline_access`, `prompt=consent`;
     Google `openid email profile`; GitHub `read:user user:email`.
2. `GET /callback/{provider}?code&state` (or `?error&state`)
   - Loads and deletes the pending entry (unknown or expired state → error page, no redirect).
   - On `error`, or a failed code exchange: stores `{error: "<readable message>"}` as the result.
   - Otherwise exchanges the code with the provider using the client secret:
     - **Notion:** result `{access_token, workspace_id, workspace_name, bot_id}`.
     - **Jira:** result `{access_token, refresh_token, expires_in, cloud_id, site_url, site_name}`; the first site from
       `accessible-resources` (the one chosen on Atlassian's consent page).
     - **Google / GitHub:** fetches the identity, then discards the provider token. Result
       `{provider, provider_user_id, email, name, picture_url}` (GitHub: primary verified email from `/user/emails`).
   - Stores the result under `state`, AES-GCM encrypted with the `RESULT_KEY` Worker secret, for 120 seconds.
   - Redirects the browser to `http://127.0.0.1:{port}/api/connect/finish?state={state}`.
3. `POST /redeem` with JSON `{state, verifier}`
   - Requires `base64url(sha256(verifier)) == challenge`, using the challenge stored with the result. Wrong verifier →
     403 and the entry is deleted.
   - Returns the decrypted result once, then deletes it. Unknown or expired → 404.
4. `POST /refresh/jira` with JSON `{refresh_token}` → Atlassian's refresh response `{access_token, refresh_token,
   expires_in}`, or 400 with Atlassian's error.

### Secrets and data

- Worker secrets: `NOTION_CLIENT_ID/SECRET`, `JIRA_CLIENT_ID/SECRET`, `GOOGLE_CLIENT_ID/SECRET`,
  `GITHUB_CLIENT_ID/SECRET`, `RESULT_KEY`. Variable: `BROKER_URL`.
- KV holds only in-flight sign-ins: pending entries for at most 10 minutes, encrypted results for at most 120 seconds.
- No logging of codes, tokens, verifiers or identities. No database. No document content ever reaches the service.
- `desktop/broker/README.md`: step-by-step registration of the four provider apps (callback URLs), `wrangler` setup,
  secrets, deploy.

## 2. Desktop app: Connect for Notion and Jira (A)

### Settings

- New API/worker setting `auth_broker_url` (env `AUTH_BROKER_URL`). The desktop build bakes in the deployed Worker's
  address; empty means "no sign-in service": Connect buttons and app sign-in are hidden, and Notion falls back to the
  integration secret form.

### API routes (`services/api/app/api/routes/connect.py`, desktop mode only, 404 otherwise)

1. `POST /connect/{purpose}/start` where `purpose` ∈ {`notion`, `jira`, `google`, `github`}; owner only.
   - Creates `state` and a 32-byte random `verifier`; keeps `{purpose, verifier, user_id, org_id, created}` in process
     memory for 10 minutes (never written to disk, never sent to the browser). `finish` uses the stored user and org,
     since the browser tab that lands there may carry no session.
   - Returns `{state, url}` where `url` is the Worker's `/start` with `challenge = base64url(sha256(verifier))` and the
     API's own port.
2. `GET /connect/finish?state` — the browser lands here. Not behind `require_auth` (it's a fresh browser tab, possibly
   without the app's cookie); protected by `state` being known locally and the verifier never leaving the process.
   - Redeems with the Worker. On success:
     - **notion/jira:** saves the `OAuthConnection` for the local org through the same functions the web version's
       callbacks use (`notion_oauth.py`/`jira_oauth.py` refactored into shared `save_notion_connection(...)` /
       `save_jira_connection(...)`), tokens encrypted at rest as today.
     - **google/github:** links the identity (section 3).
   - Marks the state `done` (or `error` with a message) and returns a small self-contained HTML page: "Connected to
     Notion ✓ / Signed in as …, you can close this tab" or the error. Unknown state → the same page with "This sign-in
     link has expired. Start again from the app."
3. `GET /connect/status/{state}` → `{status: "pending" | "done" | "error", message?}`; owner only.
4. `POST /connect/{state}/cancel` → forgets the state; owner only.

### UI

- Connections page (desktop build, service configured):
  - Notion card: **Connect Notion** (primary) and "or paste an integration secret" (opens the existing form).
    Connected: "Choose pages in Notion" reruns the flow (Notion's page picker).
  - Jira card: **Connect Jira**. The "not available in the desktop app yet" message goes away.
  - Clicking Connect: calls `/start`, opens `url` in a new tab (`target=_blank`; in the Tauri app this opens the
    system browser), and the card shows "Finish signing in in your browser…" with Cancel and "Reopen the sign-in page".
  - The card polls `/status` every 2 s; `done` → refresh the card (and start the usual first sync); `error` → show the
    message with Try again; 10 minutes with no result → back to Connect.

### Jira token refresh

- Desktop mode: `services/api/app/integrations/jira_auth.py` and the worker's `app/integrations/jira.py` refresh
  through the Worker's `/refresh/jira` (no client secret on the device). Server mode unchanged.

### Errors

| Situation | What the person sees |
| --- | --- |
| Worker unreachable on Connect | "Couldn't reach the sign-in service. Check your internet connection." |
| Denied on the provider page | Browser page and card: "Notion access wasn't granted." |
| Redeem after 120 s / reused link | "This sign-in link has expired. Start again from the app." |
| Jira refresh fails (revoked) | Sync event fails with "Reconnect Jira" and the card shows Reconnect |

## 3. Optional app sign-in (B)

### Where it appears

- **First open:** a "Make it yours" screen before the existing setup steps, with Continue with Google, Continue with
  GitHub, and Skip for now. Shown once (recorded as `account_prompt_seen: true` in `desktop.json`).
- **Settings → Account:** signed out: the same two buttons. Signed in: name, email, provider, Sign out.
- Signed in, the sidebar's workspace card shows the person's name and initial (no remote image loaded; offline-safe).

### What signing in does

- Uses the same flow as section 2 with purpose `google`/`github`.
- On finish: upserts an `oauth_identities` row (`UNIQUE(provider, provider_user_id)`, existing table from
  `004_social_login.sql`) for the local user, and sets the local user's `email` and `display_name` from the identity
  (replacing `you@this-computer.local`). One identity at a time: signing in with the other provider replaces the link.
- The app still signs the local user in automatically. No password, no lock screen; the account is an identity, not
  an access control.
- **Sign out** (`POST /account/sign-out`, desktop only): deletes the identity link and restores
  `you@this-computer.local` / no display name. Documents, chats and connections are untouched.

### Room for D (sharing)

- The identity (`provider`, `provider_user_id`, email) is stable across devices; the workspace already has a stable
  org id; `org_invites`/`org_members` exist. Inviting a teammate later becomes "invite this email to my workspace".
- Where a shared workspace lives (a hosted copy, or sync between apps) is D's own design. Nothing here commits to it.

## 4. Testing

- **Worker** (`vitest` with Cloudflare's local Workers runtime; providers faked):
  start → callback → redeem for each provider; bad parameters rejected; reused `state` rejected; wrong verifier → 403
  and entry gone; second redeem → 404; expired entries; provider `error` → readable result; Jira refresh; nothing
  stored beyond the TTLs.
- **API** (pytest, fake Worker via `httpx.MockTransport`):
  start returns a URL with the right challenge; finish saves Notion and Jira connections (Jira site fields included);
  finish for Google/GitHub links the identity and updates the user; status/cancel; unknown state page; all routes
  404 in server mode; Jira refresh goes through the Worker in desktop mode and directly to Atlassian in server mode.
- **End to end** (existing Playwright script + a local fake Worker): Connect Notion lands connected; first-open
  Continue with Google shows the name in the sidebar; Skip leaves the local profile.
- **Real providers:** manual, after the provider apps are registered and the Worker deployed: Connect Notion, Connect
  Jira, sign in with Google and GitHub on the Mac.

## Out of scope

- Sharing a workspace (D).
- An in-app Jira site picker (the site chosen on Atlassian's consent page is used).
- Profile pictures loaded from the provider.
- Code signing, auto-update.
