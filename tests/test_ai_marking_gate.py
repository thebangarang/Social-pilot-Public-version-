"""The Article 50(2) publish gate — the answer to a second review's AI Act finding.

The `[ai]` declaration in `release.toml` used to defer the marking duty with the reason *"generation
calls are not wired yet"*. The code disagreed: `/posts/{id}/generate-media` calls the image provider
today. So the deferral was justified by a statement that was not true, and nothing stopped an unmarked
generated image from reaching a client's feed.

The declaration now says what is open (the mark itself), and these cases hold the interim control: an
AI-generated asset that carries no mark cannot be published. Fail-closed on purpose — the alternative is
publishing unmarked AI content for a client, which is the exposure the rule is about.
"""
from __future__ import annotations

import pathlib
import types
import uuid

import pytest

from app.services import pipeline

from app.models import ChatSession, MediaItem, Post, Publication
from app.services.pipeline import (
    MARKED,
    UnmarkedGeneratedMedia,
    assert_publishable,
    publish_publication,
    unmarked_generated_media,
)


@pytest.fixture()
def prod_ring(monkeypatch):
    """Make the settings report the production ring.

    The suite runs with `SP_ENV=test`, which is not production — and that is the point of the ring
    scoping: the refusal exists for the ring that publishes to a client's account. So the cases that
    assert a refusal put the app in production for the length of the test.
    """
    monkeypatch.setattr(pipeline, "get_settings",
                        lambda: types.SimpleNamespace(is_production=True, env="production"))


@pytest.fixture()
def post(make_operator, db):
    # unique per test: the suite shares one database file (a session-scoped engine)
    op = make_operator(f"marking-{uuid.uuid4().hex[:8]}@socialpilot.local")
    session = ChatSession(owner_id=op.id, title="marking gate")
    db.add(session)
    db.commit()
    p = Post(owner_id=op.id, session_id=session.id, status="media_ready", content={})
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _media(db, op_id, *, generated: bool, marking: str) -> MediaItem:
    item = MediaItem(owner_id=op_id, kind="image", original_name="x.png", stored_path="media/x.png",
                     mime="image/png", size_bytes=10, ai_generated=generated, ai_marking=marking)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _publication(db, post) -> Publication:
    pub = Publication(post_id=post.id, platform="instagram", caption="", media_ids=[], status="ready")
    db.add(pub)
    db.commit()
    db.refresh(pub)
    return pub


def test_generated_asset_defaults_to_unmarked(post, db):
    item = _media(db, post.owner_id, generated=True, marking="none")
    post.media_ids = [str(item.id)]
    db.commit()
    assert [m.id for m in unmarked_generated_media(db, post)] == [item.id]


def test_a_marked_generated_asset_passes(post, db):
    item = _media(db, post.owner_id, generated=True, marking="watermark")
    post.media_ids = [str(item.id)]
    db.commit()
    assert unmarked_generated_media(db, post) == []


def test_an_uploaded_asset_is_not_the_gate_s_business(post, db):
    # ai_generated=False with ai_marking="none" is every hand-uploaded file: it must not be refused.
    item = _media(db, post.owner_id, generated=False, marking="none")
    post.media_ids = [str(item.id)]
    db.commit()
    assert unmarked_generated_media(db, post) == []


def test_a_pending_mark_is_not_a_mark(post, db):
    # "pending" is a mark that was meant to be applied and was not — the exact shape that must not pass.
    item = _media(db, post.owner_id, generated=True, marking="pending")
    post.media_ids = [str(item.id)]
    db.commit()
    assert unmarked_generated_media(db, post) == [item]
    assert "pending" not in MARKED


def test_a_mixed_post_still_fails(post, db):
    good = _media(db, post.owner_id, generated=True, marking="c2pa")
    bad = _media(db, post.owner_id, generated=True, marking="none")
    post.media_ids = [str(good.id), str(bad.id)]
    db.commit()
    assert [m.id for m in unmarked_generated_media(db, post)] == [bad.id]


