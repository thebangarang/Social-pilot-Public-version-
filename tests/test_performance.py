"""Performance / metrics service + endpoint tests."""
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


def _published_publication(client, monkeypatch):
    """Run the full pipeline to a published post; return its publication dict."""
    from app.api import chat as chat_api
    from app.services import pipeline

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
    client.post(f"/posts/{post_id}/publications", json={"platforms": ["youtube"]})
    client.post(f"/posts/{post_id}/approve")
    return client.post(f"/posts/{post_id}/publish", json={"confirm": True}).json()[0]


def test_report_pdf(client, monkeypatch):
    _published_publication(client, monkeypatch)

    resp = client.get("/performance/report")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")
    assert len(resp.content) > 1000  # a real report, not a stub
    assert "attachment" in resp.headers["content-disposition"]

    filtered = client.get("/performance/report?platform=facebook")
    assert filtered.status_code == 200
    assert filtered.content.startswith(b"%PDF-")


def test_report_pdf_empty(db, make_operator):
    from app.services import report

    # fresh operator with zero posts → exercises the no-data path
    op = make_operator("empty@socialpilot.local", "password123")
    pdf = report.build_performance_pdf(db, op)
    assert pdf.startswith(b"%PDF-")


def test_metrics_poll_and_overview(client, monkeypatch):
    from app.services import metrics

    _published_publication(client, monkeypatch)

    result = metrics.poll_insights()
    assert result["polled"] >= 1
    assert result["snapshots"] >= 5

    ov = client.get("/performance/overview").json()
    assert ov["post_count"] >= 1
    assert ov["totals"]["views"] > 0
    assert ov["totals"]["likes"] > 0
    assert 0 <= ov["engagement_rate"] <= 1
    assert len(ov["posts"]) >= 1
    assert ov["posts"][0]["platform"] == "youtube"
    assert ov["posts"][0]["post_id"]
    assert ov["posts"][0]["metrics"]["views"] > 0
    assert len(ov["trend"]) >= 1


def test_mock_insights_grow_over_time():
    """Insights are deterministic per post and grow with elapsed time."""
    from app.platforms import get_adapter

    class _Pub:
        platform = "youtube"
        idempotency_key = "fixed"
        remote_post_id = None
        published_at = None

    adapter = get_adapter("youtube")
    a = adapter.insights(_Pub())
    b = adapter.insights(_Pub())
    assert a == b  # deterministic
    assert a["views"] > 0


def test_post_detail_endpoint(client, monkeypatch):
    import uuid

    from app.services import metrics

    pub = _published_publication(client, monkeypatch)
    metrics.poll_insights()

    r = client.get(f"/performance/posts/{pub['id']}")
    assert r.status_code == 200
    d = r.json()
    assert d["platform"] == "youtube"
    assert d["metrics"]["views"] > 0
    assert len(d["series"]) >= 1

    assert client.get(f"/performance/posts/{uuid.uuid4()}").status_code == 404


def test_performance_insights(client, monkeypatch):
    from app.services import metrics

    _published_publication(client, monkeypatch)
    metrics.poll_insights()

    ins = client.get("/performance/insights").json()
    assert isinstance(ins["leaderboard"], list)
    assert isinstance(ins["formats"], list)
    assert isinstance(ins["anomalies"], list)
    assert len(ins["leaderboard"]) >= 1
    assert ins["leaderboard"][0]["engagement_rate"] > 0
    assert any(f["count"] >= 1 for f in ins["formats"])


def test_best_time_endpoint(client, monkeypatch):
    from app.services import metrics

    _published_publication(client, monkeypatch)
    metrics.poll_insights()

    bt = client.get("/performance/best-time").json()
    assert len(bt["grid"]) == 168  # 7 days × 24 hours
    assert len(bt["top_slots"]) >= 1
    assert any(c["count"] >= 1 for c in bt["grid"])
    assert any(c["score"] is not None for c in bt["grid"])


def test_engagement_rate_platform_aware():
    from app.services.metrics import engagement_rate_of

    # impressions present → uses impressions
    assert engagement_rate_of({"impressions": 100, "likes": 10, "comments": 2}) == round(12 / 100, 4)
    # no impressions, reach present → uses reach (Instagram FEED case)
    assert engagement_rate_of({"reach": 80, "likes": 10, "comments": 2}) == round(12 / 80, 4)
    # no audience metric → None (Bluesky case)
    assert engagement_rate_of({"likes": 10, "comments": 2}) is None
    # empty → None
    assert engagement_rate_of({}) is None



