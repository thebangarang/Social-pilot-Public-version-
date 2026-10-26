"""Connected-account persistence helpers (reconnect-aware).

Reconnecting a platform must REFRESH the existing account row (keeping its id)
rather than delete+recreate it, otherwise publications referencing account_id
get orphaned via the FK's ondelete=SET NULL. Two rules:

- Connect = upsert by a stable identity key (ig_user_id / open_id / did): a
  matching row (active or not) is updated in place and reactivated.
- Revoke = soft-delete: set is_active=False and clear the token, but keep the
  row so its id stays valid for any historical publications.
"""
from sqlalchemy import select

from app.models import ConnectedAccount


def upsert_account(db, *, platform, handle, account_meta, token_enc,
                   refresh_token_enc=None, token_expires_at=None,
                   identity_key=None) -> ConnectedAccount:
    """Insert or refresh a connected account for the same platform identity.

    `identity_key` names the account_meta field that uniquely identifies the
    account (e.g. "ig_user_id", "open_id", "did"). When an existing row matches
    that identity, it is updated in place (same id) and reactivated.
    """
    existing = None
    if identity_key:
        identity = account_meta.get(identity_key)
        for a in db.scalars(
            select(ConnectedAccount).where(ConnectedAccount.platform == platform)
        ).all():
            if (a.account_meta or {}).get(identity_key) == identity:
                existing = a
                break

    has_default = db.scalar(
        select(ConnectedAccount).where(
            ConnectedAccount.platform == platform, ConnectedAccount.is_default.is_(True)
        )
    )

    if existing is not None:
        existing.handle = handle
        existing.account_meta = {**(existing.account_meta or {}), **account_meta}
        existing.token_enc = token_enc
        if refresh_token_enc is not None:
            existing.refresh_token_enc = refresh_token_enc
        if token_expires_at is not None:
            existing.token_expires_at = token_expires_at
        existing.is_active = True
        if has_default is None:
            existing.is_default = True
        db.commit()
        return existing

    account = ConnectedAccount(
        platform=platform,
        handle=handle,
        account_meta=account_meta,
        token_enc=token_enc,
        refresh_token_enc=refresh_token_enc,
        token_expires_at=token_expires_at,
        is_active=True,
        is_default=has_default is None,
    )
    db.add(account)
    db.commit()
    return account
