# Desktop app

The same Knowledge Agent, running entirely on one computer: no Docker, no
hosting, no accounts. This folder is milestone 1, a launcher that runs the
backend in local mode. The Tauri shell and installers come next.

## What changes in desktop mode

| Server version | Desktop version |
| --- | --- |
| Postgres + pgvector in Docker | Embedded Postgres 16 + pgvector ([pgserver](https://pypi.org/project/pgserver/)), stored in the app's data folder |
| RabbitMQ | The worker polls `event_log` (`FOR UPDATE SKIP LOCKED`), with the same retry/backoff/dead-letter rules |
| Redis | In-memory per process (fakeredis) |
| Voyage embeddings | Ollama `nomic-embed-text`, cut to 512 dims so the schema is unchanged |
| Gemini answers by default | Local Ollama model by default (`llama3.2`); Claude, OpenAI or Gemini keys still work from Settings |
| Email/Google/GitHub sign-in | None: one local account, signed in automatically |
| Notion OAuth | Notion internal integration secret, pasted on the Connections page |
| Jira OAuth | Not available yet |
| Next.js server + `/api` proxy | Static export served by the API process, API at `/api` on the same origin |

The server version is untouched: every switch defaults to the server
behaviour (`APP_MODE=server`, `QUEUE_BACKEND=rabbitmq`, `EMBEDDING_PROVIDER=voyage`).

## Run it on a Mac

```bash
# once
brew install python@3.12 ollama        # or install the Ollama app from ollama.com
ollama pull nomic-embed-text
ollama pull llama3.2
python3.12 -m venv .venv-desktop
.venv-desktop/bin/pip install -r desktop/requirements.txt
(cd services/frontend && npm install && DESKTOP_BUILD=1 npm run build)

# every time (Ollama must be running)
.venv-desktop/bin/python desktop/launcher.py
```

It opens http://127.0.0.1:47821 in your browser. Ctrl-C stops everything,
Postgres included. Python 3.12 is the newest version pgserver has wheels for.

Data lives in `~/Library/Application Support/Knowledge Agent` (macOS) or
`%LOCALAPPDATA%\Knowledge Agent` (Windows): the database and a
`secrets.json` with this install's signing and encryption keys. Delete the
folder to start over. Set `KNOWLEDGE_AGENT_HOME` to use a different folder.

## Notes

- Embeddings from Voyage and from Ollama live in different vector spaces.
  A database is either one or the other; switching means re-syncing.
- Running the launcher twice reuses the running instance rather than
  starting a second one on the same database.
