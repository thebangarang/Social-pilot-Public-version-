"""Test configuration — runs against a dedicated SQLite test database.

The path is anchored on this file's own location, never on `./`. The suite is run from two places — from
the project root by hand, and from the foundation root by the declared-check runner
(`projects/social-pilot/.venv/bin/python -m pytest projects/social-pilot/tests/…`). With a relative path the
second one wrote `test.db` into `the repository root, `, i.e. a stray database in the foundation's root, once
per run. A test that creates files wherever it was started from is worse than a slow test: it is a finding
generator.
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Which database file this run uses. The default is one file per project, which is fine for one run at a time
# and wrong for two: the session fixture drops and recreates every table, so a hand run and the machine's own
# declared check (`breakers-hold`, which the audit runs every 15 minutes) collide on the same file — the loser
# reports "no such table: operators" for every test that touches the database. Measured on 2026-10-06: 86
# failures, all of them auth, caused by exactly that.
#
# The name is validated in `tests/db_path.py`, and it has to be: this value selects the file the session
# fixture DROPS, so an unvalidated one lets the environment aim that at any database on the machine.
from tests.db_path import session_db_url  # noqa: E402

os.environ["SP_DATABASE_URL"] = session_db_url(PROJECT_ROOT, os.environ.get("TEST_DB_NAME"))
os.environ["SP_SECRET_KEY"] = "test-secret-key-0123456789abcdef0123456789abcdef"
os.environ["SP_ENV"] = "test"
# The AI-marking key (Article 50(2)). A deployment value, so the suite states its own: a value that is
# obviously a test value and is NOT the one any installation uses. It was the deployment's own value for one
# revision, which put the secret back in the repository through the test suite — the same finding as the
# literal it replaced, arriving by another door. Nothing here reads a real mark, so any value does.
os.environ["SP_AI_MARKING_KEY"] = "424242"

from sqlalchemy import select  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Operator  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.drop_all(bind=engine)  # clean slate (test.db persists between runs)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="session", autouse=True)
def _test_admin(_database):
    """A session-scoped admin so every test client can authenticate."""
    s = SessionLocal()
    try:
        if s.scalar(select(Operator).where(Operator.email == "test-admin@socialpilot.local")) is None:
            s.add(
                Operator(
                    email="test-admin@socialpilot.local",
                    name="Test Admin",
                    role="admin",
                    password_hash=hash_password("adminpass123"),
                )
            )
            s.commit()
    finally:
        s.close()


@pytest.fixture()
def client():
    """Authenticated as the test admin."""
    with TestClient(app) as c:
        c.post(
            "/auth/login",
            json={"email": "test-admin@socialpilot.local", "password": "adminpass123"},
        )
        yield c


@pytest.fixture()
def anon_client():
    """No session — for 401/403 and the login flow itself."""
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    """A direct SQLAlchemy session (same engine as the app)."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def make_operator(db):
    """Factory creating a user directly in the DB (bypasses the admin API)."""

    def _make(email="user@socialpilot.local", password="password123", role="operator"):
        op = Operator(
            email=email,
            name=email.split("@")[0],
            role=role,
            password_hash=hash_password(password),
        )
        db.add(op)
        db.commit()
        db.refresh(op)
        return op

    return _make
