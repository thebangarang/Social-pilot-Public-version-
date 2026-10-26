"""Pinterest real driver: adapter publish + OAuth endpoints (HTTP mocked)."""
from types import SimpleNamespace

import httpx

from app.config import settings
from app.platforms.pinterest import PinterestAdapter
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
        account_meta={"username": "aiturn", "board_id": "12345", "board_name": "Social Pilot"},
    )


def test_pinterest_publish_pin(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="Hello Pinterest", media_ids=["m1"], idempotency_key="k")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json") or {}
        captured["headers"] = kwargs.get("headers") or {}
        return _FakeResp(201, {"id": "pin123", "media": {"images": {"orig": {"url": "https://i.pinimg.com/x.jpg"}}}})

    monkeypatch.setattr(httpx, "post", fake_post)
    rid, url = PinterestAdapter().publish(pub, acct)
    assert rid == "pin123"
    assert url == "https://i.pinimg.com/x.jpg"
    assert captured["url"] == "https://api.pinterest.com/v5/pins"
    assert captured["json"]["board_id"] == "12345"
    assert captured["json"]["title"] == "Hello Pinterest"
    assert captured["json"]["media_source"]["source_type"] == "image_url"
    assert captured["headers"]["Authorization"] == "Bearer token"


def test_pinterest_publish_idempotent():
    acct = _acct()
    pub = SimpleNamespace(remote_post_id="pin123", remote_url="https://www.pinterest.com/pin/pin123/", caption="x", media_ids=[], idempotency_key="k")
    rid, url = PinterestAdapter().publish(pub, acct)  # no network
    assert rid == "pin123"


def test_pinterest_publish_requires_image():
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="hello", media_ids=[], idempotency_key="k")
    try:
        PinterestAdapter().publish(pub, acct)
    except RuntimeError as e:
        assert "image" in str(e)
    else:
        raise AssertionError("expected RuntimeError for missing image")


def test_pinterest_publish_requires_board():
    acct = SimpleNamespace(handle="aiturn", token_enc=encrypt_secret("token"), account_meta={"username": "aiturn"})
    pub = SimpleNamespace(remote_post_id=None, caption="hello", media_ids=["m1"], idempotency_key="k")
    try:
        PinterestAdapter().publish(pub, acct)
    except RuntimeError as e:
        assert "tableau" in str(e)
    else:
        raise AssertionError("expected RuntimeError for missing board")


def test_pinterest_delete(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id="pin123")
    captured = {}

    def fake_delete(url, **kwargs):
        captured["url"] = url
        return _FakeResp(204, {})

    monkeypatch.setattr(httpx, "delete", fake_delete)
    PinterestAdapter().delete(pub, acct)
    assert captured["url"] == "https://api.pinterest.com/v5/pins/pin123"


def test_pinterest_insights_maps_metrics(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id="pin123")

    def fake_get(url, **kwargs):
        return _FakeResp(200, {"lifetime_metrics": [
            {"metric_type": "IMPRESSION", "value": 100},
            {"metric_type": "SAVE", "value": 5},
            {"metric_type": "PIN_CLICK", "value": 20},
        ]})

    monkeypatch.setattr(httpx, "get", fake_get)
    out = PinterestAdapter().insights(pub, acct)
    assert out == {"impressions": 100, "saves": 5, "views": 20}


def test_pinterest_reply_unavailable():
    acct = _acct()
    comment = SimpleNamespace(remote_comment_id="c1", reply_draft="Merci")
    try:
        PinterestAdapter().reply(comment, acct)
    except RuntimeError as e:
        assert "non disponible" in str(e)
    else:
        raise AssertionError("expected RuntimeError — reply not exposed")


def test_pinterest_authorize_redirects(client, monkeypatch):
    monkeypatch.setattr(settings, "pinterest_client_id", "test-id")
    r = client.get("/oauth/pinterest/authorize", follow_redirects=False)
    assert r.status_code == 302
    assert "pinterest.com/oauth" in r.headers["location"]
    assert "pins:write" in r.headers["location"]


def test_pinterest_callback_rejects_bad_state(client, monkeypatch):
    monkeypatch.setattr(settings, "pinterest_client_secret", "test-secret")
    r = client.get("/oauth/callback/pinterest", params={"code": "x", "state": "wrong"})
    assert r.status_code == 400
