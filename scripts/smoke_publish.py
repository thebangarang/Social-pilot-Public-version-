import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ConnectedAccount, MediaItem
from app.platforms import get_adapter

CAPTION = "Social Pilot smoke test — VPS deployment"

db = SessionLocal()
try:
    # Pick a small image for Instagram (image_url) and TikTok (image->mp4).
    img = (
        db.execute(
            select(MediaItem)
            .where(MediaItem.kind == "image")
            .order_by(MediaItem.created_at.desc())
        )
        .scalars()
        .first()
    )
    img_id = str(img.id) if img else None
    print(f"media used: {img.original_name if img else 'NONE'} ({img_id})")

    accounts = list(
        db.execute(select(ConnectedAccount).where(ConnectedAccount.is_active.is_(True))).scalars()
    )

    for platform in ("tiktok", "instagram", "bluesky"):
        acct = next((a for a in accounts if a.platform == platform), None)
        if acct is None or not acct.token_enc:
            print(f"[{platform}] SKIP — no connected account / token")
            continue
        media_ids = [img_id] if platform in ("instagram", "tiktok") and img_id else []
        pub = SimpleNamespace(
            caption=CAPTION,
            media_ids=media_ids,
            remote_post_id=None,
            remote_url=None,
        )
        adapter = get_adapter(platform)
        try:
            rid, url = adapter.publish(pub, acct)
            print(f"[{platform}] OK   remote_id={rid}")
            print(f"[{platform}]      url={url}")
        except Exception as e:  # noqa: BLE001 — report the real error
            print(f"[{platform}] FAIL {type(e).__name__}: {e}")
finally:
    db.close()
