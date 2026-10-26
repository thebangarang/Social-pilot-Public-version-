"""Post production pipeline tests (media gen + compliance are monkeypatched)."""
import io

from PIL import Image


def _fake_image_bytes():
    """A generated image big enough to carry the Article 50(2) mark.

    This was 4x3 until the mark existed, and the watermark library refuses anything that small ("can embed
    0.0kb, the watermark is 0.175kb"): every test asset would have been stored unmarked and the marking path
    would never have run in the suite. 512x512 is the smallest size that marks in about a second, which keeps
    the suite quick while exercising the real code.
    """
    img = Image.new("RGB", (512, 512), (28, 34, 48))
    for i in range(0, 512, 16):
        for j in range(0, 512, 16):
            img.putpixel((i, j), (150 + (i % 90), 170, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _validated_post(client, monkeypatch):
    """Create a session + send + validate → returns the created post id."""
    from app.api import chat as chat_api

    monkeypatch.setattr(chat_api, "generate_proposals", lambda *a, **k: [{
        "number": 1,
        "caption": "Caption test",
        "hashtags": ["#test"],
        "hook": "H",
        "cta": "C",
        "media_prompt": "une image de test",
    }])
    sid = client.post("/chat/sessions").json()["id"]
    pid = client.post(f"/chat/sessions/{sid}/send", json={"content": "post", "preset_id": None}).json()["proposals"][0]["id"]
    r = client.post(f"/chat/proposals/{pid}/validate")
    assert r.status_code == 200
    return r.json()["post"]["id"]


def test_validate_creates_post(client, monkeypatch):
    post_id = _validated_post(client, monkeypatch)
    post = client.get(f"/posts/{post_id}").json()
    assert post["status"] == "concept_validated"
    assert post["content"]["caption"] == "Caption test"
    assert post["media_ids"] == []


def test_create_publications_links_connected_account(client, db, monkeypatch):
    """create_publications links each publication to the platform's account."""
    from app.models import ConnectedAccount
    from app.security import encrypt_secret
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())
    monkeypatch.setattr(pipeline, "chat_completion", lambda *a, **k: {"content": '{"verdict": "pass", "risks": []}'})

    acct = ConnectedAccount(
        platform="youtube",
        handle="fb-dummy",
        token_enc=encrypt_secret("tok"),
        is_active=True,
        is_default=True,
    )
    db.add(acct)
    db.commit()
    db.refresh(acct)

    post_id = _validated_post(client, monkeypatch)
    client.post(f"/posts/{post_id}/generate-media")
    client.post(f"/posts/{post_id}/compliance")
    r = client.post(f"/posts/{post_id}/publications", json={"platforms": ["youtube"]})
    assert r.status_code == 200
    assert r.json()[0]["account_id"] == str(acct.id)


def test_delete_publication_requires_typed_confirm(client, monkeypatch):
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())
    monkeypatch.setattr(pipeline, "chat_completion", lambda *a, **k: {"content": '{"verdict": "pass", "risks": []}'})

    post_id = _validated_post(client, monkeypatch)
    client.post(f"/posts/{post_id}/generate-media")
    client.post(f"/posts/{post_id}/compliance")
    pub_ids = [p["id"] for p in client.post(
        f"/posts/{post_id}/publications", json={"platforms": ["youtube", "google"]}
    ).json()]

    # wrong confirm word (lowercase) → refused
    r = client.delete(f"/posts/{post_id}/publications/{pub_ids[0]}", params={"confirm": "supprimer"})
    assert r.status_code == 400

    # correct confirm word → one publication deleted, the other remains
    r = client.delete(f"/posts/{post_id}/publications/{pub_ids[0]}", params={"confirm": "SUPPRIMER"})
    assert r.status_code == 200
    assert r.json()["platform_deleted"] is True  # mock: no platform to delete
    remaining = client.get(f"/posts/{post_id}/publications").json()
    assert [p["id"] for p in remaining] == [pub_ids[1]]

    # English confirmation word → also accepted
    r = client.delete(f"/posts/{post_id}/publications/{pub_ids[1]}", params={"confirm": "DELETE"})
    assert r.status_code == 200


