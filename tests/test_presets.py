"""Style preset tests."""


def test_presets_seeded(client):
    presets = client.get("/presets").json()
    assert len(presets) >= 3
    assert all(p["is_builtin"] for p in presets)


def test_preset_create_delete(client):
    r = client.post("/presets", json={"name": "Mon style", "directives": "blabla"})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["is_builtin"] is False
    assert client.delete(f"/presets/{pid}").status_code == 204


def test_builtin_not_deletable(client):
    assert client.delete("/presets/minimal-clean").status_code == 409
