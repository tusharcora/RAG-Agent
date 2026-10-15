#!/usr/bin/env bash
# Nightly Postgres dump, keeping the last 14. Run from the repo root on the
# server, e.g. via cron:  0 3 * * * cd ~/RAG-Agent && ./deploy/backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env.production; set +a
mkdir -p backups
file="backups/backbone-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$file"
ls -1t backups/backbone-*.sql.gz | tail -n +15 | xargs -r rm --
echo "Saved $file"