def test_delete_publication_calls_platform_delete(client, db, monkeypatch):
    import uuid

    from app.models import ConnectedAccount, Publication
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())
    monkeypatch.setattr(pipeline, "chat_completion", lambda *a, **k: {"content": '{"verdict": "pass", "risks": []}'})

    post_id = _validated_post(client, monkeypatch)
    client.post(f"/posts/{post_id}/generate-media")
    client.post(f"/posts/{post_id}/compliance")
    pub_id = client.post(f"/posts/{post_id}/publications", json={"platforms": ["youtube"]}).json()[0]["id"]

    # Attach a remote id + account so the endpoint attempts a platform delete.
    acct = ConnectedAccount(platform="youtube", handle="yt", is_active=True)
    db.add(acct); db.commit(); db.refresh(acct)
    pub = db.get(Publication, uuid.UUID(pub_id))
    pub.remote_post_id = "remote-123"
    pub.account_id = acct.id
    db.commit()

    deleted = {"called": False}
    class _Fake:
        def delete(self, publication, account=None):
            deleted["called"] = True
            assert publication.remote_post_id == "remote-123"
    monkeypatch.setattr("app.api.posts.get_adapter", lambda platform: _Fake())

    r = client.delete(f"/posts/{post_id}/publications/{pub_id}", params={"confirm": "SUPPRIMER"})
    assert r.status_code == 200
    assert deleted["called"] is True
    assert r.json()["platform_deleted"] is True


def test_full_pipeline(client, monkeypatch):
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())
    monkeypatch.setattr(pipeline, "chat_completion", lambda *a, **k: {"content": '{"verdict": "pass", "risks": []}'})

    post_id = _validated_post(client, monkeypatch)

    r = client.post(f"/posts/{post_id}/generate-media")
    assert r.status_code == 200
    assert r.json()["status"] == "media_ready"
    assert len(r.json()["media_ids"]) == 1

    r = client.post(f"/posts/{post_id}/compliance")
    assert r.status_code == 200
    assert r.json()["status"] == "compliance_ok"
    assert r.json()["compliance"]["verdict"] == "pass"

    r = client.post(f"/posts/{post_id}/publications", json={"platforms": ["youtube", "google"]})
    assert r.status_code == 200
    assert len(r.json()) == 2

    assert client.post(f"/posts/{post_id}/approve").json()["status"] == "approved"

    # publish requires explicit confirm (double approval)
    assert client.post(f"/posts/{post_id}/publish", json={"confirm": False}).status_code == 400

    r = client.post(f"/posts/{post_id}/publish", json={"confirm": True})
    assert r.status_code == 200
    pubs = r.json()
    assert all(p["status"] == "published" for p in pubs)
    assert all(p["remote_url"] for p in pubs)


def test_publish_scheduled(client, monkeypatch):
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())
    monkeypatch.setattr(pipeline, "chat_completion", lambda *a, **k: {"content": '{"verdict": "pass", "risks": []}'})

    post_id = _validated_post(client, monkeypatch)
    client.post(f"/posts/{post_id}/generate-media")
    client.post(f"/posts/{post_id}/compliance")
    client.post(f"/posts/{post_id}/publications", json={"platforms": ["tiktok"]})
    client.post(f"/posts/{post_id}/approve")
    r = client.post(f"/posts/{post_id}/publish", json={"confirm": True, "scheduled_at": "2030-01-01T10:00:00Z"})
    assert r.status_code == 200
    assert r.json()[0]["status"] == "scheduled"
    assert client.get(f"/posts/{post_id}").json()["status"] == "scheduled"


def test_guard_requires_steps_in_order(client, monkeypatch):
    post_id = _validated_post(client, monkeypatch)
    # compliance before media → 400
    assert client.post(f"/posts/{post_id}/compliance").status_code == 400
    # publications before media + compliance → 400
    assert client.post(f"/posts/{post_id}/publications", json={"platforms": ["tiktok"]}).status_code == 400
    # publish before approve → 400
    assert client.post(f"/posts/{post_id}/publish", json={"confirm": True}).status_code == 400


def test_post_out_tolerates_null_source_media():
    """Old posts (pre-migration) can carry NULL source_media_ids without breaking the list."""
    import datetime
    import uuid

    from app.schemas import PostOut

    p = PostOut(
        id=uuid.uuid4(), owner_id=uuid.uuid4(), session_id=uuid.uuid4(),
        proposal_id=None, title="t", content={}, media_ids=[],
        source_media_ids=None, branding_usage=None, compliance=None,
        status="concept_validated",
        created_at=datetime.datetime.utcnow(), updated_at=datetime.datetime.utcnow(),
    )
    assert p.source_media_ids is None


