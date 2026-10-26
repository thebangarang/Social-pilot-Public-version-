"""Chat sessions + send/adjust/validate (LLM generation is monkeypatched)."""
FAKE_PROPOSALS = [
    {"number": 1, "caption": "Caption 1", "hashtags": ["#a", "#b"], "hook": "H1", "cta": "C1", "media_prompt": "mp1"},
    {"number": 2, "caption": "Caption 2", "hashtags": ["#c"], "hook": "H2", "cta": "C2", "media_prompt": "mp2"},
]


def test_session_crud(client):
    r = client.post("/chat/sessions")
    assert r.status_code == 201
    sid = r.json()["id"]
    assert any(s["id"] == sid for s in client.get("/chat/sessions").json())
    assert client.get(f"/chat/sessions/{sid}").json() == []


def test_send_message(client, monkeypatch):
    from app.api import chat as chat_api

    monkeypatch.setattr(chat_api, "generate_proposals", lambda *a, **k: FAKE_PROPOSALS)
    sid = client.post("/chat/sessions").json()["id"]
    r = client.post(f"/chat/sessions/{sid}/send", json={"content": "Fais un post", "media_ids": [], "preset_id": None})
    assert r.status_code == 200
    data = r.json()
    assert data["user_message"]["content"] == "Fais un post"
    assert len(data["proposals"]) == 2
    assert data["proposals"][0]["number"] == 1
    assert data["proposals"][0]["content"]["caption"] == "Caption 1"
    # session title updated from first message
    assert any(s["title"] == "Fais un post" for s in client.get("/chat/sessions").json())
    # active proposals retrievable
    active = client.get(f"/chat/sessions/{sid}/proposals").json()
    assert len(active) == 2


def test_adjust_proposal(client, monkeypatch):
    from app.api import chat as chat_api

    monkeypatch.setattr(chat_api, "generate_proposals", lambda *a, **k: FAKE_PROPOSALS)
    sid = client.post("/chat/sessions").json()["id"]
    pid = client.post(f"/chat/sessions/{sid}/send", json={"content": "post", "preset_id": None}).json()["proposals"][0]["id"]

    monkeypatch.setattr(chat_api, "generate_adjustment", lambda *a, **k: [dict(FAKE_PROPOSALS[0], caption="Ajusté")])
    r = client.post(f"/chat/proposals/{pid}/adjust", json={"proposal_id": pid, "feedback": "plus punchy"})
    assert r.status_code == 200
    assert r.json()["proposals"][0]["content"]["caption"] == "Ajusté"


def test_validate_proposal(client, monkeypatch):
    from app.api import chat as chat_api

    monkeypatch.setattr(chat_api, "generate_proposals", lambda *a, **k: FAKE_PROPOSALS)
    sid = client.post("/chat/sessions").json()["id"]
    r = client.post(f"/chat/sessions/{sid}/send", json={"content": "post", "preset_id": None})
    pid = r.json()["proposals"][1]["id"]

    r2 = client.post(f"/chat/proposals/{pid}/validate")
    assert r2.status_code == 200
    assert r2.json()["proposal"]["status"] == "validated"
    active = client.get(f"/chat/sessions/{sid}/proposals").json()
    assert all(p["id"] != pid for p in active)  # validated no longer active


def test_send_requires_llm_configured(client, monkeypatch):
    # no monkeypatch -> would hit the real LLM; instead assert the endpoint
    # surfaces a 502 cleanly when generation raises.
    from app.api import chat as chat_api

    def boom(*a, **k):
        raise RuntimeError("pas de LLM")

    monkeypatch.setattr(chat_api, "generate_proposals", boom)
    sid = client.post("/chat/sessions").json()["id"]
    r = client.post(f"/chat/sessions/{sid}/send", json={"content": "post", "preset_id": None})
    assert r.status_code == 502


def test_send_with_branding_usage(client, monkeypatch):
    from app.api import chat as chat_api

    captured = {}

    def fake(db, request, preset=None, history=None, branding_usage=None, media_type=None, on_media_text=None, media_brief=None, platforms=None, language=None):
        captured["branding_usage"] = branding_usage
        captured["media_brief"] = media_brief
        return FAKE_PROPOSALS

    monkeypatch.setattr(chat_api, "generate_proposals", fake)
    sid = client.post("/chat/sessions").json()["id"]
    r = client.post(f"/chat/sessions/{sid}/send", json={
        "content": "post", "media_ids": [], "preset_id": None,
        "branding_usage": {"use_logo": True, "logo_placement": "decor", "use_colors": False, "use_font": False},
    })
    assert r.status_code == 200
    assert captured["branding_usage"]["logo_placement"] == "decor"


def test_send_with_media_brief(client, monkeypatch):
    from app.api import chat as chat_api

    captured = {}

    def fake(db, request, preset=None, history=None, branding_usage=None, media_type=None, on_media_text=None, media_brief=None, platforms=None, language=None):
        captured["media_brief"] = media_brief
        return FAKE_PROPOSALS

    monkeypatch.setattr(chat_api, "generate_proposals", fake)
    sid = client.post("/chat/sessions").json()["id"]
    brief = "aerial view of the campus, golden hour, warm tones"
    r = client.post(f"/chat/sessions/{sid}/send", json={
        "content": "post", "media_ids": [], "preset_id": None,
        "media_type": "image", "media_brief": brief,
    })
    assert r.status_code == 200
    assert captured["media_brief"] == brief
    # stored on the user message and propagated to the validated post
    assert client.get(f"/chat/sessions/{sid}").json()[0]["media_brief"] == brief
    pid = r.json()["proposals"][0]["id"]
    v = client.post(f"/chat/proposals/{pid}/validate", json={}).json()
    assert v["post"]["media_brief"] == brief


def test_delete_session(client, monkeypatch):
    from app.api import chat as chat_api

    monkeypatch.setattr(chat_api, "generate_proposals", lambda *a, **k: FAKE_PROPOSALS)
    sid = client.post("/chat/sessions").json()["id"]
    client.post(f"/chat/sessions/{sid}/send", json={"content": "post", "preset_id": None})
    assert client.delete(f"/chat/sessions/{sid}").status_code == 204
    assert all(s["id"] != sid for s in client.get("/chat/sessions").json())
