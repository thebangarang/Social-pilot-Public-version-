"""Meta platform webhooks (deauthorization + data deletion) — public, unauthenticated.

Meta pings these server-to-server with a `signed_request` (HMAC-SHA256 over the
base64url payload, keyed by the app secret). They are required by the Threads use
case (and by Meta's data-deletion policy): the deauthorize callback must return
200, and the data-deletion callback must return `{url, confirmation_code}` or
Meta flags the endpoint as non-functional.

The endpoints are deliberately outside operator auth (Meta has no session); the
signature is the proof of origin, so a forged request without the app secret is
rejected with 400.
"""
import base64
import hashlib
import hmac
import json
import logging
import secrets

from fastapi import APIRouter, Form, HTTPException
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import ConnectedAccount

logger = logging.getLogger("social_pilot.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def parse_signed_request(signed_request: str, secret: str) -> dict:
    """Verify a Meta signed_request and return its decoded payload."""
    if not signed_request or "." not in signed_request:
        raise ValueError("signed_request malformed")
    sig, payload = signed_request.split(".", 1)
    expected = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    if not hmac.compare_digest(sig, expected):
        raise ValueError("signed_request signature mismatch")
    return json.loads(_b64url_decode(payload))


def _deactivate_threads(user_id: str) -> None:
    """Soft-delete the connected Threads account matching this identity.

    Keeps the row (publications reference its id) but clears the credential, the
    same semantics as an operator revoke — see services/accounts.py.
    """
    db = SessionLocal()
    try:
        for a in db.scalars(
            select(ConnectedAccount).where(
                ConnectedAccount.platform == "threads",
                ConnectedAccount.is_active.is_(True),
            )
        ).all():
            if (a.account_meta or {}).get("threads_user_id") == user_id:
                a.is_active = False
                a.is_default = False
                a.token_enc = None
                a.refresh_token_enc = None
                a.token_expires_at = None
                db.commit()
                logger.info("webhook: deactivated threads account %s", user_id)
                break
    finally:
        db.close()


@router.post("/threads/deauthorize")
def threads_deauthorize(signed_request: str = Form(...)):
    """Meta pings this when a user deauthorizes (uninstalls) the app."""
    try:
        data = parse_signed_request(signed_request, settings.threads_client_secret)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    user_id = str(data.get("user_id") or "")
    if user_id:
        _deactivate_threads(user_id)
    return {"status": "ok"}


@router.post("/threads/data-deletion")
def threads_data_deletion(signed_request: str = Form(...)):
    """Meta pings this when a user requests deletion of their data.

    Must return JSON {url, confirmation_code}: a status URL and an alphanumeric
    confirmation code, per Meta's Data Deletion Request Callback contract.
    """
    try:
        data = parse_signed_request(signed_request, settings.threads_client_secret)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    user_id = str(data.get("user_id") or "")
    confirmation = secrets.token_urlsafe(16)
    if user_id:
        _deactivate_threads(user_id)
    return {
        "url": f"{settings.public_base_url.rstrip('/')}/data-deletion?code={confirmation}",
        "confirmation_code": confirmation,
    }
