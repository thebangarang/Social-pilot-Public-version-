"""Meta webhook endpoints: signed_request verification + deauthorize/data-deletion."""
import base64
import hashlib
import hmac
import json

from app.api.webhooks import parse_signed_request


def _make_signed_request(secret: str, payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")
    return f"{sig}.{encoded}"


def test_parse_signed_request_valid():
    data = parse_signed_request(_make_signed_request("s3cret", {"user_id": "123", "algorithm": "HMAC-SHA256"}), "s3cret")
    assert data["user_id"] == "123"


def test_parse_signed_request_tampered():
    ok = _make_signed_request("s3cret", {"user_id": "123"})
    sig, payload = ok.split(".", 1)
    try:
        parse_signed_request(f"{sig}.{payload[:-2]}xx", "s3cret")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for tampered signed_request")


def test_parse_signed_request_malformed():
    try:
        parse_signed_request("no-dot-here", "s3cret")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for malformed signed_request")


def test_deauthorize_endpoint(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "threads_client_secret", "s3cret")
    sr = _make_signed_request("s3cret", {"user_id": "999"})
    r = client.post("/webhooks/threads/deauthorize", data={"signed_request": sr})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_deauthorize_endpoint_rejects_bad_signature(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "threads_client_secret", "s3cret")
    r = client.post("/webhooks/threads/deauthorize", data={"signed_request": "bad.sig"})
    assert r.status_code == 400


def test_data_deletion_endpoint(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "threads_client_secret", "s3cret")
    sr = _make_signed_request("s3cret", {"user_id": "999"})
    r = client.post("/webhooks/threads/data-deletion", data={"signed_request": sr})
    assert r.status_code == 200
    body = r.json()
    assert body["confirmation_code"]
    assert "url" in body
