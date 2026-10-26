"""Team scoping — content is shared across operators (single-tenant)."""
from fastapi.testclient import TestClient

from app.main import app


def _login(email, password):
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    return c


def test_sessions_are_team_visible(make_operator, client):
    # Admin creates a session; an operator must see and open it.
    sid = client.post("/chat/sessions").json()["id"]
    make_operator("member@socialpilot.local", "password123", role="operator")
    c = _login("member@socialpilot.local", "password123")
    sessions = c.get("/chat/sessions").json()
    assert any(s["id"] == sid for s in sessions)
    assert c.get(f"/chat/sessions/{sid}").status_code == 200
