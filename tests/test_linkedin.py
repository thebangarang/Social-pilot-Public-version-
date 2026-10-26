"""LinkedIn real driver: adapter publish + OAuth endpoints (HTTP mocked)."""
from types import SimpleNamespace

import httpx

from app.config import settings
from app.platforms.linkedin import LinkedInAdapter
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
        handle="AITurn",
        token_enc=encrypt_secret("token"),
        account_meta={"sub": "abc123", "author_urn": "urn:li:person:abc123"},
    )


def test_linkedin_publish_text(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="Hello LinkedIn", media_ids=[], idempotency_key="k")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json") or {}
        captured["headers"] = kwargs.get("headers") or {}
        return _FakeResp(201, {}, {"x-restli-id": "urn:li:share:12345"})

    monkeypatch.setattr(httpx, "post", fake_post)
    rid, url = LinkedInAdapter().publish(pub, acct)
    assert rid == "urn:li:share:12345"
    assert url == "https://www.linkedin.com/feed/update/urn:li:share:12345"
    assert captured["url"] == "https://api.linkedin.com/rest/posts"
    assert captured["json"]["author"] == "urn:li:person:abc123"
    assert captured["json"]["commentary"] == "Hello LinkedIn"
    assert captured["json"]["lifecycleState"] == "PUBLISHED"
    assert captured["headers"]["Linkedin-Version"]


def test_linkedin_publish_idempotent():
    acct = _acct()
    pub = SimpleNamespace(
        remote_post_id="urn:li:share:12345", remote_url="https://www.linkedin.com/feed/update/urn:li:share:12345",
        caption="x", media_ids=[], idempotency_key="k",
    )
    rid, url = LinkedInAdapter().publish(pub, acct)  # no network
    assert rid == "urn:li:share:12345"


def test_linkedin_publish_empty_text_raises(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="   ", media_ids=[], idempotency_key="k")
    try:
        LinkedInAdapter().publish(pub, acct)
    except RuntimeError as e:
        assert "vide" in str(e)
    else:
        raise AssertionError("expected RuntimeError for empty text")


def test_linkedin_reply_unavailable():
    acct = _acct()
    comment = SimpleNamespace(remote_comment_id="c1", reply_draft="Merci")
    try:
        LinkedInAdapter().reply(comment, acct)
    except RuntimeError as e:
        assert "Community Management" in str(e)
    else:
        raise AssertionError("expected RuntimeError — reply is gated")


def test_linkedin_insights_stub():
    assert LinkedInAdapter().insights(SimpleNamespace(), _acct()) == {}


def test_linkedin_comments_stub():
    assert LinkedInAdapter().comments(SimpleNamespace(), _acct()) == []


def test_linkedin_authorize_redirects(client, monkeypatch):
    monkeypatch.setattr(settings, "linkedin_client_id", "test-id")
    r = client.get("/oauth/linkedin/authorize", follow_redirects=False)
    assert r.status_code == 302
    assert "linkedin.com/oauth/v2/authorization" in r.headers["location"]
    assert "w_member_social" in r.headers["location"]


def test_linkedin_callback_rejects_bad_state(client, monkeypatch):
    monkeypatch.setattr(settings, "linkedin_client_secret", "test-secret")
    r = client.get("/oauth/callback/linkedin", params={"code": "x", "state": "wrong"})
    assert r.status_code == 400
