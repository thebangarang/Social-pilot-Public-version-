"""X (Twitter) real driver: adapter publish + OAuth endpoints (HTTP mocked)."""
from types import SimpleNamespace

import httpx

from app.config import settings
from app.platforms.x import XAdapter
from app.security import encrypt_secret


class _FakeResp:
    def __init__(self, status_code, json_data, headers=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        return self._json


def _acct():
    return SimpleNamespace(
        handle="aiturn",
        token_enc=encrypt_secret("token"),
        account_meta={"username": "aiturn", "user_id": "123"},
    )


def test_x_publish_text(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="Hello X", media_ids=[], idempotency_key="k")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json") or {}
        captured["headers"] = kwargs.get("headers") or {}
        return _FakeResp(201, {"data": {"id": "123456", "text": "Hello X"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    rid, url = XAdapter().publish(pub, acct)
    assert rid == "123456"
    assert url == "https://x.com/i/web/status/123456"
    assert captured["url"] == "https://api.x.com/2/tweets"
    assert captured["json"]["text"] == "Hello X"
    assert captured["headers"]["Authorization"] == "Bearer token"


def test_x_publish_idempotent():
    acct = _acct()
    pub = SimpleNamespace(remote_post_id="123456", remote_url="https://x.com/i/web/status/123456", caption="x", media_ids=[], idempotency_key="k")
    rid, url = XAdapter().publish(pub, acct)  # no network
    assert rid == "123456"


def test_x_publish_rejects_media():
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="hi", media_ids=["m1"], idempotency_key="k")
    try:
        XAdapter().publish(pub, acct)
    except RuntimeError as e:
        assert "image" in str(e)
    else:
        raise AssertionError("expected RuntimeError — text-only for now")


def test_x_publish_rejects_long_text():
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="a" * 281, media_ids=[], idempotency_key="k")
    try:
        XAdapter().publish(pub, acct)
    except RuntimeError as e:
        assert "280" in str(e)
    else:
        raise AssertionError("expected RuntimeError for >280 chars")


def test_x_delete(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id="123456")
    captured = {}

    def fake_delete(url, **kwargs):
        captured["url"] = url
        return _FakeResp(200, {"data": {"deleted": True}})

    monkeypatch.setattr(httpx, "delete", fake_delete)
    XAdapter().delete(pub, acct)
    assert captured["url"] == "https://api.x.com/2/tweets/123456"


def test_x_insights_maps_metrics(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id="123456")

    def fake_get(url, **kwargs):
        return _FakeResp(200, {"data": {"public_metrics": {
            "like_count": 10, "reply_count": 2, "retweet_count": 3, "impression_count": 100, "quote_count": 1,
        }}})

    monkeypatch.setattr(httpx, "get", fake_get)
    out = XAdapter().insights(pub, acct)
    assert out == {"likes": 10, "comments": 2, "shares": 3, "impressions": 100}


def test_x_reply_unavailable():
    acct = _acct()
    comment = SimpleNamespace(remote_comment_id="c1", reply_draft="Merci")
    try:
        XAdapter().reply(comment, acct)
    except RuntimeError as e:
        assert "mention" in str(e)
    else:
        raise AssertionError("expected RuntimeError — reply is restricted")


def test_x_authorize_redirects(client, monkeypatch):
    monkeypatch.setattr(settings, "x_client_id", "test-id")
    r = client.get("/oauth/x/authorize", follow_redirects=False)
    assert r.status_code == 302
    assert "x.com/i/oauth2/authorize" in r.headers["location"]
    assert "tweet.write" in r.headers["location"]


def test_x_callback_rejects_bad_state(client, monkeypatch):
    monkeypatch.setattr(settings, "x_client_secret", "test-secret")
    r = client.get("/oauth/callback/x", params={"code": "x", "state": "wrong"})
    assert r.status_code == 400
