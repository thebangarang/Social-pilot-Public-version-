"""One-off end-to-end test: publish a TEXT-ONLY post to Threads through the
real pipeline (Post -> Publication -> publish_due -> Threads adapter).

Creates the minimal ChatSession/Post/Publication rows so the post is visible in
the UI (Performance/community), then runs the scheduler's publish_due() and
reports the remote id / url / error.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Brand, ChatSession, ConnectedAccount, Operator, Post, Publication

CAPTION = (
    "Premier fil publié depuis Social Pilot — le pipeline génération → "
    "validation humaine → publication tourne de bout en bout. 👋"
)


def main() -> None:
    db = SessionLocal()
    pub_id = None
    try:
        admin = db.scalar(select(Operator).where(Operator.role == "admin").limit(1))
        brand = db.scalar(select(Brand).where(Brand.name == "Social Pilot"))
        acct = db.scalar(
            select(ConnectedAccount).where(
                ConnectedAccount.platform == "threads",
                ConnectedAccount.is_active.is_(True),
            )
        )
        if admin is None or brand is None or acct is None:
            raise SystemExit(f"missing rows: admin={bool(admin)} brand={bool(brand)} acct={bool(acct)}")
        if not acct.token_enc:
            raise SystemExit("threads account has no token")

        sess = ChatSession(owner_id=admin.id, title="Test publication Threads (texte seul)")
        db.add(sess)
        db.flush()

        post = Post(
            owner_id=admin.id,
            session_id=sess.id,
            brand_id=brand.id,
            title="Premier fil Threads",
            content={"caption": CAPTION},
            media_ids=[],
            media_type="text",
            status="compliance_ok",
        )
        db.add(post)
        db.flush()

        pub = Publication(
            post_id=post.id,
            platform="threads",
            account_id=acct.id,
            caption=CAPTION,
            media_ids=[],
            status="scheduled",
            scheduled_at=datetime.now(timezone.utc),
        )
        db.add(pub)
        db.commit()
        pub_id = pub.id
        print(f"publication created: {pub_id} (account={acct.handle})")
    finally:
        db.close()

    from app.services import pipeline

    print("publish_due ->", pipeline.publish_due())

    db = SessionLocal()
    try:
        pub = db.get(Publication, pub_id)
        print(f"status={pub.status}")
        print(f"remote_post_id={pub.remote_post_id}")
        print(f"remote_url={pub.remote_url}")
        print(f"last_error={pub.last_error}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
