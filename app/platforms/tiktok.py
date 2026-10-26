"""TikTok adapter — real publish via the Content Posting API (direct video upload).

Auth is OAuth 2.0 (access token + refresh token) stored encrypted at rest.
Insights/comments need the separate Business API — return empty for now (phase 2).
Publish is async: init → PUT the video to the upload_url → poll status/fetch.
Sandbox apps publish with privacy_level SELF_ONLY (visible to the owner only)
until TikTok approves the app; `is_aigc` tags AI-generated content.
"""
import logging
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx

from app.security import decrypt_secret

logger = logging.getLogger("social_pilot.tiktok")

API_BASE = "https://open.tiktokapis.com"


class TikTokAdapter:
    name = "tiktok"

    def _token(self, account):
        if account is None or not account.token_enc:
            raise RuntimeError("Compte TikTok non connecté (OAuth requis).")
        return decrypt_secret(account.token_enc)

    @staticmethod
    def _headers(token):
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _check(r):
        if r.status_code >= 400:
            raise RuntimeError(f"TikTok API {r.status_code}: {r.text[:200]}")
        body = r.json()
        err = body.get("error") or {}
        if err.get("code") not in (None, "ok"):
            raise RuntimeError(f"TikTok API error {err.get('code')}: {err.get('message', '')}")

    # -- publish -----------------------------------------------------------
    def publish(self, publication, account=None) -> tuple[str, str]:
        token = self._token(account)
        if publication.remote_post_id:
            return publication.remote_post_id, publication.remote_url or ""

        video = self._resolve_video(publication)

        r = httpx.post(
            f"{API_BASE}/v2/post/publish/video/init/",
            headers=self._headers(token),
            json={
                "post_info": {
                    "title": (publication.caption or "").strip()[:2200],
                    "privacy_level": "SELF_ONLY",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "is_aigc": True,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": len(video),
                    "chunk_size": len(video),
                    "total_chunk_count": 1,
                },
            },
            timeout=30.0,
        )
        self._check(r)
        data = r.json()["data"]
        publish_id = data["publish_id"]
        upload_url = data["upload_url"]

        r = httpx.put(
            upload_url,
            content=video,
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes 0-{len(video)-1}/{len(video)}",
            },
            timeout=180.0,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"TikTok upload {r.status_code}: {r.text[:200]}")

        self._poll(token, publish_id)
        handle = account.handle if account else ""
        url = f"https://www.tiktok.com/@{handle}/video/{publish_id}" if handle else publish_id
        return publish_id, url

    def _poll(self, token, publish_id):
        for _ in range(20):  # ~100s max
            r = httpx.post(
                f"{API_BASE}/v2/post/publish/status/fetch/",
                headers=self._headers(token),
                json={"publish_id": publish_id},
                timeout=30.0,
            )
            self._check(r)
            status = r.json()["data"]["status"]
            if status == "PUBLISH_COMPLETE":
                return
            if status == "FAILED":
                raise RuntimeError(f"TikTok publish failed: {r.json()}")
            time.sleep(5)
        raise RuntimeError("TikTok publish status timeout")

    def _resolve_video(self, publication) -> bytes:
        from app.config import PROJECT_ROOT
        from app.database import SessionLocal
        from app.models import MediaItem

        db = SessionLocal()
        try:
            for mid in (publication.media_ids or []):
                try:
                    item = db.get(MediaItem, uuid.UUID(str(mid)))
                except (ValueError, TypeError):
                    continue
                if item is None:
                    continue
                path = PROJECT_ROOT / item.stored_path
                if not path.exists():
                    continue
                if item.kind == "video":
                    return path.read_bytes()
                if item.kind == "image":
                    return self._image_to_mp4(path, ai_generated=bool(item.ai_generated))
            raise RuntimeError("TikTok : aucun média vidéo/image attaché à la publication.")
        finally:
            db.close()

    def _image_to_mp4(self, image_path, ai_generated: bool) -> bytes:  # noqa: D401
        """Static image → 3s vertical (9:16) MP4 via ffmpeg.

        `ai_generated` is required, with no default: it was `= False` for one revision, which means a caller
        that forgot it would silently skip the Article 50(2) check and publish an unmarked generated video —
        the same shape of hole as a control a missing environment variable switches off. The caller derives it
        from the media row it already loaded (`item.ai_generated`), so there is one place that decides.

        When the image was AI-generated, the mark is read back out of a frame of the video that is about to be
        uploaded — the same rule the image paths apply to the bytes they upload. It was measured to survive
        this conversion (tests/test_ai_marking_video.py), and the measurement is why this is a check rather
        than a refusal; if a future filter chain or encoder takes the mark, the publication fails here instead
        of publishing unmarked generated content.
        """
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.mp4"
            cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image_path), "-t", "3", "-r", "24"]
            if _needs_rescale(image_path):
                # A rescale destroys the Article 50(2) mark (measured, app/ai_marking.py), and the generator
                # asks the provider for 9:16 at 1024x1792 — so scaling that to 1080x1920 would trade the mark
                # for 56 pixels, on every AI-generated TikTok post. The filter chain is applied only when the
                # image is not already at TikTok's aspect. A second review found this by reading the fixture
                # size against the filter chain: the previous test fed it exactly 1080x1920, so the resize it
                # claimed to exercise never ran.
                cmd += ["-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
                               "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black"]
            else:
                # ...and the chain was also what guaranteed an even 1080x1920 output. libx264 with yuv420p
                # needs even dimensions, so an image within a hair of 9:16 but odd-sized would have failed to
                # encode where it used to succeed. Padding is enough — it adds pixels, it does not resample
                # them, so the mark is untouched (a rescale is what takes it, not a pad).
                pad = _even_pad_filter(image_path)
                if pad:
                    cmd += ["-vf", pad]
            cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out)]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
            data = out.read_bytes()
        if ai_generated:
            from app.ai_marking import PAYLOAD, detect_bytes
            from app.media import MarkingLost, video_frame

            if detect_bytes(video_frame(data)) != PAYLOAD:
                raise MarkingLost(
                    "the Article 50(2) mark is not in the video this post would upload: the conversion for "
                    "TikTok took it, so publishing this would make the media row's declaration false.")
        return data

    # -- insights / comments (Business API, phase 2) -----------------------
    def insights(self, publication, account=None) -> dict:
        return {}

    def comments(self, publication, account=None) -> list[dict]:
        return []

    def delete(self, publication, account=None) -> None:
        """Delete a published video (best-effort; TikTok's delete endpoint)."""
        token = self._token(account)
        pid = publication.remote_post_id
        if not pid:
            return
        r = httpx.request(
            "DELETE",
            f"{API_BASE}/v2/post/publish/video/delete/",
            headers=self._headers(token),
            json={"publish_id": pid},
            timeout=30.0,
        )
        if r.status_code >= 400 and r.status_code != 404:
            raise RuntimeError(f"TikTok delete {r.status_code}: {r.text[:200]}")


