"""SQLAlchemy engine, session factory, and declarative base (SQLite, WAL)."""
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory of a relative sqlite file path."""
    if url.startswith("sqlite:///") and url not in ("sqlite:///:memory:", "sqlite://"):
        path = url[len("sqlite:///") :]
        if not path.startswith("file:") and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


_is_sqlite = settings.database_url.startswith("sqlite")
_ensure_sqlite_dir(settings.database_url)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - db-level
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
