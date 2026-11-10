#!/bin/sh
set -e
# Started as root (the image default): hosted disks and volumes are often mounted root-owned,
# so hand the data and reports folders to the app user, then restart this script as that user.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/data /app/reports
  chown -R appuser:appuser /app/data 2>/dev/null || true
  chown appuser:appuser /app/reports 2>/dev/null || true
  exec python -c 'import os; os.setgroups([]); os.setgid(1000); os.setuid(1000); os.environ["HOME"] = "/home/appuser"; os.execvp("sh", ["sh", "/app/docker-entrypoint.sh"])'
fi

# Hosts such as Render provide the public address and port; local Docker uses the defaults.
export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-${RENDER_EXTERNAL_URL:-http://localhost:8000}}"
PORT="${PORT:-8000}"

# Reports go to the bind-mounted ./reports when the container user can write there.
if [ -z "${REPORTS_DIR:-}" ] && ! touch /app/reports/.write-test 2>/dev/null; then
  echo "reports/ is not writable by the container user; writing reports to /app/data/reports"
  export REPORTS_DIR=/app/data/reports
else
  rm -f /app/reports/.write-test
fi
if [ "${SEED_ON_START:-true}" = "true" ] && [ ! -f "${DATA_DIR:-/app/data}/app.db" ]; then
  echo "First start: generating synthetic data, running tests and building reports (2-3 minutes)..."
  python manage.py demo
fi
exec python manage.py run --host 0.0.0.0 --port "$PORT"
