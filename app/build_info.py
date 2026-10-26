"""Build identity — which commit a running instance was built from.

Why this exists: an instance that cannot say what it is running makes drift
invisible. The production Social Pilot image was built 2026-09-20 while the
repository moved on to 2026-09-24; nothing on the instance could report that, and
the only way to find out was to notice a missing endpoint.

Resolution order:
1. ``SP_BUILD_SHA`` / ``SP_BUILD_DATE`` / ``SP_BUILD_DIRTY`` — stamped into the
   image by the Dockerfile build args. Authoritative for a built image.
2. The git checkout — when running from source (dev, tests).
3. ``unknown`` — reported honestly, never guessed.

The value is exposed on the authenticated management endpoint only
(``/api/v1/management/health``), which is what the Command Center polls. The
public ``/health`` stays bare by design.
"""
from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str | None:
    """Run a git command in the source tree; None when git or the repo is absent."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(_SOURCE_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


@lru_cache(maxsize=1)
def build_info() -> dict:
    """Return the build identity as a JSON-serialisable dict."""
    sha = os.getenv("SP_BUILD_SHA", "").strip()
    source = "image"
    if not sha:
        sha = _git("rev-parse", "HEAD") or ""
        source = "git" if sha else "unknown"

    stamped_date = os.getenv("SP_BUILD_DATE", "").strip()
    built_at = stamped_date or (_git("log", "-1", "--format=%cI") or "") or "unknown"

    stamped_dirty = os.getenv("SP_BUILD_DIRTY", "").strip()
    if stamped_dirty:
        dirty: bool | None = stamped_dirty.lower() not in {"0", "false", "no"}
    elif source == "git":
        dirty = bool(_git("status", "--porcelain"))
    else:
        dirty = None  # unknown, not False — an unstamped image reports no claim

    return {
        "commit": sha or "unknown",
        "commit_short": sha[:7] if sha else "unknown",
        "built_at": built_at,
        "dirty": dirty,
        "source": source,
    }
