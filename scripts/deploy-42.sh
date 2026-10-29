#!/usr/bin/env bash
set -euo pipefail

# Publish the application to the existing 42 host without copying runtime secrets.
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${PI_BUGFIX_DEPLOY_TARGET:-root@192.168.2.42}"
remote_dir="${PI_BUGFIX_DEPLOY_DIR:-/opt/pi-bugfix-console}"

if [[ ! "$remote_dir" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  printf 'Invalid remote directory: %s\n' "$remote_dir" >&2
  exit 2
fi

cd "$project_dir"
ssh -o BatchMode=yes "$target" bash -s -- "$remote_dir" <<'REMOTE_CHECK'
set -euo pipefail
cd "$1"
test -f .env
test -f repos.json
test -S /var/run/docker.sock
if docker compose ps --status running --services | grep -qx api; then
  active_jobs="$(docker compose exec -T api python3 -c \
    'import os, sqlite3; db = sqlite3.connect(os.getenv("DATABASE_PATH", "/data/jobs.sqlite")); print(db.execute("SELECT count(*) FROM jobs WHERE status = ?", ("running",)).fetchone()[0])')"
  if (( active_jobs > 0 )); then
    printf 'Refusing to deploy while %s job(s) are running\n' "$active_jobs" >&2
    exit 1
  fi
fi
REMOTE_CHECK

rsync -az --relative \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'node_modules/' \
  --exclude 'dist/' \
  --exclude 'tsconfig.tsbuildinfo' \
  ./compose.yaml ./backend/ ./frontend/ "$target:$remote_dir/"

ssh -o BatchMode=yes "$target" bash -s -- "$remote_dir" <<'REMOTE_DEPLOY'
set -euo pipefail
cd "$1"
docker compose config --quiet
docker compose up -d --build

for attempt in $(seq 1 30); do
  running="$(docker compose ps --status running --services)"
  if grep -qx api <<<"$running" && grep -qx worker <<<"$running" && \
     grep -qx web <<<"$running" && \
     curl -fsS -o /dev/null http://127.0.0.1/; then
    docker compose ps
    exit 0
  fi
  sleep 2
done

docker compose ps >&2
docker compose logs --tail=50 api worker web >&2
printf 'Service did not become ready within 60 seconds\n' >&2
exit 1
REMOTE_DEPLOY

printf 'Ready: http://192.168.2.42/\n'