def _needs_rescale(image_path) -> bool:
    """Is this image far enough from TikTok's 9:16 to be worth the mark it costs to rescale it?

    Read from the file rather than assumed: the generator's 9:16 size is 1024x1792 (app/services/pipeline.py),
    and an image within a hair of the target aspect is uploaded as it stands.
    """
    from PIL import Image

    try:
        with Image.open(image_path) as im:
            width, height = im.size
    except Exception as exc:      # noqa: BLE001 — an unreadable image gets the filter chain, which will say why
        # Recorded rather than swallowed: the previous blanket catch returned True with nothing written, so a
        # library that stopped reading a format would have looked like an ordinary rescale decision (the same
        # "a suppression nothing can observe" shape a reviewer caught in app/ai_marking.py).
        logger.warning("tiktok: could not read %s to decide about rescaling (%s: %s) — using the filter chain",
                       image_path, type(exc).__name__, exc)
        return True
    if not width or not height:
        return True
    return abs((width / height) - (9 / 16)) > 0.02


def _even_pad_filter(image_path) -> str:
    """A pad filter that makes the dimensions even, or "" when they already are.

    libx264 with `-pix_fmt yuv420p` requires even width and height. The rescale branch gets that from its
    1080x1920 target; the pass-through branch does not, so an odd-sized image would fail to encode where it
    previously succeeded. Padding is used rather than scaling because a pad adds pixels instead of resampling
    them — a rescale is what erases the Article 50(2) mark, and this branch exists to preserve it.
    """
    from PIL import Image

    try:
        with Image.open(image_path) as im:
            width, height = im.size
    except Exception as exc:      # noqa: BLE001 — unreadable: no pad filter, ffmpeg reports the real problem
        # Logged for the same reason as its neighbour above: a silent catch here discards the only signal
        # explaining why an odd-sized image reached libx264 unpadded. A reviewer found the two functions
        # disagreeing about how to report a failed read.
        logger.warning("tiktok: could not read %s to check for odd dimensions (%s: %s) — encoding unpadded",
                       image_path, type(exc).__name__, exc)
        return ""
    if not width or not height or (width % 2 == 0 and height % 2 == 0):
        return ""
    return (f"pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0:color=black")
