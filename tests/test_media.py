"""Media upload / list / serve tests."""
import io

from PIL import Image


def test_media_upload_and_serve(client):
    buf = io.BytesIO()
    Image.new("RGB", (3, 2), "red").save(buf, "PNG")
    data = buf.getvalue()

    r = client.post("/media/upload", files={"file": ("test.png", data, "image/png")})
    assert r.status_code == 201
    item = r.json()
    assert item["kind"] == "image"
    assert item["width"] == 3 and item["height"] == 2
    assert item["size_bytes"] == len(data)

    assert any(m["id"] == item["id"] for m in client.get("/media").json())

    fr = client.get(f"/media/{item['id']}/file")
    assert fr.status_code == 200
    assert len(fr.content) > 0


def test_media_requires_auth(anon_client):
    assert anon_client.get("/media").status_code == 401


def test_media_delete(client):
    buf = io.BytesIO()
    Image.new("RGB", (3, 2), "blue").save(buf, "PNG")
    item = client.post("/media/upload", files={"file": ("del.png", buf.getvalue(), "image/png")}).json()

    assert client.delete(f"/media/{item['id']}").status_code == 204
    assert all(m["id"] != item["id"] for m in client.get("/media").json())
    # file removed from disk
    assert client.get(f"/media/{item['id']}/file").status_code == 404


def test_upload_rejects_unknown_extension(client):
    r = client.post("/media/upload", files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert r.status_code == 400


def test_upload_rejects_fake_image(client):
    r = client.post("/media/upload", files={"file": ("fake.png", b"this is not an image", "image/png")})
    assert r.status_code == 400
