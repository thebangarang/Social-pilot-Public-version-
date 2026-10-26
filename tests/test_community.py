"""Community manager tests."""
import io

from PIL import Image


def _fake_image_bytes():
    """A generated image big enough to carry the Article 50(2) mark (4x3 cannot: the library refuses it)."""
    img = Image.new("RGB", (512, 512), (28, 34, 48))
    for i in range(0, 512, 16):
        for j in range(0, 512, 16):
            img.putpixel((i, j), (150 + (i % 90), 170, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _published_publication(client, monkeypatch, platforms=None):
    from app.api import chat as chat_api
    from app.services import pipeline

    platforms = platforms or ["youtube"]

    monkeypatch.setattr(chat_api, "generate_proposals", lambda *a, **k: [{
        "number": 1, "caption": "Caption", "hashtags": ["#t"], "hook": "H", "cta": "A",
        "media_prompt": "an image",
    }])
    monkeypatch.setattr(pipeline, "image_generation", lambda prompt, reference_images=None: _fake_image_bytes())
    monkeypatch.setattr(pipeline, "chat_completion", lambda *a, **k: {"content": '{"verdict": "pass", "risks": []}'})

    sid = client.post("/chat/sessions").json()["id"]
    pid = client.post(f"/chat/sessions/{sid}/send", json={"content": "post"}).json()["proposals"][0]["id"]
    post_id = client.post(f"/chat/proposals/{pid}/validate").json()["post"]["id"]
    client.post(f"/posts/{post_id}/generate-media")
    client.post(f"/posts/{post_id}/compliance")
    client.post(f"/posts/{post_id}/publications", json={"platforms": platforms})
    client.post(f"/posts/{post_id}/approve")
    return client.post(f"/posts/{post_id}/publish", json={"confirm": True}).json()[0]


def test_classify():
    from app.services.community import classify

    assert classify("Superbe publication, merci pour le partage !") == "green"
    assert classify("C'est combien ?") == "orange"
    assert classify("Remboursez-moi, c'est une arnaque, je vais porter plainte !") == "red"
    # ambiguous → None (deferred to the LLM)
    assert classify("Le colis est arrivé cassé.") is None
    assert classify("Mouais, pas convaincu.") is None


def test_poll_and_list_comments(client, monkeypatch):
    from app.services import community

    monkeypatch.setattr(
        community, "classify_batch_llm",
        lambda texts: ["red" if "cassé" in t else "orange" for t in texts],
    )
    _published_publication(client, monkeypatch)
    result = community.poll_comments()
    assert result["added"] >= 7

    comments = client.get("/community/comments").json()
    assert len(comments) >= 7
    levels = {c["level"] for c in comments}
    assert {"green", "orange", "red"} <= levels

    # the "cassé" comment (missed by rules) is caught by the LLM as red
    casse = [c for c in comments if "cassé" in c["text"]]
    assert casse and casse[0]["level"] == "red"

    # level filter
    red = client.get("/community/comments?level=red").json()
    assert all(c["level"] == "red" for c in red)

    # idempotent poll (dedup by remote id)
    assert community.poll_comments()["added"] == 0


def test_generate_and_reply(client, monkeypatch):
    from app.services import community

    monkeypatch.setattr("app.llm.chat_completion", lambda *a, **k: {"content": "Merci pour votre message !"})
    _published_publication(client, monkeypatch)
    community.poll_comments()

    c = client.get("/community/comments").json()[0]
    r = client.post(f"/community/comments/{c['id']}/generate-reply")
    assert r.status_code == 200
    assert r.json()["reply_draft"] == "Merci pour votre message !"

    r2 = client.post(f"/community/comments/{c['id']}/reply", json={"text": "Merci !"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "replied"


def test_batch_reply(client, monkeypatch):
    from app.services import community

    monkeypatch.setattr("app.llm.chat_completion", lambda *a, **k: {"content": "Merci !"})
    _published_publication(client, monkeypatch)
    community.poll_comments()

    comments = client.get("/community/comments?status=new").json()[:3]
    ids = [c["id"] for c in comments]

    r = client.post("/community/batch-generate", json={"comment_ids": ids})
    assert r.status_code == 200
    assert all(c["reply_draft"] for c in r.json())

    items = [{"comment_id": c["id"], "text": "Merci beaucoup !"} for c in r.json()]
    r2 = client.post("/community/batch-reply", json={"items": items})
    assert r2.status_code == 200
    assert r2.json()["sent"] == 3
