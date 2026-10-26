"""Facebook real driver: adapter publish/insights/comments/reply + OAuth endpoints (HTTP mocked)."""
from types import SimpleNamespace

import httpx

from app.config import settings
from app.platforms.facebook import FacebookAdapter
from app.security import encrypt_secret


class _FakeResp:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def _acct():
    return SimpleNamespace(
        handle="mypage",
        token_enc=encrypt_secret("page-token"),
        account_meta={"page_id": "12345", "page_name": "My Page"},
    )


def test_facebook_publish_image(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="Hello FB", media_ids=["mid"], idempotency_key="k")
    monkeypatch.setattr(FacebookAdapter, "_first_media_kind", lambda self, p: "image")
    monkeypatch.setattr(FacebookAdapter, "_media_url", lambda self, p: "https://x/media/mid/file")
    captured = {}

    def fake_post(url, **kwargs):
        captured["endpoint"] = url
        captured.update(kwargs.get("data") or {})
        return _FakeResp(200, {"id": "photo1", "post_id": "12345_999"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(200, {"permalink_url": "https://www.facebook.com/12345/posts/999"}))
    rid, url = FacebookAdapter().publish(pub, acct)
    assert rid == "12345_999"
    assert url == "https://www.facebook.com/12345/posts/999"
    assert captured["endpoint"].endswith("/12345/photos")
    assert captured["url"] == "https://x/media/mid/file"
    assert captured["message"] == "Hello FB"


def test_facebook_publish_text_only(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="Hello FB", media_ids=[], idempotency_key="k")
    monkeypatch.setattr(FacebookAdapter, "_first_media_kind", lambda self, p: None)
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs.get("data") or {})
        return _FakeResp(200, {"id": "12345_999"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(200, {"permalink_url": "https://www.facebook.com/12345/posts/999"}))
    rid, url = FacebookAdapter().publish(pub, acct)
    assert rid == "12345_999"
    assert captured["url"].endswith("/12345/feed")
    assert captured["message"] == "Hello FB"


def test_facebook_publish_video(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="Watch", media_ids=["mid"], idempotency_key="k")
    monkeypatch.setattr(FacebookAdapter, "_first_media_kind", lambda self, p: "video")
    monkeypatch.setattr(FacebookAdapter, "_media_url", lambda self, p: "https://x/media/mid/file")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs.get("data") or {})
        return _FakeResp(200, {"id": "video1"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(200, {"permalink_url": "https://www.facebook.com/12345/videos/1"}))
    rid, url = FacebookAdapter().publish(pub, acct)
    assert rid == "video1"
    assert captured["url"].endswith("/12345/videos")
    assert captured["file_url"] == "https://x/media/mid/file"
    assert captured["description"] == "Watch"


def test_facebook_publish_idempotent():
    acct = _acct()
    pub = SimpleNamespace(
        remote_post_id="12345_999", remote_url="https://www.facebook.com/12345/posts/999",
        caption="x", media_ids=[], idempotency_key="k",
    )
    rid, url = FacebookAdapter().publish(pub, acct)  # no network
    assert rid == "12345_999"
    assert url == "https://www.facebook.com/12345/posts/999"


def test_facebook_publish_empty_text_raises(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="", media_ids=[], idempotency_key="k")
    monkeypatch.setattr(FacebookAdapter, "_first_media_kind", lambda self, p: None)
    try:
        FacebookAdapter().publish(pub, acct)
    except RuntimeError as e:
        assert "texte vide" in str(e)
    else:
        raise AssertionError("expected RuntimeError for empty text")


def test_facebook_insights(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id="12345_999", caption="x", media_ids=[], idempotency_key="k")

    def fake_get(url, **kwargs):
        if "/insights" in url:
            return _FakeResp(200, {"data": [
                {"name": "post_impressions", "values": [{"value": 1000}]},
                {"name": "post_impressions_unique", "values": [{"value": 800}]},
                {"name": "post_video_views", "values": [{"value": 500}]},
            ]})
        return _FakeResp(200, {
            "reactions": {"summary": {"total_count": 42}},
            "comments": {"summary": {"total_count": 7}},
        })

    monkeypatch.setattr(httpx, "get", fake_get)
    m = FacebookAdapter().insights(pub, acct)
    assert m["impressions"] == 1000
    assert m["reach"] == 800
    assert m["views"] == 500
    assert m["likes"] == 42
    assert m["comments"] == 7


def test_facebook_comments(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id="12345_999", caption="x", media_ids=[], idempotency_key="k")

    def fake_get(url, **kwargs):
        return _FakeResp(200, {"data": [
            {"id": "c1", "message": "nice", "from": {"name": "Alice"}},
        ]})

    monkeypatch.setattr(httpx, "get", fake_get)
    cs = FacebookAdapter().comments(pub, acct)
    assert cs[0]["remote_comment_id"] == "c1"
    assert cs[0]["author_name"] == "Alice"
    assert cs[0]["text"] == "nice"


def test_facebook_reply(monkeypatch):
    acct = _acct()
    comment = SimpleNamespace(remote_comment_id="c1", reply_draft="Merci !")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs.get("data") or {})
        return _FakeResp(200, {"id": "r1"})

    monkeypatch.setattr(httpx, "post", fake_post)
    rid = FacebookAdapter().reply(comment, acct)
    assert rid == "r1"
    assert captured["url"].endswith("/c1/comments")
    assert captured["message"] == "Merci !"


def test_facebook_reply_requires_text():
    acct = _acct()
    comment = SimpleNamespace(remote_comment_id="c1", reply_draft="   ")
    try:
        FacebookAdapter().reply(comment, acct)
    except RuntimeError as e:
        assert "vide" in str(e)
    else:
        raise AssertionError("expected RuntimeError for empty reply")


def test_facebook_authorize_redirects(client, monkeypatch):
    monkeypatch.setattr(settings, "facebook_client_id", "test-id")
    r = client.get("/oauth/facebook/authorize", follow_redirects=False)
    assert r.status_code == 302
    assert "facebook.com" in r.headers["location"]
    assert "pages_manage_posts" in r.headers["location"]


def test_facebook_callback_rejects_bad_state(client, monkeypatch):
    monkeypatch.setattr(settings, "facebook_client_secret", "test-secret")
    r = client.get("/oauth/callback/facebook", params={"code": "x", "state": "wrong"})
    assert r.status_code == 400
