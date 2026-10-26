"""Editorial calendar endpoint tests."""
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.auth import utcnow
from app.models import ChatSession, Operator, Post, Publication


def _admin(db) -> Operator:
    return db.scalar(select(Operator).where(Operator.email == "test-admin@socialpilot.local"))


def test_calendar_empty_range(client):
    resp = client.get("/calendar?start=2020-01-01&end=2020-01-31")
    assert resp.status_code == 200
    assert resp.json() == []


def test_calendar_scheduled_and_published(client, db):
    op = _admin(db)

    session = ChatSession(owner_id=op.id, title="cal test")
    db.add(session)
    db.commit()
    db.refresh(session)

    # scheduled publication tomorrow
    p1 = Post(owner_id=op.id, session_id=session.id, content={"caption": "scheduled post"})
    db.add(p1)
    db.commit()
    db.refresh(p1)
    sched = utcnow() + timedelta(days=1)
    db.add(Publication(post_id=p1.id, platform="instagram", caption="scheduled post",
                       status="scheduled", scheduled_at=sched))

    # published publication now
    p2 = Post(owner_id=op.id, session_id=session.id, content={"caption": "published post"})
    db.add(p2)
    db.commit()
    db.refresh(p2)
    now = utcnow()
    db.add(Publication(post_id=p2.id, platform="tiktok", caption="published post",
                       status="published", published_at=now))
    db.commit()

    start = (now - timedelta(days=1)).date().isoformat()
    end = (sched + timedelta(days=1)).date().isoformat()
    resp = client.get(f"/calendar?start={start}&end={end}")
    assert resp.status_code == 200
    data = resp.json()
    assert {e["status"] for e in data} == {"scheduled", "published"}
    for e in data:
        assert e["date"]
        assert e["time"]
        assert e["caption"] in ("scheduled post", "published post")

    # scheduled entry carries the future date; published entry carries today
    sched_entry = next(e for e in data if e["status"] == "scheduled")
    assert sched_entry["date"] == sched.date().isoformat()


def test_calendar_brand_filter(client, db):
    op = _admin(db)
    b = client.post("/brands", data={"name": "Filtered Brand"}).json()

    session = ChatSession(owner_id=op.id, title="brand filter")
    db.add(session)
    db.commit()
    db.refresh(session)

    now = utcnow()
    p1 = Post(owner_id=op.id, session_id=session.id, content={"caption": "unbranded"})
    db.add(p1)
    db.commit()
    db.refresh(p1)
    db.add(Publication(post_id=p1.id, platform="tiktok", caption="unbranded", status="published", published_at=now))

    p2 = Post(owner_id=op.id, session_id=session.id, content={"caption": "branded"}, brand_id=uuid.UUID(b["id"]))
    db.add(p2)
    db.commit()
    db.refresh(p2)
    db.add(Publication(post_id=p2.id, platform="instagram", caption="branded", status="published", published_at=now))
    db.commit()

    start = (now - timedelta(days=1)).date().isoformat()
    end = (now + timedelta(days=1)).date().isoformat()

    filtered = client.get(f"/calendar?start={start}&end={end}&brand_id={b['id']}").json()
    assert [e["caption"] for e in filtered] == ["branded"]

    client.delete(f"/brands/{b['id']}")
