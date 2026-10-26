"""Social connection (mock adapter) endpoints tests."""


def test_list_empty(client):
    assert client.get("/connect").json() == []


def test_admin_mock_connect_and_revoke(client):
    r = client.post("/connect/tiktok/mock")
    assert r.status_code == 201
    acc = r.json()
    assert acc["platform"] == "tiktok"
    assert acc["handle"].startswith("tiktok_")
    assert acc["account_meta"]["mock"] is True
    assert "token_enc" not in acc  # token never exposed

    assert any(a["id"] == acc["id"] for a in client.get("/connect").json())

    assert client.delete(f"/connect/{acc['id']}").status_code == 204
    assert client.get("/connect").json() == []


def test_unknown_platform(client):
    assert client.post("/connect/myspace/mock").status_code == 400


def test_linkedin_mock_connect(client):
    r = client.post("/connect/linkedin/mock")
    assert r.status_code == 201
    acc = r.json()
    assert acc["platform"] == "linkedin"
    assert acc["handle"].startswith("linkedin_")
    assert client.delete(f"/connect/{acc['id']}").status_code == 204


def test_x_mock_connect(client):
    r = client.post("/connect/x/mock")
    assert r.status_code == 201
    acc = r.json()
    assert acc["platform"] == "x"
    assert acc["handle"].startswith("x_")
    assert client.delete(f"/connect/{acc['id']}").status_code == 204


def test_additional_networks_mock_connect(client):
    for platform in ("youtube", "threads", "pinterest", "google", "bluesky"):
        r = client.post(f"/connect/{platform}/mock")
        assert r.status_code == 201
        acc = r.json()
        assert acc["platform"] == platform
        assert acc["handle"].startswith(f"{platform}_")
        assert client.delete(f"/connect/{acc['id']}").status_code == 204


def test_non_admin_cannot_connect(make_operator, anon_client):
    make_operator("conn-op@socialpilot.local", "password123", role="operator")
    anon_client.post("/auth/login", json={"email": "conn-op@socialpilot.local", "password": "password123"})
    assert anon_client.post("/connect/tiktok/mock").status_code == 403
    assert anon_client.delete("/connect/00000000-0000-0000-0000-000000000000").status_code == 403


def test_default_account_behavior(client):
    a = client.post("/connect/instagram/mock").json()
    b = client.post("/connect/instagram/mock").json()
    assert a["is_default"] is True   # first account is default
    assert b["is_default"] is False

    r = client.post(f"/connect/{b['id']}/default").json()
    assert r["is_default"] is True

    ig = [x for x in client.get("/connect").json() if x["platform"] == "instagram"]
    defaults = [x for x in ig if x["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == b["id"]