def test_scheduler_publishes_due_and_is_idempotent(client, monkeypatch):
    """publish_due() fires due publications once; a second tick is a no-op."""
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())
    monkeypatch.setattr(pipeline, "chat_completion", lambda *a, **k: {"content": '{"verdict": "pass", "risks": []}'})

    post_id = _validated_post(client, monkeypatch)
    client.post(f"/posts/{post_id}/generate-media")
    client.post(f"/posts/{post_id}/compliance")
    client.post(f"/posts/{post_id}/publications", json={"platforms": ["youtube", "google"]})
    client.post(f"/posts/{post_id}/approve")
    r = client.post(f"/posts/{post_id}/publish", json={"confirm": True, "scheduled_at": "2020-01-01T10:00:00Z"})
    assert r.json()[0]["status"] == "scheduled"

    first = pipeline.publish_due()
    assert first["published"] == 2
    assert first["failed"] == 0
    assert client.get(f"/posts/{post_id}").json()["status"] == "published"

    second = pipeline.publish_due()
    assert second["published"] == 0
    assert second["failed"] == 0


def test_scheduler_failure_retries_then_fails(client, monkeypatch):
    """A throwing adapter increments attempts, retries, then fails the post."""
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())
    monkeypatch.setattr(pipeline, "chat_completion", lambda *a, **k: {"content": '{"verdict": "pass", "risks": []}'})
    monkeypatch.setattr(pipeline, "MAX_PUBLISH_ATTEMPTS", 2)

    class _Boom:
        def publish(self, publication, account=None):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.platforms._ADAPTERS", {"youtube": _Boom(), "google": _Boom()})

    post_id = _validated_post(client, monkeypatch)
    client.post(f"/posts/{post_id}/generate-media")
    client.post(f"/posts/{post_id}/compliance")
    client.post(f"/posts/{post_id}/publications", json={"platforms": ["youtube"]})
    client.post(f"/posts/{post_id}/approve")
    client.post(f"/posts/{post_id}/publish", json={"confirm": True, "scheduled_at": "2020-01-01T10:00:00Z"})

    # First tick: fails once, still scheduled (attempt 1/2) for a retry.
    first = pipeline.publish_due()
    assert first["published"] == 0
    assert first["failed"] == 1
    assert client.get(f"/posts/{post_id}").json()["status"] == "scheduled"

    # Second tick: fails again → attempt 2 → terminal failure.
    second = pipeline.publish_due()
    assert second["failed"] == 1
    assert client.get(f"/posts/{post_id}").json()["status"] == "failed"
    pub = client.get(f"/posts/{post_id}/publications").json()[0]
    assert pub["attempt_count"] == 2
    assert "boom" in pub["last_error"]


def test_mock_adapter_idempotency_key_is_stable():
    """Same idempotency key → same remote id (no double post on retry)."""
    from app.platforms import get_adapter

    class _Pub:
        platform = "youtube"
        idempotency_key = "fixed-key-123"

    adapter = get_adapter("youtube")
    rid1, url1 = adapter.publish(_Pub())
    rid2, url2 = adapter.publish(_Pub())
    assert rid1 == rid2
    assert url1 == url2


def test_text_only_post_skips_media_generation(client, monkeypatch):
    """media_type 'text' → no media step; compliance runs from concept_validated."""
    from app.api import chat as chat_api
    from app.services import pipeline

    monkeypatch.setattr(chat_api, "generate_proposals", lambda *a, **k: [{
        "number": 1, "caption": "Texte seul", "hashtags": ["#test"],
        "hook": "H", "cta": "C", "media_prompt": "",
    }])
    monkeypatch.setattr(pipeline, "chat_completion", lambda *a, **k: {"content": '{"verdict": "pass", "risks": []}'})

    sid = client.post("/chat/sessions").json()["id"]
    pid = client.post(f"/chat/sessions/{sid}/send", json={"content": "post", "media_type": "text"}).json()["proposals"][0]["id"]
    post_id = client.post(f"/chat/proposals/{pid}/validate").json()["post"]["id"]

    post = client.get(f"/posts/{post_id}").json()
    assert post["media_type"] == "text"
    assert post["status"] == "concept_validated"

    # media generation is rejected for text-only posts
    assert client.post(f"/posts/{post_id}/generate-media").status_code == 400

    # compliance runs directly from concept_validated (no media step)
    r = client.post(f"/posts/{post_id}/compliance")
    assert r.status_code == 200
    assert r.json()["status"] == "compliance_ok"
    assert r.json()["media_ids"] == []


