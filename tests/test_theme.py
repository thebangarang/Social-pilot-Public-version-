"""Theme (whitelabel) endpoints tests."""


def test_themes_seeded(client):
    themes = client.get("/theme/all").json()
    assert len(themes) >= 3
    active = client.get("/theme").json()
    assert active["is_active"] is True


def test_non_admin_cannot_activate_theme(make_operator, anon_client):
    make_operator("op-theme@socialpilot.local", "password123", role="operator")
    anon_client.post("/auth/login", json={"email": "op-theme@socialpilot.local", "password": "password123"})
    assert anon_client.post("/theme/ivory/activate").status_code == 403


def test_admin_can_activate_theme(client):
    r = client.post("/theme/ivory/activate")
    assert r.status_code == 200
    assert r.json()["id"] == "ivory"
    assert client.get("/theme").json()["id"] == "ivory"
