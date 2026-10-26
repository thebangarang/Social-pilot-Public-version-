"""Signed public media URLs: signature helpers + unauthenticated serving."""
import uuid

from sqlalchemy import select

from app.config import PROJECT_ROOT
from app.models import MediaItem, Operator
from app.security import media_signature, verify_media_signature


def test_media_signature_roundtrip():
    sid = str(uuid.uuid4())
    sig = media_signature(sid)
    assert verify_media_signature(sid, sig)
    assert not verify_media_signature(sid, sig + "x")
    assert not verify_media_signature(sid, "")


def test_public_media_route_rejects_bad_sig(anon_client):
    mid = uuid.uuid4()
    r = anon_client.get(f"/m/{mid}", params={"sig": "deadbeef"})
    assert r.status_code == 404


def test_public_media_route_serves_file(anon_client, db):
    op = db.scalar(select(Operator).limit(1))
    mid = uuid.uuid4()
    rel = f"media/test-{mid.hex}.txt"
    path = PROJECT_ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"hello")
    item = MediaItem(
        id=mid, owner_id=op.id, kind="image", original_name="t.txt",
        stored_path=rel, mime="text/plain",
    )
    db.add(item)
    db.commit()
    try:
        r = anon_client.get(f"/m/{mid}", params={"sig": media_signature(str(mid))})
        assert r.status_code == 200
        assert r.content == b"hello"
    finally:
        path.unlink(missing_ok=True)
        db.delete(item)
        db.commit()