def test_onscreen_text_detection():
    """Heuristic flags only prompts that request rendered text."""
    from app.services.pipeline import _requests_onscreen_text

    assert _requests_onscreen_text("A clean product shot on a desk", "") is False
    assert _requests_onscreen_text('Banner with the text "SOLDES -50%"', "") is True
    assert _requests_onscreen_text("", "affiche le slogan de la marque") is True


def test_compliance_vision_checks_onscreen_text(client, monkeypatch):
    """When the prompt asks for on-image text, compliance vision-checks it."""
    from app.api import chat as chat_api
    from app.services import pipeline

    monkeypatch.setattr(chat_api, "generate_proposals", lambda *a, **k: [{
        "number": 1, "caption": "C", "hashtags": ["#t"], "hook": "H", "cta": "A",
        "media_prompt": 'Banner with the text "SOLDES"',
    }])
    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())

    def fake_chat(messages, **k):
        content = messages[-1].get("content") if messages else ""
        if isinstance(content, list):  # vision call
            return {"content": '{"match": false, "text_found": "SOLDES", "issues": ["faute sur le mot"]}'}
        return {"content": '{"verdict": "pass", "risks": []}'}  # text compliance call

    monkeypatch.setattr(pipeline, "chat_completion", fake_chat)

    sid = client.post("/chat/sessions").json()["id"]
    pid = client.post(f"/chat/sessions/{sid}/send", json={"content": "post"}).json()["proposals"][0]["id"]
    post_id = client.post(f"/chat/proposals/{pid}/validate").json()["post"]["id"]
    client.post(f"/posts/{post_id}/generate-media")
    r = client.post(f"/posts/{post_id}/compliance")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "compliance_ok"
    assert data["compliance"]["verdict"] == "risk"
    assert any("faute" in str(x) for x in data["compliance"]["risks"])


def test_onscreen_text_mode_no_skips_vision(client, monkeypatch):
    """on_media_text mode 'no' skips the vision check (saves credits)."""
    from app.api import chat as chat_api
    from app.services import pipeline

    monkeypatch.setattr(chat_api, "generate_proposals", lambda *a, **k: [{
        "number": 1, "caption": "C", "hashtags": ["#t"], "hook": "H", "cta": "A",
        "media_prompt": "A clean product shot",
    }])
    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())

    calls = {"vision": 0}

    def fake_chat(messages, **k):
        content = messages[-1].get("content") if messages else ""
        if isinstance(content, list):
            calls["vision"] += 1
            return {"content": '{"match": true, "text_found": "", "issues": []}'}
        return {"content": '{"verdict": "pass", "risks": []}'}

    monkeypatch.setattr(pipeline, "chat_completion", fake_chat)

    sid = client.post("/chat/sessions").json()["id"]
    pid = client.post(f"/chat/sessions/{sid}/send", json={"content": "post", "on_media_text": {"mode": "no"}}).json()["proposals"][0]["id"]
    post_id = client.post(f"/chat/proposals/{pid}/validate").json()["post"]["id"]
    client.post(f"/posts/{post_id}/generate-media")
    r = client.post(f"/posts/{post_id}/compliance")
    assert r.status_code == 200
    assert calls["vision"] == 0
    assert r.json()["compliance"]["verdict"] == "pass"


def test_step_back_undoes_approval(client, monkeypatch):
    """step-back walks the pipeline backward, dropping derived data."""
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())
    monkeypatch.setattr(pipeline, "chat_completion", lambda *a, **k: {"content": '{"verdict": "pass", "risks": []}'})

    post_id = _validated_post(client, monkeypatch)
    client.post(f"/posts/{post_id}/generate-media")
    client.post(f"/posts/{post_id}/compliance")
    client.post(f"/posts/{post_id}/publications", json={"platforms": ["tiktok"]})
    client.post(f"/posts/{post_id}/approve")
    assert client.get(f"/posts/{post_id}").json()["status"] == "approved"

    # approved → compliance_ok (draft pubs kept)
    r = client.post(f"/posts/{post_id}/step-back")
    assert r.status_code == 200
    assert r.json()["status"] == "compliance_ok"
    assert len(client.get(f"/posts/{post_id}/publications").json()) == 1

    # compliance_ok (with drafts) → network re-selection (drafts dropped)
    r2 = client.post(f"/posts/{post_id}/step-back")
    assert r2.json()["status"] == "compliance_ok"
    assert client.get(f"/posts/{post_id}/publications").json() == []

    # compliance_ok (no pubs) → media_ready
    r3 = client.post(f"/posts/{post_id}/step-back")
    assert r3.json()["status"] == "media_ready"



