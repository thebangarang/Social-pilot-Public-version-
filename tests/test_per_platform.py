"""Per-platform content variants — parsing, resolution, publish caption."""
import json

from sqlalchemy import select

from app.agents import _parse_proposals
from app.models import ChatSession, Operator, Post
from app.services.pipeline import variant_for


def _admin(db) -> Operator:
    return db.scalar(select(Operator).where(Operator.email == "test-admin@socialpilot.local"))


def test_parse_proposals_with_variants():
    raw = json.dumps({
        "proposals": [{
            "number": 1,
            "concept": "summer sale",
            "media_prompt": "a bright storefront",
            "media_prompt_fr": "une devanture lumineuse",
            "style_notes": "warm tones",
            "variants": {
                "tiktok": {"caption": "SALE 🔥", "hook": "Wait", "cta": "Shop now", "hashtags": ["#sale", "#summer"]},
                "linkedin": {"caption": "Our summer sale starts now.", "hook": "We're live", "cta": "Learn more", "hashtags": ["#business"]},
            },
        }]
    })
    out = _parse_proposals(raw)
    assert len(out) == 1
    p = out[0]
    assert p["concept"] == "summer sale"
    assert set(p["variants"].keys()) == {"tiktok", "linkedin"}
    assert p["variants"]["tiktok"]["caption"] == "SALE 🔥"
    # Flat defaults = first variant, for backward-compat readers.
    assert p["caption"] == "SALE 🔥"
    assert p["hook"] == "Wait"
    assert p["hashtags"] == ["#sale", "#summer"]


def test_parse_proposals_flat_backward_compat():
    raw = json.dumps({
        "proposals": [{"number": 1, "caption": "Hello", "hook": "Hi", "cta": "Go", "hashtags": ["#a"], "media_prompt": "mp"}]
    })
    p = _parse_proposals(raw)[0]
    assert p["caption"] == "Hello"
    assert p["variants"] == {}


def test_variant_for_resolution():
    content = {
        "caption": "default",
        "variants": {"tiktok": {"caption": "TikTok caption"}, "linkedin": {"caption": "LinkedIn caption"}},
    }
    assert variant_for(content, "tiktok")["caption"] == "TikTok caption"
    assert variant_for(content, "linkedin")["caption"] == "LinkedIn caption"
    # Platform without a variant → flat fallback.
    assert variant_for(content, "facebook")["caption"] == "default"
    # Flat-only content.
    assert variant_for({"caption": "flat"}, "tiktok")["caption"] == "flat"


def test_publish_uses_per_platform_caption(client, db):
    admin = _admin(db)
    s = ChatSession(owner_id=admin.id, title="x")
    db.add(s)
    db.flush()
    post = Post(
        owner_id=admin.id, session_id=s.id, title="t",
        content={
            "concept": "sale",
            "media_prompt": "mp",
            "variants": {
                "tiktok": {"caption": "TK caption", "hook": "", "cta": "", "hashtags": []},
                "instagram": {"caption": "IG caption", "hook": "", "cta": "", "hashtags": []},
            },
        },
        media_ids=["abc"], media_type="image", status="compliance_ok",
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    r = client.post(f"/posts/{post.id}/publications", json={"platforms": ["tiktok", "instagram"]})
    assert r.status_code == 200
    pubs = {p["platform"]: p for p in r.json()}
    assert pubs["tiktok"]["caption"] == "TK caption"
    assert pubs["instagram"]["caption"] == "IG caption"


def test_fit_for_platform_compresses_oversized_image(tmp_path):
    import os

    from PIL import Image

    from app.media import fit_for_platform

    # Random RGB noise compresses poorly → a >1MB PNG.
    im = Image.frombytes("RGB", (1000, 1000), os.urandom(1000 * 1000 * 3))
    p = tmp_path / "big.png"
    im.save(p, "PNG")
    assert p.stat().st_size > 1_000_000

    data, mime = fit_for_platform(p, "bluesky", "image/png")
    assert mime == "image/jpeg"
    assert len(data) <= 1_000_000

    # A small image is passed through unchanged.
    small = tmp_path / "small.png"
    Image.new("RGB", (100, 100), (255, 0, 0)).save(small, "PNG")
    data2, mime2 = fit_for_platform(small, "bluesky", "image/png")
    assert mime2 == "image/png"
    assert data2 == small.read_bytes()

    # Unknown platform passes through unchanged.
    data3, mime3 = fit_for_platform(small, "unknown-platform", "image/png")
    assert data3 == small.read_bytes()


def test_generate_media_per_platform(monkeypatch, db):
    import uuid as _uuid
    from types import SimpleNamespace

    from app.services import pipeline

    admin = _admin(db)
    s = ChatSession(owner_id=admin.id, title="x")
    db.add(s)
    db.flush()
    post = Post(
        owner_id=admin.id, session_id=s.id, title="t",
        content={
            "media_prompt": "a shop",
            "variants": {
                "tiktok": {"caption": "a", "media_format": "9:16"},
                "linkedin": {"caption": "b", "media_format": "1.91:1"},
            },
        },
        media_type="image", status="concept_validated",
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    sizes = []

    def fake_image(prompt, reference_images=None, size=None):
        sizes.append(size)
        return b"x"

    def fake_save(db, owner_id, data):
        return SimpleNamespace(id=_uuid.uuid4())

    monkeypatch.setattr(pipeline, "image_generation", fake_image)
    monkeypatch.setattr(pipeline, "_save_generated_image", fake_save)

    result = pipeline.generate_media(db, post, admin)
    assert set(result.media_variants.keys()) == {"tiktok", "linkedin"}
    assert len(result.media_ids) == 2
    assert sizes == ["1024x1792", "1344x704"]
    assert result.status == "media_ready"
