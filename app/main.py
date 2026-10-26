"""Social Pilot — FastAPI application entrypoint."""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.api import auth, branding, brands, calendar, chat, community, connect, health, llm, management, media, oauth, performance, posts, presets, theme, usage, users, webhooks
from app.api.presets import BUILTIN_PRESETS
from app.config import PROJECT_ROOT, settings
from app.database import Base, SessionLocal, engine
from app.deps import get_current_operator
from app.models import StylePreset, Theme

APP_VERSION = "0.1.0"
logger = logging.getLogger("social_pilot.scheduler")

STATIC_DIR = PROJECT_ROOT / "app" / "static"

DEFAULT_THEMES = [
    Theme(
        id="neutral", name="Neutre",
        bg="#0f1115", surface="#171a21", border="#262a33",
        fg="#e6e8ee", muted="#8a8f9c", accent="#5b8cff", accent_2="#8a5bff",
        success="#3fbf7f", warning="#e0a33c", danger="#e05b5b",
        is_active=True,
    ),
    Theme(
        id="graphite", name="Graphite",
        bg="#101316", surface="#1a1e23", border="#2a3037",
        fg="#e8eaed", muted="#8b919a", accent="#7ee0c3", accent_2="#4aa8ff",
        success="#43c98c", warning="#e3a93f", danger="#e2635c",
    ),
    Theme(
        id="ivory", name="Ivoire",
        bg="#f6f5f1", surface="#ffffff", border="#e3e0d8",
        fg="#1d1f26", muted="#6b6e78", accent="#2f6fed", accent_2="#8a5bff",
        success="#2f9e6e", warning="#c68a2e", danger="#cf4f4a",
    ),
]


def _seed_themes() -> None:
    db = SessionLocal()
    try:
        if db.scalar(select(Theme).limit(1)) is None:
            db.add_all(DEFAULT_THEMES)
            db.commit()
    finally:
        db.close()


def _seed_presets() -> None:
    db = SessionLocal()
    try:
        if db.scalar(select(StylePreset).limit(1)) is None:
            db.add_all(BUILTIN_PRESETS)
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 3: create tables on startup. Replace with Alembic migrations before
    # onboarding a real client (additive-only; never drops).
    Base.metadata.create_all(bind=engine)
    _seed_themes()
    _seed_presets()
    scheduler_task = asyncio.create_task(_scheduler_loop())
    insights_task = asyncio.create_task(_insights_loop())
    try:
        yield
    finally:
        scheduler_task.cancel()
        insights_task.cancel()


async def _scheduler_loop():
    """Publish scheduled posts when their time arrives (every 30s)."""
    from app.auth import purge_expired_sessions, purge_old_login_attempts
    from app.services import pipeline

    last_purge = 0.0
    while True:
        try:
            # Run the sync DB/network work off the event loop so a slow publish
            # never blocks request handling.
            result = await asyncio.to_thread(pipeline.publish_due)
            if result and (result.get("published") or result.get("failed")):
                logger.info(
                    "scheduler tick: published=%s failed=%s",
                    result["published"],
                    result["failed"],
                )
        except Exception:  # noqa: BLE001 — keep the loop alive
            logger.exception("scheduler tick failed")

        # Opportunistic cleanup: expired sessions + old login-attempt audit rows.
        if time.time() - last_purge >= 3600:
            try:
                await asyncio.to_thread(purge_expired_sessions)
                await asyncio.to_thread(purge_old_login_attempts)
            except Exception:  # noqa: BLE001
                logger.exception("session/audit purge failed")
            last_purge = time.time()

        await asyncio.sleep(30)


async def _insights_loop():
    """Poll post insights periodically (every 10 min) for the Performance tab."""
    from app.services import metrics

    while True:
        try:
            result = await asyncio.to_thread(metrics.poll_insights)
            if result and result.get("polled"):
                logger.info(
                    "insights tick: polled=%s snapshots=%s",
                    result["polled"],
                    result["snapshots"],
                )
        except Exception:  # noqa: BLE001 — keep the loop alive
            logger.exception("insights tick failed")
        await asyncio.sleep(600)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Social Pilot",
        version=APP_VERSION,
        description="Whitelabel, self-hosted social media operating tool.",
        lifespan=lifespan,
    )

    # Public: liveness + login page + OAuth callbacks (platform redirects) +
    # signed media URLs (platform fetchers).
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(oauth.router)
    app.include_router(media.public_router)
    app.include_router(webhooks.router)

    # Instance management / telemetry API (token-auth, no operator session).
    # This is how the command center polls each client instance. Telemetry only.
    app.include_router(management.router)

    # Operator-protected control API (session cookie required).
    _op = [Depends(get_current_operator)]
    app.include_router(users.router, dependencies=_op)  # admin enforced internally
    app.include_router(theme.router, dependencies=_op)
    app.include_router(connect.router, dependencies=_op)  # writes admin-only
    app.include_router(brands.router, dependencies=_op)  # writes admin-only
    app.include_router(llm.router, dependencies=_op)  # writes admin-only
    app.include_router(usage.router, dependencies=_op)
    app.include_router(chat.router, dependencies=_op)
    app.include_router(media.router, dependencies=_op)
    app.include_router(branding.router, dependencies=_op)
    app.include_router(presets.router, dependencies=_op)
    app.include_router(posts.router, dependencies=_op)
    app.include_router(performance.router, dependencies=_op)
    app.include_router(community.router, dependencies=_op)
    app.include_router(calendar.router, dependencies=_op)

    # Static assets + SPA entry points (JS handles its own auth redirect).
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(STATIC_DIR / "login.html")

    return app


app = create_app()
