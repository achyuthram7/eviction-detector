#!/bin/sh
set -e
# Reports go to the bind-mounted ./reports when the container user can write there.
if ! touch /app/reports/.write-test 2>/dev/null; then
  echo "reports/ is not writable by the container user; writing reports to /app/data/reports"
  export REPORTS_DIR=/app/data/reports
else
  rm -f /app/reports/.write-test
fi
if [ "${SEED_ON_START:-true}" = "true" ] && [ ! -f /app/data/app.db ]; then
  echo "First start: generating synthetic data, running tests and building reports (2-3 minutes)..."
  python manage.py demo
fi
exec python manage.py run --host 0.0.0.0 --port 8000
