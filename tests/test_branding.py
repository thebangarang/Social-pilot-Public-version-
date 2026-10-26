"""Branding kit CRUD tests."""


def test_branding_crud(client):
    assert client.get("/branding").json() == []
    r = client.post("/branding", json={"kind": "color", "filename": "Couleur primaire", "notes": "#5b8cff"})
    assert r.status_code == 201
    aid = r.json()["id"]
    assert any(a["id"] == aid for a in client.get("/branding").json())
    assert client.delete(f"/branding/{aid}").status_code == 204
    assert client.get("/branding").json() == []


def test_branding_invalid_kind(client):
    assert client.post("/branding", json={"kind": "bogus", "filename": "x"}).status_code == 422
