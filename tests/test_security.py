"""Security hardening tests — login lockout + audit + session purge."""
from datetime import timedelta

from sqlalchemy import func, select

from app.auth import utcnow
from app.models import LoginAttempt, Operator, UserSession


def test_login_lockout_after_failures(make_operator, anon_client):
    make_operator("lock@socialpilot.local", "password123")
    for _ in range(5):
        assert anon_client.post(
            "/auth/login", json={"email": "lock@socialpilot.local", "password": "wrong"}
        ).status_code == 401
    # Now locked out even with the correct password.
    r = anon_client.post(
        "/auth/login", json={"email": "lock@socialpilot.local", "password": "password123"}
    )
    assert r.status_code == 429


def test_login_not_locked_under_threshold(make_operator, anon_client):
    make_operator("ok@socialpilot.local", "password123")
    for _ in range(4):
        anon_client.post(
            "/auth/login", json={"email": "ok@socialpilot.local", "password": "wrong"}
        )
    assert anon_client.post(
        "/auth/login", json={"email": "ok@socialpilot.local", "password": "password123"}
    ).status_code == 200


def test_login_attempts_audited(make_operator, anon_client, db):
    make_operator("audit@socialpilot.local", "password123")
    anon_client.post("/auth/login", json={"email": "audit@socialpilot.local", "password": "wrong"})

    attempts = db.scalars(
        select(LoginAttempt).where(LoginAttempt.email == "audit@socialpilot.local")
    ).all()
    assert len(attempts) == 1
    assert attempts[0].success is False
    assert attempts[0].ip


def test_session_purge(make_operator, db):
    from app.auth import purge_expired_sessions

    op = make_operator("purge@socialpilot.local", "password123")
    db.add(UserSession(
        operator_id=op.id,
        token_hash="expired-" + "0" * 56,
        expires_at=utcnow() - timedelta(days=1),
    ))
    db.commit()

    deleted = purge_expired_sessions()
    assert deleted >= 1

    remaining = db.scalar(
        select(func.count()).select_from(UserSession).where(
            UserSession.token_hash == "expired-" + "0" * 56
        )
    )
    assert remaining == 0
