"""Threads real driver: adapter publish/reply/insights + OAuth endpoints (HTTP mocked)."""
from types import SimpleNamespace

import httpx

from app.config import settings
from app.platforms.threads import ThreadsAdapter
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


def _acct(meta=None):
    return SimpleNamespace(
        handle="dummy",
        token_enc=encrypt_secret("token"),
        account_meta=meta or {"threads_user_id": "12345"},
    )


def test_threads_adapter_publish_image(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(
        remote_post_id=None, caption="Hello Threads", media_ids=["mid"], idempotency_key="k"
    )
    container_data = {}

    monkeypatch.setattr(ThreadsAdapter, "_first_media_kind", lambda self, p: "image")
    monkeypatch.setattr(ThreadsAdapter, "_media_url", lambda self, p: "https://x/media/mid/file")

    def fake_post(url, **kwargs):
        if url.endswith("/threads_publish"):
            return _FakeResp(200, {"id": "media456"})
        container_data.update(kwargs.get("data") or {})
        return _FakeResp(200, {"id": "container123"})

    monkeypatch.setattr(httpx, "post", fake_post)

    def fake_get(url, **kwargs):
        if "/media456" in url:
            return _FakeResp(200, {"permalink": "https://www.threads.net/@dummy/post/ABC123/"})
        return _FakeResp(200, {"status": "FINISHED"})

    monkeypatch.setattr(httpx, "get", fake_get)
    rid, url = ThreadsAdapter().publish(pub, acct)
    assert rid == "media456"
    assert url == "https://www.threads.net/@dummy/post/ABC123/"
    assert container_data["media_type"] == "IMAGE"
    assert container_data["image_url"] == "https://x/media/mid/file"
    assert container_data["text"] == "Hello Threads"


def test_threads_adapter_publish_text_only(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(
        remote_post_id=None, caption="Hello Threads", media_ids=[], idempotency_key="k"
    )
    container_data = {}
    monkeypatch.setattr(ThreadsAdapter, "_first_media_kind", lambda self, p: None)

    def fake_post(url, **kwargs):
        if url.endswith("/threads_publish"):
            return _FakeResp(200, {"id": "media456"})
        container_data.update(kwargs.get("data") or {})
        return _FakeResp(200, {"id": "container123"})

    monkeypatch.setattr(httpx, "post", fake_post)

    def fake_get(url, **kwargs):
        if "/media456" in url:
            return _FakeResp(200, {"permalink": "https://www.threads.net/t/ABC"})
        return _FakeResp(200, {"status": "FINISHED"})

    monkeypatch.setattr(httpx, "get", fake_get)
    rid, url = ThreadsAdapter().publish(pub, acct)
    assert rid == "media456"
    assert container_data["media_type"] == "TEXT"
    assert container_data["text"] == "Hello Threads"


def test_threads_adapter_publish_idempotent_retry():
    acct = _acct()
    pub = SimpleNamespace(
        remote_post_id="media456", remote_url="https://www.threads.net/t/media456/",
        caption="x", media_ids=[], idempotency_key="k",
    )
    rid, url = ThreadsAdapter().publish(pub, acct)  # no network
    assert rid == "media456"
    assert url == "https://www.threads.net/t/media456/"


def test_threads_publish_retries_on_transient_media_error(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id=None, caption="Hi", media_ids=[], idempotency_key="k")
    monkeypatch.setattr(ThreadsAdapter, "_first_media_kind", lambda self, p: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    publish_calls = {"n": 0}

    def fake_post(url, **kwargs):
        if url.endswith("/threads_publish"):
            publish_calls["n"] += 1
            if publish_calls["n"] == 1:
                return _FakeResp(400, {"error": {"error_subcode": 4279009, "message": "not found"}})
            return _FakeResp(200, {"id": "media456"})
        return _FakeResp(200, {"id": "container123"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(200, {"status": "FINISHED"}))
    rid, url = ThreadsAdapter().publish(pub, acct)
    assert rid == "media456"
    assert publish_calls["n"] == 2  # first hit 4279009, retry succeeded


def test_threads_adapter_reply(monkeypatch):
    acct = _acct()
    comment = SimpleNamespace(remote_comment_id="cmt1", reply_draft="Merci !")
    captured = {}

    def fake_post(url, **kwargs):
        if url.endswith("/threads_publish"):
            return _FakeResp(200, {"id": "reply1"})
        captured.update(kwargs.get("data") or {})
        return _FakeResp(200, {"id": "container1"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(200, {"status": "FINISHED"}))
    rid = ThreadsAdapter().reply(comment, acct)
    assert rid == "reply1"
    assert captured["media_type"] == "TEXT"
    assert captured["reply_to_id"] == "cmt1"
    assert captured["text"] == "Merci !"


def test_threads_adapter_reply_requires_text(monkeypatch):
    acct = _acct()
    comment = SimpleNamespace(remote_comment_id="cmt1", reply_draft="   ")
    try:
        ThreadsAdapter().reply(comment, acct)
    except RuntimeError as e:
        assert "vide" in str(e)
    else:
        raise AssertionError("expected RuntimeError for empty reply")


def test_threads_adapter_insights(monkeypatch):
    acct = _acct()
    pub = SimpleNamespace(remote_post_id="media456", caption="x", media_ids=[], idempotency_key="k")

    def fake_get(url, **kwargs):
        if "/insights" in url:
            return _FakeResp(200, {"data": [
                {"name": "views", "values": [{"value": 100}]},
                {"name": "likes", "values": [{"value": 12}]},
                {"name": "replies", "values": [{"value": 3}]},
                {"name": "reposts", "values": [{"value": 2}]},
            ]})
        return _FakeResp(404, {})

    monkeypatch.setattr(httpx, "get", fake_get)
    m = ThreadsAdapter().insights(pub, acct)
    assert m["views"] == 100
    assert m["likes"] == 12
    assert m["comments"] == 3  # replies → comments
    assert m["shares"] == 2  # reposts → shares


def test_threads_authorize_redirects(client, monkeypatch):
    monkeypatch.setattr(settings, "threads_client_id", "test-id")
    r = client.get("/oauth/threads/authorize", follow_redirects=False)
    assert r.status_code == 302
    assert "threads.net/oauth/authorize" in r.headers["location"]
    assert "threads_content_publish" in r.headers["location"]


def test_threads_callback_rejects_bad_state(client, monkeypatch):
    monkeypatch.setattr(settings, "threads_client_secret", "test-secret")
    r = client.get("/oauth/callback/threads", params={"code": "x", "state": "wrong"})
    assert r.status_code == 400
