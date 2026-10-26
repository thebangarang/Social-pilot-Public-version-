"""Bluesky real driver: connect endpoint + adapter publish (HTTP mocked)."""
import uuid
from types import SimpleNamespace

import httpx

from app.models import ConnectedAccount
from app.platforms.bluesky import BlueskyAdapter
from app.security import decrypt_secret, encrypt_secret


class _FakeResp:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def test_real_connect_not_implemented_for_other_platforms(client):
    r = client.post("/connect/instagram", json={"handle": "x", "app_password": "y"})
    assert r.status_code == 501


def test_bluesky_connect_creates_encrypted_account(client, db, monkeypatch):
    def fake_post(url, **kwargs):
        assert url.endswith("com.atproto.server.createSession")
        return _FakeResp(
            200,
            {"accessJwt": "jwt", "did": "did:plc:test123", "handle": "test.bsky.social"},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    r = client.post(
        "/connect/bluesky",
        json={"handle": "test.bsky.social", "app_password": "abcd-efgh-ijkl"},
    )
    assert r.status_code == 201
    acc = r.json()
    assert acc["platform"] == "bluesky"
    assert acc["handle"] == "test.bsky.social"
    assert acc["account_meta"]["did"] == "did:plc:test123"
    assert acc["account_meta"]["source"] == "app_password"

    row = db.get(ConnectedAccount, uuid.UUID(acc["id"]))
    assert row.token_enc != "abcd-efgh-ijkl"  # never stored in clear
    assert decrypt_secret(row.token_enc) == "abcd-efgh-ijkl"
    client.delete(f"/connect/{acc['id']}")


def test_bluesky_connect_invalid_password(client, monkeypatch):
    def fake_post(url, **kwargs):
        return _FakeResp(401, {"error": "Invalid identifier or password"})

    monkeypatch.setattr(httpx, "post", fake_post)
    r = client.post(
        "/connect/bluesky",
        json={"handle": "bad.bsky.social", "app_password": "wrong"},
    )
    assert r.status_code == 401


def test_bluesky_adapter_publish_roundtrip(monkeypatch):
    acct = SimpleNamespace(handle="test.bsky.social", token_enc=encrypt_secret("app-pass"))
    pub = SimpleNamespace(
        remote_post_id=None, caption="Hello world", media_ids=[], idempotency_key="abc123"
    )
    record_payloads = []

    def fake_post(url, **kwargs):
        if url.endswith("createSession"):
            return _FakeResp(200, {"accessJwt": "jwt", "did": "did:plc:test"})
        if url.endswith("createRecord"):
            record_payloads.append(kwargs["json"])
            return _FakeResp(200, {"uri": "at://did:plc:test/app.bsky.feed.post/3abc"})
        return _FakeResp(404, {})

    monkeypatch.setattr(httpx, "post", fake_post)
    uri, url = BlueskyAdapter().publish(pub, acct)
    assert uri == "at://did:plc:test/app.bsky.feed.post/3abc"
    assert url == "https://bsky.app/profile/test.bsky.social/post/3abc"
    assert record_payloads[0]["record"]["text"] == "Hello world"
    assert record_payloads[0]["collection"] == "app.bsky.feed.post"


def test_bluesky_adapter_publish_idempotent_retry():
    acct = SimpleNamespace(handle="test.bsky.social", token_enc=encrypt_secret("app-pass"))
    pub = SimpleNamespace(
        remote_post_id="at://did:plc:test/app.bsky.feed.post/3abc",
        caption="Hello", media_ids=[], idempotency_key="abc123",
    )
    uri, url = BlueskyAdapter().publish(pub, acct)  # must not hit the network
    assert uri == "at://did:plc:test/app.bsky.feed.post/3abc"
    assert url == "https://bsky.app/profile/test.bsky.social/post/3abc"
