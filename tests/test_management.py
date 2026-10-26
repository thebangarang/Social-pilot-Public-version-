"""Management / telemetry API tests."""


def test_management_disabled_without_token(client):
    # No SP_MANAGEMENT_TOKEN in the test env → API disabled (404).
    assert client.get("/api/v1/management/health").status_code == 404
    assert client.get("/api/v1/management/stats").status_code == 404


def test_management_token_auth(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")

    assert client.get("/api/v1/management/health").status_code == 401
    assert client.get(
        "/api/v1/management/health", headers={"X-Management-Token": "wrong"}
    ).status_code == 401

    r = client.get("/api/v1/management/health", headers={"X-Management-Token": "secret-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["instance_id"]
    assert body["version"]
    assert "uptime_seconds" in body


def test_management_stats_is_telemetry_only(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    r = client.get("/api/v1/management/stats", headers={"X-Management-Token": "secret-token"})
    assert r.status_code == 200
    body = r.json()
    for key in ("operators", "posts_total", "connected_accounts", "usage_all_time_usd"):
        assert key in body
    # No personal data / content leak: no emails, no comment text, no post captions.
    assert "@" not in str(body)


def test_dependencies_reports_the_breakers_behind_the_token(client, monkeypatch):
    """A halted dependency has to be visible where the operator looks, not only in the log.

    The endpoint is telemetry: names and states, no content, no personal data.
    """
    from app import breaker
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    assert client.get("/api/v1/management/dependencies").status_code == 401

    breaker.reset()
    try:
        breaker.breaker_for("tiktok").threshold = 1
        breaker.record("tiktok", False)               # one failure opens it (threshold 1)
        r = client.get("/api/v1/management/dependencies", headers={"X-Management-Token": "secret-token"})
        assert r.status_code == 200
        body = r.json()
        assert body["halted"] == ["tiktok"]
        assert body["breakers"]["tiktok"]["state"] == "open"
        assert body["breakers"]["tiktok"]["consecutive_failures"] == 1
        assert body["breakers"]["tiktok"]["retry_in"] > 0
        assert "@" not in str(body)
    finally:
        breaker.reset()
