# Deterministic build for the Mag 7 News Bot. Railway (and any host) uses this
# Dockerfile when present, bypassing Railpack/Nixpacks auto-detection — which
# otherwise fails to find a start command for a package-style entrypoint.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Long-running worker (no HTTP port). State persists via DB_PATH on a volume.
CMD ["python", "-m", "mag7bot.app"]
