# Eviction Settlement Opportunity Detector — prototype image (synthetic data only).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.lock.txt ./
RUN pip install -r requirements.lock.txt

COPY . .
# Hosts without a persistent disk (e.g. Render's free plan) build with SEED_AT_BUILD=true: the
# demo data, tests and reports are baked into the image, so the container starts in seconds and
# every restart returns to the same clean demo. The data's date is kept in data/.seed_date.
ARG SEED_AT_BUILD=false
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data /app/reports \
    && if [ "$SEED_AT_BUILD" = "true" ]; then \
         python manage.py demo && date +%F > /app/data/.seed_date; \
       fi \
    && chown -R appuser:appuser /app

EXPOSE 8000
# The entrypoint starts as root only to make mounted data folders writable, then runs
# everything as appuser (uid 1000). First start without a database: build synthetic data,
# run tests and reports, then serve on $PORT (default 8000).
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
