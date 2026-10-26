# Social Pilot — single-tenant app image (one instance per client VPS).
#
# NOTE: run with a SINGLE uvicorn worker. The scheduler (publish + insights
# polling) lives in-process; multiple workers would double-post. SQLite WAL is
# single-writer anyway.
#
# Build/run via docker compose: `SITE_ADDRESS=client.example.com docker compose up -d`

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build identity — stamped in so a running instance can say which commit it is.
# Supplied by the release tool / compose (`GIT_SHA`, `BUILD_DATE`, `BUILD_DIRTY`);
# read back by app/build_info.py and reported on the management health endpoint.
ARG GIT_SHA=unknown
ARG BUILD_DATE=unknown
ARG BUILD_DIRTY=unknown
ENV SP_BUILD_SHA=${GIT_SHA} \
    SP_BUILD_DATE=${BUILD_DATE} \
    SP_BUILD_DIRTY=${BUILD_DIRTY}

WORKDIR /app

# ffmpeg: the TikTok adapter converts images to MP4 (and media ingestion uses it
# for video thumbnails). Not in python-slim — must be installed explicitly.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (cacheable layer).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code + CLI helpers (create_user, init_db, management token).
COPY app ./app
COPY scripts ./scripts

# Runtime state — mounted as volumes in compose (data/, media/, backups/).
RUN mkdir -p /app/data /app/media /app/backups

EXPOSE 8100

# Defense in depth: run as a non-root user.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
