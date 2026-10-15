# Deploying on Oracle Cloud Always Free

One Ampere A1 VM runs the whole stack with `docker-compose.prod.yml`. A
Cloudflare Tunnel gives it a public HTTPS address without opening any ports.

```
browser ──https──► Cloudflare ──tunnel──► frontend (Next.js) ──/api/*──► api (FastAPI)
                                                                   worker, postgres, redis, rabbitmq
```

Everything, including OAuth callbacks and the MCP server, is served from one
hostname: the frontend proxies `/api/*` to the API.

## What you need

- A domain on Cloudflare (free plan). A quick `trycloudflare.com` tunnel won't
  do: its address changes on every restart, which breaks OAuth redirects.
- An Oracle Cloud account (a card is required for verification).
- Your Google AI Studio and Voyage keys, and the Notion (and optionally Jira,
  Google, GitHub) OAuth app credentials.

## 1. Domain on Cloudflare

Add the domain to Cloudflare (Free plan) and switch its nameservers to the two
Cloudflare gives you at your registrar. Wait until Cloudflare shows it Active.

## 2. Create the VM

1. Oracle Cloud console → Compute → Instances → Create instance.
2. Image: **Canonical Ubuntu 24.04** (aarch64). Shape: **Ampere → VM.Standard.A1.Flex,
   2 OCPU, 12 GB**. Boot volume: 100 GB.
3. Add your SSH public key (`cat ~/.ssh/id_ed25519.pub`, or let Oracle generate one and download it).
4. Leave networking at defaults. No ingress rules are needed: the tunnel only makes
   outbound connections.

"Out of capacity" for A1 is common. Retry later, or pick another availability domain.

The Always Free A1 allowance is 2 OCPU / 12 GB. Oracle may reclaim an Always Free
instance that stays idle for 7 days (CPU, network and memory all under 20%), so
keep the backups below.

## 3. Install Docker on the VM

```bash
ssh ubuntu@<vm-public-ip>
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && exit   # log back in afterwards
```

## 4. Get the code

```bash
git clone https://github.com/tusharcora/RAG-Agent.git && cd RAG-Agent
git checkout changes   # or main, once it's merged
```

If the repo is private, use a GitHub personal access token (repo read) as the
password, or add a deploy key.

## 5. Create the tunnel

Cloudflare dashboard → Zero Trust → Networks → Tunnels → **Create a tunnel** →
Cloudflared → name it → copy the **token** from the install command (the long
string after `--token`). Skip the install step itself: the stack runs
cloudflared in Docker.

Then add a **public hostname**: subdomain `app` (or blank for the bare domain),
your domain, service **HTTP** → `frontend:3000`.

## 6. Configure

```bash
cp .env.production.example .env.production
nano .env.production
```

Fill in every blank. Generate the secrets on the VM:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"        # JWT_SECRET
python3 -c "import secrets; print(secrets.token_urlsafe(24))"        # Postgres / RabbitMQ passwords
docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"   # TOKEN_ENCRYPTION_KEY
```

Replace every `app.example.com` with your hostname.

## 7. Point the OAuth apps at the new address

| Provider | Redirect URI to add |
|---|---|
| Notion (integration settings) | `https://<host>/api/oauth/notion/callback` |
| Jira (developer console) | `https://<host>/api/oauth/jira/callback` |
| Google sign-in (optional) | `https://<host>/api/auth/google/callback` |
| GitHub sign-in (optional) | `https://<host>/api/auth/github/callback` |

## 8. Start it

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

The first build takes several minutes on the A1. Then open `https://<host>`, sign
up (the first account owns a new organization), connect Notion and sync.

Logs: `docker compose -f docker-compose.prod.yml --env-file .env.production logs -f api worker`

## 9. Backups

```bash
crontab -e
# add:
0 3 * * * cd ~/RAG-Agent && ./deploy/backup.sh >> backups/backup.log 2>&1
```

Keeps the last 14 nightly dumps in `backups/`. Copy them off the VM now and then
(`scp`), since a reclaimed instance takes its disk with it. Restore with
`gunzip -c <file> | docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres psql -U backbone backbone`.

## Updating

```bash
cd ~/RAG-Agent && git pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

## Good to know

- **Anyone can sign up.** Each question spends your Gemini/Voyage quota. To keep the
  demo private, put the hostname behind Cloudflare Access (Zero Trust → Access →
  Applications, free for up to 50 users) or watch the rate limits in `.env.production`.
- **Local models** (Ollama) aren't available on the server; use the built-in Gemini or
  an Anthropic/OpenAI key in Settings.
- **Claude.ai / ChatGPT connectors** work here, since the MCP server is public:
  `https://<host>/api/mcp` with a token from Settings.
- **No Grafana/Prometheus** in this setup, to fit the free VM. Trace ids are still
  recorded on events.
