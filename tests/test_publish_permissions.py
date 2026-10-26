"""Publish permission gate — only admins and can_publish operators may publish."""
import uuid

from fastapi.testclient import TestClient

from app.main import app


def test_operator_without_permission_forbidden(make_operator):
    make_operator("nopub@socialpilot.local", "password123", role="operator")
    with TestClient(app) as c:
        c.post("/auth/login", json={"email": "nopub@socialpilot.local", "password": "password123"})
        r = c.post(f"/posts/{uuid.uuid4()}/publish", json={"confirm": True})
        assert r.status_code == 403


def test_operator_with_permission_passes_gate(make_operator, db):
    op = make_operator("publisher@socialpilot.local", "password123", role="operator")
    op.can_publish = True
    db.commit()
    with TestClient(app) as c:
        c.post("/auth/login", json={"email": "publisher@socialpilot.local", "password": "password123"})
        # 404 (post not found) proves the operator passed the permission gate.
        r = c.post(f"/posts/{uuid.uuid4()}/publish", json={"confirm": True})
        assert r.status_code == 404


def test_admin_passes_gate(client):
    r = client.post(f"/posts/{uuid.uuid4()}/publish", json={"confirm": True})
    assert r.status_code == 404  # past the gate → post-not-found


def test_admin_can_grant_and_revoke_publish(make_operator, client):
    op = make_operator("grantee@socialpilot.local", "password123", role="operator")
    r = client.patch(f"/users/{op.id}", json={"can_publish": True})
    assert r.status_code == 200
    assert r.json()["can_publish"] is True
    r = client.patch(f"/users/{op.id}", json={"can_publish": False})
    assert r.status_code == 200
    assert r.json()["can_publish"] is False
