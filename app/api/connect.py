"""Social platform connections (OAuth stub + mock adapter for the demo path).

Real OAuth (TikTok Content Posting / Instagram Login / Facebook Login) is wired
later once the apps are registered and reviewed. Until then, the mock adapter
drives the full connect → list → revoke flow so the UI and persistence are
exercised end-to-end. See docs/platform-apis.md and ADR-007.
"""
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_current_operator, get_db, require_admin
from app.models import Brand, ConnectedAccount, Operator
from app.schemas import BlueskyConnectIn, ConnectedAccountBrandIn, ConnectedAccountOut
from app.security import encrypt_secret
from app.services.accounts import upsert_account

router = APIRouter(prefix="/connect", tags=["connect"])

PLATFORMS = ["tiktok", "instagram", "facebook", "linkedin", "x", "youtube", "threads", "pinterest", "google", "bluesky"]


@router.get("", response_model=list[ConnectedAccountOut])
def list_accounts(db: Session = Depends(get_db), _: Operator = Depends(get_current_operator)):
    # Only active accounts — soft-deleted (disconnected) rows are kept for
    # publication references but hidden from the UI.
    return db.scalars(
        select(ConnectedAccount)
        .where(ConnectedAccount.is_active.is_(True))
        .order_by(ConnectedAccount.created_at)
    ).all()


@router.post("/{platform}/mock", response_model=ConnectedAccountOut, status_code=status.HTTP_201_CREATED)
def mock_connect(
    platform: str,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    """Simulate an OAuth connection (demo path). Real OAuth replaces this later."""
    if platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail="Plateforme inconnue")
    handle = f"{platform}_{secrets.token_hex(3)}"
    has_default = db.scalar(
        select(ConnectedAccount).where(
            ConnectedAccount.platform == platform, ConnectedAccount.is_default.is_(True)
        )
    )
    account = ConnectedAccount(
        platform=platform,
        handle=handle,
        account_meta={"mock": True, "display": f"@{handle}"},
        token_enc=encrypt_secret(secrets.token_urlsafe(24)),
        is_active=True,
        is_default=has_default is None,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.post("/{platform}", response_model=ConnectedAccountOut, status_code=status.HTTP_201_CREATED)
def real_connect(
    platform: str,
    payload: BlueskyConnectIn,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    """Connect a platform for real (BYOK). Currently only Bluesky (App Password)."""
    if platform != "bluesky":
        raise HTTPException(
            status_code=501,
            detail="Driver réel pas encore implémenté pour cette plateforme (mock dispo via /connect/{platform}/mock).",
        )
    import httpx

    from app.platforms.bluesky import PDS

    r = httpx.post(
        f"{PDS}/xrpc/com.atproto.server.createSession",
        json={"identifier": payload.handle, "password": payload.app_password},
        timeout=30.0,
    )
    if r.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="Identifiant ou App Password invalide.")
    r.raise_for_status()
    data = r.json()
    handle = (data.get("handle") or payload.handle).strip()
    did = data.get("did", "")
    account = upsert_account(
        db,
        platform="bluesky",
        handle=handle,
        account_meta={"did": did, "source": "app_password", "display": f"@{handle}"},
        token_enc=encrypt_secret(payload.app_password),
        identity_key="did",
    )
    db.refresh(account)
    return account


@router.post("/{account_id}/default", response_model=ConnectedAccountOut)
def set_default_account(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    """Mark one account as the default for its platform (for publishing)."""
    account = db.get(ConnectedAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Compte introuvable")
    for other in db.scalars(
        select(ConnectedAccount).where(
            ConnectedAccount.platform == account.platform, ConnectedAccount.is_default.is_(True)
        )
    ).all():
        other.is_default = False
    account.is_default = True
    db.commit()
    db.refresh(account)
    return account


@router.patch("/{account_id}", response_model=ConnectedAccountOut)
def assign_brand(
    account_id: uuid.UUID,
    payload: ConnectedAccountBrandIn,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    """Assign (or unassign) a social account to a brand."""
    account = db.get(ConnectedAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Compte introuvable")
    if payload.brand_id is not None and db.get(Brand, payload.brand_id) is None:
        raise HTTPException(status_code=404, detail="Marque introuvable")
    account.brand_id = payload.brand_id
    db.commit()
    db.refresh(account)
    return account


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_account(
    account_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    account = db.get(ConnectedAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Compte introuvable")
    # Soft-delete: keep the row (publications reference its id) but deactivate
    # and clear the credential. Reconnecting upserts this same row back active.
    account.is_active = False
    account.is_default = False
    account.token_enc = None
    account.refresh_token_enc = None
    account.token_expires_at = None
    db.commit()
