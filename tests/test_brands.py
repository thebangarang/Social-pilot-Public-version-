"""Brand management + account-brand assignment tests."""
import uuid

from app.models import Brand


def _make_brand(db, name="Brand A"):
    b = Brand(name=name)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def test_brand_crud(client):
    assert client.get("/brands").json() == []

    created = client.post("/brands", data={"name": "Client X"})
    assert created.status_code == 201
    b = created.json()
    assert b["name"] == "Client X"

    assert [x["name"] for x in client.get("/brands").json()] == ["Client X"]

    assert client.patch(f"/brands/{b['id']}", json={"name": "Client Y"}).json()["name"] == "Client Y"

    assert client.delete(f"/brands/{b['id']}").status_code == 204
    assert client.get("/brands").json() == []


def test_assign_account_to_brand(client, db):
    b = _make_brand(db, "Marque Test")
    acc = client.post("/connect/tiktok/mock").json()
    try:
        assert acc["brand_id"] is None

        assigned = client.patch(f"/connect/{acc['id']}", json={"brand_id": str(b.id)})
        assert assigned.status_code == 200
        assert assigned.json()["brand_id"] == str(b.id)

        unassigned = client.patch(f"/connect/{acc['id']}", json={"brand_id": None})
        assert unassigned.json()["brand_id"] is None

        # unknown brand rejected
        assert client.patch(f"/connect/{acc['id']}", json={"brand_id": str(uuid.uuid4())}).status_code == 404
    finally:
        client.delete(f"/connect/{acc['id']}")
        db.delete(b)
        db.commit()


def test_brand_delete_unassigns_accounts(client, db):
    b = _make_brand(db, "To Delete")
    acc = client.post("/connect/instagram/mock").json()
    try:
        client.patch(f"/connect/{acc['id']}", json={"brand_id": str(b.id)})
        assert client.delete(f"/brands/{b.id}").status_code == 204

        # account remains, now unassigned
        accounts = client.get("/connect").json()
        mine = next(x for x in accounts if x["id"] == acc["id"])
        assert mine["brand_id"] is None
    finally:
        client.delete(f"/connect/{acc['id']}")


def test_validate_tags_post_with_brand(client, monkeypatch):
    from app.api import chat as chat_api

    monkeypatch.setattr(chat_api, "generate_proposals", lambda *a, **k: [{
        "number": 1, "caption": "C", "hashtags": [], "media_prompt": "x",
    }])
    b = client.post("/brands", data={"name": "B"}).json()
    try:
        sid = client.post("/chat/sessions").json()["id"]
        pid = client.post(f"/chat/sessions/{sid}/send", json={"content": "post"}).json()["proposals"][0]["id"]
        tagged = client.post(f"/chat/proposals/{pid}/validate", json={"brand_id": b["id"]}).json()["post"]
        assert tagged["brand_id"] == b["id"]

        sid2 = client.post("/chat/sessions").json()["id"]
        pid2 = client.post(f"/chat/sessions/{sid2}/send", json={"content": "post2"}).json()["proposals"][0]["id"]
        untagged = client.post(f"/chat/proposals/{pid2}/validate", json={}).json()["post"]
        assert untagged["brand_id"] is None
    finally:
        client.delete(f"/brands/{b['id']}")


def test_brand_logo_upload_standardized(client):
    import io

    from PIL import Image

    # Non-square logo (300x100) — must be fitted (contain), never cropped, into a square.
    buf = io.BytesIO()
    Image.new("RGBA", (300, 100), (255, 0, 0, 255)).save(buf, "PNG")
    r = client.post(
        "/brands",
        data={"name": "Logo Brand"},
        files={"logo": ("logo.png", buf.getvalue(), "image/png")},
    )
    assert r.status_code == 201
    b = r.json()
    assert b["logo_path"] is not None

    logo = client.get(f"/brands/{b['id']}/logo")
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(logo.content)).size == (256, 256)  # standardized square

    client.delete(f"/brands/{b['id']}")


