"""Instagram real driver: adapter publish + OAuth endpoints (HTTP mocked)."""
from types import SimpleNamespace

import httpx

from app.config import settings
from app.platforms.instagram import InstagramAdapter
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


def test_instagram_adapter_publish(monkeypatch):
    acct = SimpleNamespace(
        handle="dummy", token_enc=encrypt_secret("token"), account_meta={"ig_user_id": "12345"}
    )
    pub = SimpleNamespace(
        remote_post_id=None, caption="Hello IG", media_ids=["mid"], idempotency_key="k"
    )
    container_data = {}

    monkeypatch.setattr(InstagramAdapter, "_media_url", lambda self, p: "https://x/media/mid/file")

    def fake_post(url, **kwargs):
        if url.endswith("/media"):
            container_data.update(kwargs.get("data") or {})
            return _FakeResp(200, {"id": "container123"})
        if url.endswith("/media_publish"):
            return _FakeResp(200, {"id": "media456"})
        return _FakeResp(404, {})

    monkeypatch.setattr(httpx, "post", fake_post)

    def fake_get(url, **kwargs):
        if "/media456" in url:
            return _FakeResp(200, {"permalink": "https://www.instagram.com/p/ABC123/"})
        return _FakeResp(404, {})

    monkeypatch.setattr(httpx, "get", fake_get)
    rid, url = InstagramAdapter().publish(pub, acct)
    assert rid == "media456"
    assert url == "https://www.instagram.com/p/ABC123/"
    assert container_data["image_url"] == "https://x/media/mid/file"
    assert container_data["caption"] == "Hello IG"


def test_instagram_adapter_publish_idempotent_retry():
    acct = SimpleNamespace(handle="dummy", token_enc=encrypt_secret("token"), account_meta={})
    pub = SimpleNamespace(
        remote_post_id="media456", remote_url="https://www.instagram.com/p/media456/",
        caption="x", media_ids=[], idempotency_key="k",
    )
    rid, url = InstagramAdapter().publish(pub, acct)  # no network
    assert rid == "media456"
    assert url == "https://www.instagram.com/p/media456/"


def test_instagram_authorize_redirects(client, monkeypatch):
    monkeypatch.setattr(settings, "instagram_client_id", "test-id")
    r = client.get("/oauth/instagram/authorize", follow_redirects=False)
    assert r.status_code == 302
    assert "instagram.com/oauth/authorize" in r.headers["location"]
    assert "instagram_business_content_publish" in r.headers["location"]


def test_instagram_callback_rejects_bad_state(client, monkeypatch):
    monkeypatch.setattr(settings, "instagram_client_secret", "test-secret")
    r = client.get("/oauth/callback/instagram", params={"code": "x", "state": "wrong"})
    assert r.status_code == 400


def test_instagram_adapter_reply(monkeypatch):
    acct = SimpleNamespace(handle="dummy", token_enc=encrypt_secret("token"), account_meta={})
    comment = SimpleNamespace(remote_comment_id="cmt1", reply_draft="Merci !")
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs.get("data") or {})
        return _FakeResp(200, {"id": "reply1"})

    monkeypatch.setattr(httpx, "post", fake_post)
    rid = InstagramAdapter().reply(comment, acct)
    assert rid == "reply1"
    assert captured["message"] == "Merci !"


def test_instagram_adapter_reply_requires_text(monkeypatch):
    acct = SimpleNamespace(handle="dummy", token_enc=encrypt_secret("token"), account_meta={})
    comment = SimpleNamespace(remote_comment_id="cmt1", reply_draft="   ")
    try:
        InstagramAdapter().reply(comment, acct)
    except RuntimeError as e:
        assert "vide" in str(e)
    else:
        raise AssertionError("expected RuntimeError for empty reply")


def test_instagram_adapter_insights(monkeypatch):
    acct = SimpleNamespace(handle="dummy", token_enc=encrypt_secret("token"), account_meta={})
    pub = SimpleNamespace(remote_post_id="media456", caption="x", media_ids=[], idempotency_key="k")

    def fake_get(url, **kwargs):
        if "/insights" in url:
            return _FakeResp(200, {"data": [
                {"name": "reach", "values": [{"value": 80}]},
                {"name": "saved", "values": [{"value": 5}]},
                {"name": "shares", "values": [{"value": 2}]},
                {"name": "views", "values": [{"value": 100}]},
            ]})
        if "/media456" in url:
            return _FakeResp(200, {"like_count": 12, "comments_count": 3})
        return _FakeResp(404, {})

    monkeypatch.setattr(httpx, "get", fake_get)
    m = InstagramAdapter().insights(pub, acct)
    assert m["reach"] == 80
    assert m["saves"] == 5
    assert m["shares"] == 2
    assert m["views"] == 100
    assert m["likes"] == 12
    assert m["comments"] == 3
