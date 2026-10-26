"""TikTok real driver: adapter publish + OAuth endpoints (HTTP mocked)."""
from types import SimpleNamespace

import httpx

from app.platforms.tiktok import TikTokAdapter
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


def test_tiktok_adapter_publish(monkeypatch):
    acct = SimpleNamespace(handle="dummy", token_enc=encrypt_secret("access-token"))
    pub = SimpleNamespace(
        remote_post_id=None, caption="Hello TikTok", media_ids=["x"], idempotency_key="k"
    )
    calls = {}

    monkeypatch.setattr(TikTokAdapter, "_resolve_video", lambda self, p: b"fakevideo")

    def fake_post(url, **kwargs):
        if url.endswith("/video/init/"):
            calls["init"] = kwargs["json"]
            return _FakeResp(
                200,
                {"data": {"publish_id": "v_pub_123", "upload_url": "https://upload/here"},
                 "error": {"code": "ok"}},
            )
        if url.endswith("/status/fetch/"):
            calls["status"] = kwargs["json"]
            return _FakeResp(200, {"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}})
        return _FakeResp(404, {})

    def fake_put(url, **kwargs):
        calls["upload_url"] = url
        calls["upload_body"] = kwargs.get("content")
        return _FakeResp(200, {})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "put", fake_put)

    rid, url = TikTokAdapter().publish(pub, acct)
    assert rid == "v_pub_123"
    assert url == "https://www.tiktok.com/@dummy/video/v_pub_123"
    assert calls["init"]["post_info"]["is_aigc"] is True
    assert calls["init"]["post_info"]["privacy_level"] == "SELF_ONLY"
    assert calls["init"]["source_info"]["source"] == "FILE_UPLOAD"
    assert calls["upload_body"] == b"fakevideo"
    assert calls["status"]["publish_id"] == "v_pub_123"


def test_tiktok_adapter_publish_idempotent_retry():
    acct = SimpleNamespace(handle="dummy", token_enc=encrypt_secret("access-token"))
    pub = SimpleNamespace(
        remote_post_id="v_pub_123", remote_url="https://www.tiktok.com/@dummy/video/v_pub_123",
        caption="x", media_ids=[], idempotency_key="k",
    )
    rid, url = TikTokAdapter().publish(pub, acct)  # no network
    assert rid == "v_pub_123"
    assert url == "https://www.tiktok.com/@dummy/video/v_pub_123"


def test_tiktok_authorize_redirects(client):
    r = client.get("/oauth/tiktok/authorize", follow_redirects=False)
    assert r.status_code == 302
    assert "tiktok.com/v2/auth/authorize" in r.headers["location"]
    assert "video.publish" in r.headers["location"]


def test_tiktok_callback_rejects_bad_state(client):
    r = client.get("/oauth/callback/tiktok", params={"code": "x", "state": "wrong"})
    assert r.status_code == 400