def test_brand_delete_unassigns_posts_and_media(client, db):
    import uuid

    from sqlalchemy import select

    from app.models import ChatSession, MediaItem, Operator, Post

    admin = db.scalar(select(Operator).where(Operator.email == "test-admin@socialpilot.local"))
    b = client.post("/brands", data={"name": "To Delete Posts"}).json()
    bid = uuid.UUID(b["id"])

    session = ChatSession(owner_id=admin.id, title="x")
    db.add(session)
    db.flush()
    post = Post(owner_id=admin.id, session_id=session.id, brand_id=bid, title="t", content={"caption": "c"})
    media = MediaItem(owner_id=admin.id, kind="image", original_name="m.png", stored_path="media/x.png", brand_id=bid)
    db.add_all([post, media])
    db.commit()

    assert client.delete(f"/brands/{b['id']}").status_code == 204

    # Content survives, just unassigned.
    db.refresh(post)
    db.refresh(media)
    assert db.get(Post, post.id) is not None and post.brand_id is None
    assert db.get(MediaItem, media.id) is not None and media.brand_id is None


def test_brand_purge_requires_exact_name_and_deletes_content(client, db):
    import uuid

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import BrandAsset, ChatSession, MediaItem, Operator, Post, StylePreset

    admin = db.scalar(select(Operator).where(Operator.email == "test-admin@socialpilot.local"))
    b = client.post("/brands", data={"name": "Purge Me"}).json()
    bid = uuid.UUID(b["id"])

    session = ChatSession(owner_id=admin.id, title="x")
    db.add(session)
    db.flush()
    post = Post(owner_id=admin.id, session_id=session.id, brand_id=bid, title="t", content={"caption": "c"})
    media = MediaItem(owner_id=admin.id, kind="image", original_name="m.png", stored_path="media/x.png", brand_id=bid)
    preset = StylePreset(id="purge-test-slug", name="p", brand_id=bid)
    asset = BrandAsset(kind="logo", filename="l.png", brand_id=bid)
    db.add_all([post, media, preset, asset])
    db.commit()
    # Capture ids as plain values up-front — accessing .id after the delete would
    # raise ObjectDeletedError on the expired ORM objects.
    post_id, media_id, preset_id, asset_id = post.id, media.id, preset.id, asset.id

    # Wrong confirmation -> rejected, nothing deleted.
    assert client.delete(f"/brands/{b['id']}/purge?confirm=wrong").status_code == 400
    assert db.scalar(select(Post).where(Post.id == post_id)) is not None

    # Exact name -> 204, brand + content gone.
    assert client.delete(f"/brands/{b['id']}/purge?confirm=Purge%20Me").status_code == 204
    check = SessionLocal()  # fresh session — the test session's identity map would return stale objects
    try:
        assert check.get(Post, post_id) is None
        assert check.get(MediaItem, media_id) is None
        assert check.get(StylePreset, preset_id) is None
        assert check.get(BrandAsset, asset_id) is None
    finally:
        check.close()
    assert b["id"] not in {x["id"] for x in client.get("/brands").json()}


def test_branding_brand_filter(client, db):
    b = _make_brand(db, "Branding Brand")
    created_ids = []
    try:
        r1 = client.post("/branding", json={"kind": "logo", "filename": "logo.png", "brand_id": str(b.id)}).json()
        created_ids.append(r1["id"])
        r2 = client.post("/branding", json={"kind": "color", "filename": "color", "notes": "#fff"}).json()
        created_ids.append(r2["id"])

        branded = client.get(f"/branding?brand_id={b.id}").json()
        assert [a["id"] for a in branded] == [r1["id"]]
        assert branded[0]["brand_id"] == str(b.id)
    finally:
        for aid in created_ids:
            client.delete(f"/branding/{aid}")
        db.delete(b)
        db.commit()
