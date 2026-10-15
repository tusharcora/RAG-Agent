# Knowledge Agent sign-in service (`desktop/broker/`)

A small Cloudflare Worker that holds the OAuth client secrets for Notion, Jira (Atlassian), Google and GitHub, so
the desktop app never ships them. It only handles the sign-in exchange: pending sign-ins live in KV for up to
10 minutes and encrypted results for up to 2 minutes. No documents, chats or search data ever reach it, and it
logs no codes, tokens, verifiers or identities.

## Endpoints

| Route | Purpose |
| --- | --- |
| `GET /start/{provider}?state&challenge&port` | Saves the pending sign-in and redirects to the provider (`notion`, `jira`, `google`, `github`). |
| `GET /callback/{provider}` | Provider redirect target. Exchanges the code, stores the encrypted result, sends the browser to `http://127.0.0.1:{port}/api/connect/finish?state=…`. |
| `POST /redeem` `{state, verifier}` | Returns the result once if `base64url(sha256(verifier))` matches the challenge. 403 wrong verifier (entry deleted), 404 unknown/expired/used. |
| `POST /refresh/jira` `{refresh_token}` | Refreshes a Jira token with the client secret. |
| `GET /health` | `ok` |

## Deploy

You need a Cloudflare account (the free tier is enough) and Node 18+.

```bash
cd desktop/broker
npm install
npx wrangler login
```

### 1. Create the KV namespace

```bash
npx wrangler kv namespace create PENDING
```

Copy the printed `id` into `wrangler.toml`, replacing `REPLACE_WITH_YOUR_KV_NAMESPACE_ID`.

### 2. Find your Worker's URL

Deploy once to learn the address (sign-ins won't work yet):

```bash
npx wrangler deploy
```

It prints something like `https://knowledge-agent-auth.<your-subdomain>.workers.dev`. Put that (no trailing
slash) in `wrangler.toml` as `BROKER_URL` under `[vars]`. Below, `<worker>` means that URL.

### 3. Register the four provider apps

Each one gets exactly one callback URL, `<worker>/callback/{provider}`:

- **Notion** — <https://www.notion.so/my-integrations> → New integration → type **Public**. Capabilities: **Read
  content** (nothing else needed). Redirect URI: `<worker>/callback/notion`. Copy the OAuth client ID and secret.
- **Jira (Atlassian)** — <https://developer.atlassian.com/console/myapps/> → Create → **OAuth 2.0 integration**.
  Permissions → Jira API → add `read:jira-work` and `read:jira-user`. Authorization → OAuth 2.0 (3LO) → callback
  URL `<worker>/callback/jira`. (`offline_access` is requested at sign-in to get a refresh token.) Settings →
  copy the client ID and secret. To let people other than you connect, set the app's distribution to **Sharing**.
- **Google** — <https://console.cloud.google.com/apis/credentials> → configure the OAuth consent screen (scopes
  `openid`, `email`, `profile`) → Create credentials → **OAuth client ID** → type **Web application** → authorized
  redirect URI `<worker>/callback/google`.
- **GitHub** — <https://github.com/settings/developers> → **New OAuth App** → Authorization callback URL
  `<worker>/callback/github`. Generate a client secret.

### 4. Set the secrets

```bash
npx wrangler secret put NOTION_CLIENT_ID
npx wrangler secret put NOTION_CLIENT_SECRET
npx wrangler secret put JIRA_CLIENT_ID
npx wrangler secret put JIRA_CLIENT_SECRET
npx wrangler secret put GOOGLE_CLIENT_ID
npx wrangler secret put GOOGLE_CLIENT_SECRET
npx wrangler secret put GITHUB_CLIENT_ID
npx wrangler secret put GITHUB_CLIENT_SECRET
openssl rand -base64 32 | npx wrangler secret put RESULT_KEY
```

`RESULT_KEY` encrypts results while they wait in KV (AES-256-GCM). It must decode to exactly 32 bytes. Rotating it
only invalidates sign-ins in flight at that moment.

### 5. Deploy and check

```bash
npx wrangler deploy
curl <worker>/health    # → ok
```

### 6. Point the desktop app at it

Set the desktop default `DEFAULT_AUTH_BROKER_URL` to `<worker>` (or run the app with `AUTH_BROKER_URL=<worker>`).
Empty means "no sign-in service": the Connect and sign-in buttons stay hidden.

## Develop

```bash
npm test            # vitest in the Workers runtime; every provider is faked with fetchMock
npm run typecheck   # tsc --noEmit
npx wrangler dev    # local; needs the secrets in a .dev.vars file (never commit it)
```
