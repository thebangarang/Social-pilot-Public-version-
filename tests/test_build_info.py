"""Build identity tests — the drift detector depends on this being honest."""
from app.build_info import build_info


def test_stamped_image_is_authoritative(monkeypatch):
    """Env stamps win over the git checkout: a built image must report its own build."""
    monkeypatch.setenv("SP_BUILD_SHA", "a" * 40)
    monkeypatch.setenv("SP_BUILD_DATE", "2026-09-20T17:43:50Z")
    monkeypatch.setenv("SP_BUILD_DIRTY", "0")
    build_info.cache_clear()

    info = build_info()

    assert info["source"] == "image"
    assert info["commit"] == "a" * 40
    assert info["commit_short"] == "aaaaaaa"
    assert info["built_at"] == "2026-09-20T17:43:50Z"
    assert info["dirty"] is False
    build_info.cache_clear()


def test_dirty_stamp_is_read_as_a_boolean(monkeypatch):
    monkeypatch.setenv("SP_BUILD_SHA", "b" * 40)
    monkeypatch.setenv("SP_BUILD_DIRTY", "1")
    build_info.cache_clear()

    assert build_info()["dirty"] is True
    build_info.cache_clear()


def test_unstamped_reports_unknown_never_a_guess(monkeypatch):
    """With no stamps the module falls back to git, or says unknown — it never invents a sha."""
    for var in ("SP_BUILD_SHA", "SP_BUILD_DATE", "SP_BUILD_DIRTY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("app.build_info._git", lambda *a: None)
    build_info.cache_clear()

    info = build_info()

    assert info["source"] == "unknown"
    assert info["commit"] == "unknown"
    assert info["commit_short"] == "unknown"
    assert info["built_at"] == "unknown"
    assert info["dirty"] is None  # unknown, not False — no claim made
    build_info.cache_clear()


def test_management_health_reports_the_build(client, monkeypatch):
    """The Command Center reads drift from this payload."""
    from app.config import settings

    monkeypatch.setattr(settings, "management_token", "secret-token")
    monkeypatch.setenv("SP_BUILD_SHA", "c" * 40)
    build_info.cache_clear()

    r = client.get(
        "/api/v1/management/health", headers={"X-Management-Token": "secret-token"}
    )

    assert r.status_code == 200
    build = r.json()["build"]
    assert build["commit"] == "c" * 40
    assert build["commit_short"] == "ccccccc"
    assert build["source"] == "image"
    build_info.cache_clear()


def test_public_health_stays_bare(client):
    """Publishing the commit to anonymous callers is disclosure the app does not need."""
    body = client.get("/health").json()

    assert body == {"status": "ok", "version": "0.1.0"}
