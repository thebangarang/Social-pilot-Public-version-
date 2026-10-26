#!/usr/bin/env bash
# Social Pilot — production start (TLS terminated by reverse proxy in front).
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/uvicorn app.main:app --host "${SP_HOST:-127.0.0.1}" --port "${SP_PORT:-8100}"
