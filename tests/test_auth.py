"""Authentication, 2FA, password management, and admin user-management tests."""
import pyotp

from app.security import encrypt_secret


def test_login_success(make_operator, anon_client):
    make_operator("me@socialpilot.local", "password123")
    r = anon_client.post("/auth/login", json={"email": "me@socialpilot.local", "password": "password123"})
    assert r.status_code == 200
    assert r.json()["requires_2fa"] is False
    assert anon_client.get("/auth/me").json()["email"] == "me@socialpilot.local"


def test_login_wrong_password(make_operator, anon_client):
    make_operator("wrong@socialpilot.local", "password123")
    assert anon_client.post("/auth/login", json={"email": "wrong@socialpilot.local", "password": "wrong"}).status_code == 401


def test_login_2fa_flow(make_operator, anon_client, db):
    op = make_operator("2fa@socialpilot.local", "password123")
    secret = pyotp.random_base32()
    op.totp_secret = encrypt_secret(secret)
    op.totp_enabled = True
    db.commit()

    r = anon_client.post("/auth/login", json={"email": "2fa@socialpilot.local", "password": "password123"})
    assert r.json()["requires_2fa"] is True
    challenge = r.json()["challenge"]

    assert anon_client.post("/auth/login/2fa", json={"challenge": challenge, "code": "000000"}).status_code == 401

    code = pyotp.TOTP(secret).now()
    r2 = anon_client.post("/auth/login/2fa", json={"challenge": challenge, "code": code})
    assert r2.status_code == 200
    assert anon_client.get("/auth/me").json()["email"] == "2fa@socialpilot.local"


def test_password_change(make_operator, anon_client):
    make_operator("pw@socialpilot.local", "oldpass123")
    anon_client.post("/auth/login", json={"email": "pw@socialpilot.local", "password": "oldpass123"})

    assert anon_client.post("/auth/password", json={"current_password": "nope", "new_password": "newpass456"}).status_code == 401
    assert anon_client.post("/auth/password", json={"current_password": "oldpass123", "new_password": "newpass456"}).status_code == 200

    anon_client.post("/auth/logout")
    assert anon_client.post("/auth/login", json={"email": "pw@socialpilot.local", "password": "newpass456"}).status_code == 200


def test_2fa_setup_enable_disable(make_operator, anon_client):
    make_operator("self@socialpilot.local", "password123")
    anon_client.post("/auth/login", json={"email": "self@socialpilot.local", "password": "password123"})

    s = anon_client.get("/auth/2fa/setup").json()
    assert s["secret"]
    assert s["qr_data_uri"].startswith("data:image/svg+xml;base64,")

    code = pyotp.TOTP(s["secret"]).now()
    assert anon_client.post("/auth/2fa/enable", json={"code": code}).status_code == 200
    assert anon_client.get("/auth/me").json()["totp_enabled"] is True

    assert anon_client.post("/auth/2fa/disable", json={"password": "password123"}).status_code == 200
    assert anon_client.get("/auth/me").json()["totp_enabled"] is False


def test_non_admin_cannot_list_users(make_operator, anon_client):
    make_operator("op@socialpilot.local", "password123", role="operator")
    anon_client.post("/auth/login", json={"email": "op@socialpilot.local", "password": "password123"})
    assert anon_client.get("/users").status_code == 403


def test_admin_crud_users(make_operator, anon_client):
    make_operator("boss@socialpilot.local", "password123", role="admin")
    anon_client.post("/auth/login", json={"email": "boss@socialpilot.local", "password": "password123"})

    r = anon_client.post("/users", json={"name": "New User", "email": "new@socialpilot.local",
                                         "role": "operator", "password": "password123"})
    assert r.status_code == 201
    uid = r.json()["id"]

    assert any(u["email"] == "new@socialpilot.local" for u in anon_client.get("/users").json())
    assert anon_client.patch(f"/users/{uid}", json={"is_active": False}).json()["is_active"] is False
    assert anon_client.delete(f"/users/{uid}").status_code == 204


def test_admin_cannot_delete_self(make_operator, anon_client):
    admin = make_operator("solo@socialpilot.local", "password123", role="admin")
    anon_client.post("/auth/login", json={"email": "solo@socialpilot.local", "password": "password123"})
    assert anon_client.delete(f"/users/{admin.id}").status_code == 409


def test_last_admin_guard_demote(make_operator, anon_client):
    a1 = make_operator("ga1@socialpilot.local", "password123", role="admin")
    a2 = make_operator("ga2@socialpilot.local", "password123", role="admin")
    anon_client.post("/auth/login", json={"email": "ga1@socialpilot.local", "password": "password123"})
    # a1 demotes a2 → allowed (a1 remains the sole admin)
    assert anon_client.patch(f"/users/{a2.id}", json={"role": "operator"}).status_code == 200
    # a1 (now the last admin) cannot demote itself
    assert anon_client.patch(f"/users/{a1.id}", json={"role": "operator"}).status_code == 409


def test_update_me_language(make_operator, anon_client):
    make_operator("lang@socialpilot.local", "password123")
    anon_client.post("/auth/login", json={"email": "lang@socialpilot.local", "password": "password123"})

    assert anon_client.patch("/auth/me", json={"language": "en"}).json()["language"] == "en"
    assert anon_client.get("/auth/me").json()["language"] == "en"
    # unsupported language rejected
    assert anon_client.patch("/auth/me", json={"language": "xx"}).status_code == 422


def test_protected_routes_require_auth(anon_client):
    assert anon_client.get("/users").status_code == 401
    assert anon_client.get("/theme").status_code == 401
    assert anon_client.get("/auth/me").status_code == 401
