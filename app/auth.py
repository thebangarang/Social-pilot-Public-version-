"""Authentication & session logic (pure helpers + small DB writes)."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import pyotp
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DBSession

from app.models import LoginAttempt, Operator, UserSession
from app.security import decrypt_secret, hash_password, verify_password

SESSION_TTL = timedelta(days=7)
CHALLENGE_TTL_SECONDS = 300
COOKIE_NAME = "sp_session"
TOTP_ISSUER = "Social Pilot"

MAX_LOGIN_FAILURES = 5
LOGIN_LOCKOUT = timedelta(minutes=15)

# A precomputed hash used to equalize login timing for unknown/inactive accounts
# (so they don't return faster than a real scrypt verify — no email enumeration).
_DUMMY_HASH = hash_password("timing-equalization-dummy")


def utcnow() -> datetime:
    """Naive UTC now — SQLite stores/returns naive datetimes, so we compare
    naive-against-naive (avoids offset-aware vs offset-naive TypeError)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- password / identity ------------------------------------------------------


def authenticate(db: DBSession, email: str, password: str) -> Operator | None:
    op = db.scalar(
        select(Operator).where(func.lower(Operator.email) == email.strip().lower())
    )
    if op is None or not op.is_active:
        # Equalize timing: run a scrypt verify even for unknown/inactive accounts.
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, op.password_hash):
        return None
    return op


def is_locked_out(db: DBSession, email: str) -> bool:
    """True if this email has >= MAX_LOGIN_FAILURES failures in the last window."""
    since = utcnow() - LOGIN_LOCKOUT
    fails = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.email == email.strip().lower(),
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at >= since,
        )
    ) or 0
    return fails >= MAX_LOGIN_FAILURES


def record_attempt(db: DBSession, email: str, ip: str, success: bool) -> None:
    """Append a login attempt to the audit log (no commit — caller decides)."""
    db.add(LoginAttempt(email=email.strip().lower(), ip=ip, success=success))


# --- sessions -----------------------------------------------------------------


def create_session(db: DBSession, operator: Operator) -> str:
    """Create a server-side session; returns the raw token (goes in the cookie)."""
    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            operator_id=operator.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=utcnow() + SESSION_TTL,
        )
    )
    db.commit()
    return token


def get_session(db: DBSession, token: str | None) -> UserSession | None:
    if not token:
        return None
    sess = db.scalar(
        select(UserSession).where(
            UserSession.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    if sess is None or sess.expires_at < utcnow():
        return None
    return sess


def destroy_session(db: DBSession, token: str | None) -> None:
    sess = get_session(db, token)
    if sess is not None:
        db.delete(sess)
        db.commit()


def purge_expired_sessions() -> int:
    """Delete expired sessions (own session; called from the scheduler)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        res = db.execute(delete(UserSession).where(UserSession.expires_at < utcnow()))
        db.commit()
        return res.rowcount
    finally:
        db.close()


def purge_old_login_attempts(days: int = 30) -> int:
    """Delete login-attempt audit rows older than `days` (own session)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        res = db.execute(
            delete(LoginAttempt).where(
                LoginAttempt.created_at < utcnow() - timedelta(days=days)
            )
        )
        db.commit()
        return res.rowcount
    finally:
        db.close()


# --- 2FA challenge (signed, short-lived; issued after password passes) --------


def _sign(payload: str) -> str:
    from app.config import settings

    return hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_challenge(operator_id) -> str:
    """Signed token carrying the operator id and an expiry (no session yet)."""
    expiry = int(datetime.now(timezone.utc).timestamp()) + CHALLENGE_TTL_SECONDS
    payload = f"{operator_id}.{expiry}"
    return f"{payload}.{_sign(payload)}"


def verify_challenge(token: str) -> str | None:
    """Return the operator id if the challenge is valid and unexpired, else None."""
    if not token:
        return None
    payload, _, sig = token.rpartition(".")
    if not payload or not hmac.compare_digest(sig.encode(), _sign(payload).encode()):
        return None
    try:
        op_id, expiry = payload.split(".", 1)
    except ValueError:
        return None
    if int(expiry) < int(datetime.now(timezone.utc).timestamp()):
        return None
    return op_id


# --- TOTP (Google Authenticator) ----------------------------------------------


def totp_secret_plain(operator: Operator) -> str | None:
    """Decrypt an operator's stored TOTP secret, if any."""
    if not operator.totp_secret:
        return None
    return decrypt_secret(operator.totp_secret)


def totp_uri(email: str, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)
