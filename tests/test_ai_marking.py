"""The Article 50(2) mark: it is in the pixels, it survives the platform's re-encode, and an unmarkable
asset is stored honestly as unmarked.

Measured on this machine (2026-10-05, social-pilot's own venv): a byte copy keeps the mark, a JPEG re-encode
at quality 82 keeps it, a resize to 640x640 destroys it (the unmarked control is correctly reported as
unmarked). That is why the mark is applied in `_save_generated_image`, where the bytes are already at the
platform's publish size — nothing resizes a generated image afterwards.

The cases below are the ones that would catch a regression: if someone moves the marking before a resize, or
stops verifying what they wrote, or lets the registry hold client text, one of these fails.
"""
from __future__ import annotations

import io
import pathlib

import pytest
from PIL import Image

from app import ai_marking
from app.ai_marking import PAYLOAD, detect_bytes, mark_bytes, mark_generated


def _png(size: tuple[int, int], colour: tuple[int, int, int] = (30, 40, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def publish_size_png() -> bytes:
    """A stand-in for what the provider returns: one of the sizes this app asks for, with detail to carry a
    mark (a flat colour block is a worst case for any pixel-domain watermark)."""
    img = Image.new("RGB", (512, 512), (28, 34, 48))
    for i in range(0, 512, 16):
        for j in range(0, 512, 16):
            img.putpixel((i, j), (150 + (i % 90), 170, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def marked(publish_size_png) -> bytes:
    return mark_bytes(publish_size_png)


def test_the_mark_reads_back_out_of_the_pixels(marked):
    assert detect_bytes(marked) == PAYLOAD


def test_the_mark_survives_the_re_encode_every_platform_does(marked):
    buf = io.BytesIO()
    Image.open(io.BytesIO(marked)).convert("RGB").save(buf, "JPEG", quality=82)
    assert detect_bytes(buf.getvalue()) == PAYLOAD, (
        "the mark did not survive a JPEG re-encode — the mark would be decoration on every platform that "
        "re-encodes what we upload")


def test_an_unmarked_image_is_not_reported_as_marked(publish_size_png):
    """The control. Without this case, a detector that answered yes to everything would look correct."""
    assert detect_bytes(publish_size_png) is None


def test_an_image_too_small_to_carry_a_mark_is_stored_unmarked(tmp_path, monkeypatch):
    """Fail closed: the app stores the asset as unmarked, and the production-ring publish gate refuses it.

    The library refuses outright on a 4x3 image ("can embed 0.0kb, the watermark is 0.175kb"). `mark_generated`
    must turn that into `verified=False` rather than an exception in the middle of a generation.
    """
    monkeypatch.setattr(ai_marking, "REGISTRY", str(tmp_path / "registry.jsonl"))
    tiny = _png((4, 3))
    data, verified = mark_generated(tiny, "tiny-media-id")
    assert verified is False
    assert data == tiny, "an unmarkable asset must be stored as it came, not half-processed"
    assert not (tmp_path / "registry.jsonl").exists(), "nothing was marked, so nothing may be recorded"


def test_a_marked_asset_records_provenance_without_client_text(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_marking, "REGISTRY", str(tmp_path / "registry.jsonl"))
    prompt_text = "SELL-OUR-CLIENT-SECRET-PRODUCT"
    data, verified = mark_generated(_png((512, 512)), "media-id-under-test")
    assert verified is True
    assert detect_bytes(data) == PAYLOAD
    line = (tmp_path / "registry.jsonl").read_text().strip()
    assert "media-id-under-test" in line and "issuer" in line
    assert prompt_text not in line and "prompt" not in line, (
        "the registry is hashes and identifiers only — a prompt or client text in it would be a leak")
    assert PAYLOAD in line


def test_the_verification_is_what_sets_the_column():
    """The source-level claim this file is about: `ai_marking` comes from reading the written bytes back.

    A column set from the intention to mark would pass every behavioural case above while lying about an asset
    whose marking silently failed — so the assertion is on the code that decides it.
    """
    source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "services" / "pipeline.py").read_text()
    created = source.split("def _save_generated_image", 1)[1].split("\ndef ", 1)[0]
    assert "mark_generated(data, uid)" in created
    assert 'ai_marking="watermark" if verified else "none"' in created
    assert "path.write_bytes(marked)" in created


def test_the_mark_survives_the_platform_fit(tmp_path, monkeypatch):
    """The finding that mattered: a platform's cap re-encodes the image, and that used to erase the mark.

    `fit_for_platform` resizes and re-encodes to JPEG for a platform whose limits are below the generated size
    — the same operation the module docstring measures as destructive. The DB row said `ai_marking='watermark'`
    and the published file would have carried none. The bytes returned here are the bytes that get uploaded, so
    the assertion is on *those*, and it covers both branches:

    * an image already inside the cap comes back untouched, mark and all;
    * an image over the cap is re-encoded for it, and the mark has to be in the re-encoded bytes.
    """
    from app import ai_marking as marking_module
    from app import media
    from app.media import PLATFORM_MEDIA_LIMITS, fit_for_platform

    # The registry goes to a temporary file: `mark_generated` records provenance, and a case that let it write
    # into the project's own data/ would leave the machine's real registry holding test rows (the same trap
    # the older cases in this file already avoid, and one a reviewer caught in these two).
    monkeypatch.setattr(marking_module, "REGISTRY", str(tmp_path / "registry.jsonl"))
    source = tmp_path / "generated.png"
    marked, verified = mark_generated(_png((1024, 1024)), "media-under-test")
    assert verified, "the fixture must be markable, or this case proves nothing"
    source.write_bytes(marked)

    # (a) inside the platform's limits: returned as it stands
    data, mime = fit_for_platform(source, "bluesky", "image/png", ai_generated=True)
    assert detect_bytes(data) == PAYLOAD, "the mark must still be there when nothing was re-encoded"
    assert mime == "image/png"

    # (b) over the limits: re-encoded to JPEG for the cap, and re-marked
    limits = dict(PLATFORM_MEDIA_LIMITS)
    limits["probe-cap"] = {"max_bytes": 60_000, "max_dim": 1024}
    original = media.PLATFORM_MEDIA_LIMITS
    media.PLATFORM_MEDIA_LIMITS = limits
    try:
        data, mime = fit_for_platform(source, "probe-cap", "image/png", ai_generated=True)
    finally:
        media.PLATFORM_MEDIA_LIMITS = original
    assert mime == "image/jpeg" and len(data) <= 60_000, (mime, len(data))
    assert len(data) != len(marked), "the fit must actually have re-encoded, or this branch proves nothing"
    assert detect_bytes(data) == PAYLOAD, (
        "the mark did not survive the re-encode for the cap: the row would claim a watermark the uploaded "
        "bytes do not carry")


def test_a_fit_that_cannot_keep_the_mark_refuses_rather_than_publishing(tmp_path, monkeypatch):
    """Fail closed: when the cap is too small to hold a marked image, the asset does not go out unmarked."""
    from app import ai_marking as marking_module
    from app import media

    monkeypatch.setattr(marking_module, "REGISTRY", str(tmp_path / "registry.jsonl"))
    from app.media import PLATFORM_MEDIA_LIMITS, MarkingLost, fit_for_platform

    source = tmp_path / "generated.png"
    marked, _ = mark_generated(_png((512, 512)), "media-under-test-2")
    source.write_bytes(marked)

    limits = dict(PLATFORM_MEDIA_LIMITS)
    limits["probe-tiny"] = {"max_bytes": 3_000, "max_dim": 64}      # far below what a marked image needs
    original = media.PLATFORM_MEDIA_LIMITS
    media.PLATFORM_MEDIA_LIMITS = limits
    try:
        with pytest.raises(MarkingLost):
            fit_for_platform(source, "probe-tiny", "image/png", ai_generated=True)
    finally:
        media.PLATFORM_MEDIA_LIMITS = original