def test_per_platform_variants_are_checked_too(post, db):
    # A post's media lives in media_ids and, for multi-platform posts, in media_variants — the gate has
    # to see both, or a per-platform generated image would slip through.
    item = _media(db, post.owner_id, generated=True, marking="none")
    post.media_variants = {"instagram": str(item.id)}
    db.commit()
    assert [m.id for m in unmarked_generated_media(db, post)] == [item.id]


def test_publishing_an_unmarked_generated_asset_is_refused(post, db, prod_ring):
    item = _media(db, post.owner_id, generated=True, marking="none")
    post.media_ids = [str(item.id)]
    db.commit()
    pub = _publication(db, post)
    with pytest.raises(UnmarkedGeneratedMedia) as err:
        assert_publishable(db, pub)
    assert "Article 50(2)" in str(err.value)


def test_the_refusal_happens_before_any_adapter_runs(post, db, prod_ring):
    """`publish_publication` must refuse before it reaches the platform client.

    Asserted on a post with no connected account and no adapter in sight: if the gate were placed after
    the adapter lookup, this would fail with something else (or, worse, publish).
    """
    item = _media(db, post.owner_id, generated=True, marking="none")
    post.media_ids = [str(item.id)]
    db.commit()
    pub = _publication(db, post)
    with pytest.raises(Exception) as err:
        publish_publication(db, pub)
    assert isinstance(err.value, UnmarkedGeneratedMedia), f"got {type(err.value).__name__}"
    db.refresh(pub)
    assert pub.status != "published"


def test_the_devnet_ring_is_not_blocked_and_says_so(post, db, caplog):
    """Non-production rings log the allowance instead of refusing.

    A hard refusal everywhere would block the devnet's own demo of a flow whose marking is a declared,
    dated item; silence everywhere would be the control decaying into a sentence. So: allowed, and said.
    """
    item = _media(db, post.owner_id, generated=True, marking="none")
    post.media_ids = [str(item.id)]
    db.commit()
    pub = _publication(db, post)
    with caplog.at_level("WARNING", logger="social_pilot.pipeline"):
        assert_publishable(db, pub)          # SP_ENV=test here: not the production ring
    messages = [record.getMessage() for record in caplog.records]
    assert any("unmarked AI-generated media" in m for m in messages), \
        f"the devnet allowance must be visible in the log, not quiet (got {messages})"


def test_an_unrecognised_ring_refuses(post, db, monkeypatch):
    """The default must be the strict one.

    The first version refused only when SP_ENV said "production", while the setting defaults to
    "development" — so a deployment that forgot the variable would have had the control off. Now the
    rings that may proceed are named one by one, and anything unrecognised (including empty) refuses.
    """
    for ring in ("", "development", "staging", "Production!", "prod"):
        with monkeypatch.context() as m:
            m.setattr(pipeline, "get_settings",
                      lambda ring=ring: types.SimpleNamespace(is_production=False, env=ring))
            item = _media(db, post.owner_id, generated=True, marking="none")
            post.media_ids = [str(item.id)]
            db.commit()
            pub = _publication(db, post)
            with pytest.raises(UnmarkedGeneratedMedia):
                assert_publishable(db, pub)


def test_the_gate_is_actually_in_the_publish_path():
    source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "pipeline.py").read_text()
    body = source.split("def publish_publication", 1)[1]
    assert "assert_publishable(db, pub)" in body, "the gate is defined but not called on the publish path"


def test_generated_assets_are_tagged_at_creation():
    """The two columns the publish gate reads are written where the asset is created.

    This case used to assert `ai_marking="none"` — the state before the marking existed. It now asserts what
    the marking build changed: the asset is tagged as generated, and the marking column comes from reading the
    mark back out of the bytes that were written (`app/ai_marking.py` has the end-to-end cases).
    """
    source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "pipeline.py").read_text()
    created = source.split("def _save_generated_image", 1)[1].split("\ndef ", 1)[0]
    assert "ai_generated=True" in created, \
        "an asset the generator produced must be tagged as generated when it is created"
    assert 'ai_marking="watermark" if verified else "none"' in created, \
        "the marking column must come from the verification, never from the intention to mark"
