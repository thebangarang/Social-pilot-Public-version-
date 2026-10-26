"""Reconnect-aware account persistence: upsert keeps the row id, revoke soft-deletes."""
from sqlalchemy import select

from app.models import ConnectedAccount
from app.security import encrypt_secret
from app.services.accounts import upsert_account


def _cleanup(db, platform):
    for a in db.scalars(select(ConnectedAccount).where(ConnectedAccount.platform == platform)).all():
        db.delete(a)
    db.commit()


def test_upsert_account_refreshes_in_place(db):
    a1 = upsert_account(
        db, platform="instagram", handle="h1",
        account_meta={"ig_user_id": "123", "source": "oauth"},
        token_enc=encrypt_secret("t1"), identity_key="ig_user_id",
    )
    a2 = upsert_account(
        db, platform="instagram", handle="h1",
        account_meta={"ig_user_id": "123", "source": "oauth"},
        token_enc=encrypt_secret("t2"), identity_key="ig_user_id",
    )
    assert a1.id == a2.id  # reconnect refreshes the same row, no duplicate
    assert a2.is_active is True

    a3 = upsert_account(
        db, platform="instagram", handle="h2",
        account_meta={"ig_user_id": "456", "source": "oauth"},
        token_enc=encrypt_secret("t3"), identity_key="ig_user_id",
    )
    assert a3.id != a1.id  # different identity → a new row
    _cleanup(db, "instagram")


def test_revoke_soft_deletes_and_reconnect_keeps_id(client, db):
    acct = upsert_account(
        db, platform="bluesky", handle="dummy.bsky.social",
        account_meta={"did": "did:plc:abc", "source": "app_password"},
        token_enc=encrypt_secret("pw"), identity_key="did",
    )
    acct_id = acct.id

    assert client.delete(f"/connect/{acct_id}").status_code == 204

    db.expire_all()
    acct = db.get(ConnectedAccount, acct_id)
    assert acct is not None  # row survives (publications keep their account_id)
    assert acct.is_active is False
    assert acct.token_enc is None

    acct2 = upsert_account(
        db, platform="bluesky", handle="dummy.bsky.social",
        account_meta={"did": "did:plc:abc", "source": "app_password"},
        token_enc=encrypt_secret("pw2"), identity_key="did",
    )
    assert acct2.id == acct_id  # reconnect reactivates the same row
    assert acct2.is_active is True
    _cleanup(db, "bluesky")
