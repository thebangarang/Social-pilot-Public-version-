"""Does the mark survive the TikTok path? Measured, on the adapter's own conversion.

A second review raised the question of the tiktok adapter: it turns a still image into a 3-second MP4 through
ffmpeg (`scale` + `pad` + x264), which is a lossier re-encode than the JPEG path the marking module measures,
and if it took the mark the honest position would be to refuse AI-generated images on TikTok until a video
marker exists. It does not: the measurement below reads the mark out of a frame of the video the adapter
produces, driving the adapter's own `_image_to_mp4` so the case covers whatever the filter chain becomes.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.ai_marking import PAYLOAD, detect_bytes, mark_generated

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def _generated_png(monkeypatch, tmp_path, size=(1200, 1200)) -> Path:
    from app import ai_marking as marking

    monkeypatch.setattr(marking, "REGISTRY", str(tmp_path / "registry.jsonl"))
    img = _image(size)
    marked, verified = mark_generated(_png(img), "tiktok-marking-case")
    assert verified, "the fixture must be markable, or this case proves nothing"
    path = tmp_path / "generated.png"
    path.write_bytes(marked)
    return path


def _image(size):
    from PIL import Image

    img = Image.new("RGB", size, (28, 34, 48))
    for i in range(0, size[0], 24):
        for j in range(0, size[1], 24):
            img.putpixel((i, j), (150 + (i % 90), 170, 200))
    return img


def _png(img) -> bytes:
    import io

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_the_mark_survives_the_tiktok_conversion(tmp_path, monkeypatch):
    """The generated image goes to TikTok with its mark, and the adapter checks rather than assumes it.

    The generator asks the provider for 9:16 at 1024x1792 (app/services/pipeline.py), and the adapter used to
    rescale every image to 1080x1920 on the way into ffmpeg — which is a resize, which is what takes the mark
    (measured in app/ai_marking.py). The filter chain now runs only when the aspect is not already TikTok's,
    and `_image_to_mp4(..., ai_generated=True)` reads the mark back out of a frame of the video it returns.
    """
    from app.media import video_frame
    from app.platforms.tiktok import TikTokAdapter

    source = _generated_png(monkeypatch, tmp_path, size=(1024, 1792))     # the generator's own 9:16 size
    adapter = TikTokAdapter.__new__(TikTokAdapter)
    video = adapter._image_to_mp4(source, ai_generated=True)

    frame = video_frame(video)
    assert detect_bytes(frame) == PAYLOAD, (
        "the mark did not survive the tiktok conversion: this post would carry no mark while the media row "
        "says otherwise")


def test_a_rescale_that_would_take_the_mark_refuses_the_publication(tmp_path, monkeypatch):
    """A square image sent to TikTok is rescaled, the resize takes the mark, and that must refuse.

    This is the other side of the same measurement: 1200x1200 is far enough from 9:16 that the adapter's filter
    chain runs, and the measured result is that a mark does not survive a resize. So the adapter's own check
    (not a copy in the test) raises — the post fails with a sentence instead of publishing unmarked content.
    """
    from app.media import MarkingLost
    from app.platforms.tiktok import TikTokAdapter

    source = _generated_png(monkeypatch, tmp_path, size=(1200, 1200))
    adapter = TikTokAdapter.__new__(TikTokAdapter)
    with pytest.raises(MarkingLost):
        adapter._image_to_mp4(source, ai_generated=True)


def test_a_square_image_with_no_mark_is_still_converted(tmp_path):
    """The check applies to AI-generated assets. A client's own photo is converted as before.

    Worth pinning: the runtime check must not become a blanket refusal of TikTok image posts, and the way to
    show that is an ordinary image going through the same call. The parameter is required as of this commit
    (it was `= False`, and a caller that forgot it would skip the Article 50(2) check), so the call passes
    `ai_generated=False` explicitly rather than relying on a default that no longer exists.
    """
    from app.platforms.tiktok import TikTokAdapter

    plain = tmp_path / "client-photo.png"
    plain.write_bytes(_png(_image((1200, 1200))))
    video = TikTokAdapter.__new__(TikTokAdapter)._image_to_mp4(plain, ai_generated=False)
    assert len(video) > 1000


def test_an_odd_sized_image_still_encodes_and_keeps_its_mark(tmp_path, monkeypatch):
    """An image within a hair of 9:16 but odd-sized used to be fixed by the rescale, which no longer runs.

    libx264 with yuv420p needs even dimensions, so dropping the filter chain for images already at TikTok's
    aspect removed a guarantee they had been getting for free. Padding restores it — and a pad adds pixels
    instead of resampling them, so this case also asserts the mark survives it (a rescale would not).
    """
    from app.media import video_frame
    from app.platforms.tiktok import TikTokAdapter

    source = _generated_png(monkeypatch, tmp_path, size=(1025, 1821))     # odd, and 9:16 within 0.02
    video = TikTokAdapter.__new__(TikTokAdapter)._image_to_mp4(source, ai_generated=True)

    frame = video_frame(video)
    import io

    from PIL import Image

    with Image.open(io.BytesIO(frame)) as im:
        assert im.size[0] % 2 == 0 and im.size[1] % 2 == 0, f"uneven dimensions reached the encoder: {im.size}"
    assert detect_bytes(frame) == PAYLOAD, "padding must not cost the mark — it adds pixels, it does not resample"
