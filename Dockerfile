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
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data /app/reports \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# First start (no database yet): build synthetic data, run tests and reports, then serve.
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
