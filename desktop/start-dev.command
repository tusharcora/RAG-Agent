#!/bin/bash
# Double-click in Finder (or run in Terminal) to start the desktop version
# from this checkout: sets up Python 3.12, builds the UI, runs the launcher.
# Ctrl-C (or closing this window) stops everything, Postgres included.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Knowledge Agent (desktop, from source) =="

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! command -v uv >/dev/null 2>&1; then
  echo "-- Installing uv (manages Python 3.12 for this app)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ ! -x .venv-desktop/bin/python ]; then
  echo "-- Creating .venv-desktop with Python 3.12"
  uv venv --python 3.12 .venv-desktop
fi
echo "-- Installing Python packages"
uv pip install --quiet --python .venv-desktop/bin/python -r desktop/requirements.txt

if ! command -v npm >/dev/null 2>&1; then
  echo "npm isn't installed. Install Node 20+ from https://nodejs.org and run this again."
  read -r -p "Press Return to close." _; exit 1
fi
echo "-- Building the UI (a minute or two)"
(
  cd services/frontend
  [ -d node_modules ] || npm install
  DESKTOP_BUILD=1 npm run build >/tmp/knowledge-agent-ui-build.log 2>&1 \
    || { echo "UI build failed; see /tmp/knowledge-agent-ui-build.log"; tail -20 /tmp/knowledge-agent-ui-build.log; exit 1; }
)

if command -v ollama >/dev/null 2>&1 || [ -d /Applications/Ollama.app ]; then
  echo "-- Ollama is installed (setup will check it's running)"
else
  echo "-- Ollama isn't installed; the setup screen will walk you through it"
fi

echo "-- Starting (opens your browser; Ctrl-C here to stop)"
# AUTH_BROKER_URL (if set in your shell) reaches the app: it points Connect/sign-in at your Worker.
exec .venv-desktop/bin/python desktop/launcher.py
