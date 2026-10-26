"""Media ingestion: validation + transcoding to standard formats (ffmpeg/Pillow)."""
import json
import subprocess
import uuid
from pathlib import Path

from app.config import PROJECT_ROOT

MEDIA_DIR = PROJECT_ROOT / "media"

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "heic", "heif"}
VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "avi", "m4v", "ts"}
AUDIO_EXTS = {"mp3", "m4a", "wav", "aac", "flac", "ogg", "opus"}
DOC_EXTS = {"pdf", "doc", "docx", "txt", "md", "csv"}

MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
    "gif": "image/gif", "bmp": "image/bmp", "tiff": "image/tiff",
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "mkv": "video/x-matroska",
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "wav": "audio/wav", "aac": "audio/aac",
    "flac": "audio/flac", "ogg": "audio/ogg", "opus": "audio/opus",
    "pdf": "application/pdf",
}

LOGO_SIZE = 256  # brand logo icons are standardized to this square canvas

# Per-platform media limits (max bytes + max image dimension). Applied at publish
# time so generated/uploaded media never exceeds a platform's cap. Sources shift —
# re-verify against each platform's current docs before adding a new adapter.
PLATFORM_MEDIA_LIMITS = {
    "bluesky": {"max_bytes": 1_000_000, "max_dim": 1080},
    "instagram": {"max_bytes": 8_000_000, "max_dim": 1440},
    "facebook": {"max_bytes": 8_000_000, "max_dim": 2048},
    "linkedin": {"max_bytes": 5_000_000, "max_dim": 4320},
    "x": {"max_bytes": 5_000_000, "max_dim": 4096},
    "pinterest": {"max_bytes": 10_000_000, "max_dim": 2000},
    "google": {"max_bytes": 5_000_000, "max_dim": 2048},
    "threads": {"max_bytes": 8_000_000, "max_dim": 1440},
    "tiktok": {"max_bytes": 20_000_000, "max_dim": 4096},
    "youtube": {"max_bytes": 2_000_000, "max_dim": 1280},
}


def _within_dim(path: Path, max_dim: int) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return max(im.size) <= max_dim
    except Exception:
        return True  # can't probe — assume within


def video_frame(data: bytes) -> bytes:
    """The first frame of an MP4, as PNG bytes. Raises if ffmpeg cannot read the video.

    Exists so a platform path that re-encodes *after* generation can ask the question the image paths ask:
    is the Article 50(2) mark in the bytes about to be uploaded? A second review pointed out that the tiktok
    path (image → MP4 through ffmpeg) uploaded without ever checking, and that the *test* which measured the
    mark surviving was not the same thing as the code enforcing it.
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        mp4 = Path(td) / "in.mp4"
        png = Path(td) / "frame.png"
        mp4.write_bytes(data)
        subprocess.run(["ffmpeg", "-y", "-i", str(mp4), "-frames:v", "1", str(png)],
                       check=True, capture_output=True, timeout=120)
        return png.read_bytes()


class MarkingLost(RuntimeError):
    """An AI-generated asset could not be published with its mark intact (AI Act Article 50(2))."""


def _encode_within(im, max_bytes: int, max_dim: int | None) -> tuple[bytes, str]:
    """JPEG bytes for this image, under `max_bytes` (progressive quality reduction)."""
    import io

    from PIL import Image  # noqa: F401 — imported for its Resampling enum, used just below

    if max_dim and max(im.size) > max_dim:
        im = im.copy()
        im.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    for q in (90, 82, 74, 66, 58, 50, 42, 34, 30):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= max_bytes:
            return buf.getvalue(), "image/jpeg"
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=25, optimize=True)
    return buf.getvalue(), "image/jpeg"


def fit_for_platform(path: Path, platform: str, mime: str | None = None, *,
                     ai_generated: bool = False) -> tuple[bytes, str]:
    """Return (bytes, mime) re-encoded to respect a platform's size/dimension cap.

    Images over the cap are re-encoded to JPEG (progressive quality reduction, scaled down to max_dim if
    needed). Non-images and unknown platforms are returned unchanged (their transcoding already happened at
    ingest).

    `ai_generated=True` means the asset carries the Article 50(2) mark, and this function is the one place that
    can destroy it: a resize and a re-encode is exactly what the measurements in app/ai_marking.py show erases
    the mark, and the mark is applied at generation time. So for those assets the *fitted* bytes are marked
    again, and if the mark cannot be read back out of what would be uploaded, this raises `MarkingLost` rather
    than publishing an unmarked AI image while the database says it is marked. A second review found that gap:
    the row said `ai_marking='watermark'`, the platform re-encoded for its cap, and the published file carried
    no mark at all. Failing the upload is the honest outcome — and the caller decides whether the post loses its
    image or the whole publication fails.
    """
    import io

    limits = PLATFORM_MEDIA_LIMITS.get(platform)
    data = path.read_bytes()
    mime = mime or "image/png"
    if limits is None:
        return data, mime

    max_bytes = limits["max_bytes"]
    max_dim = limits.get("max_dim")
    is_image = (mime or "").startswith("image/")
    if len(data) <= max_bytes and (not is_image or max_dim is None or _within_dim(path, max_dim)):
        return data, mime          # already publishable as it stands, mark and all
    if not is_image:
        return data, mime          # non-image over cap: pass through (ingest already transcoded)

    try:
        from PIL import Image

        im = Image.open(path).convert("RGB")
    except Exception:
        return data, mime          # can't decode — pass through

    if not ai_generated:
        return _encode_within(im, max_bytes, max_dim)

    # An AI-generated asset: fit, re-mark the fitted bytes, and insist the mark reads back under the cap.
    # The loop shrinks the *dimensions*, not the encode quality: blind-watermark writes its own JPEG at its own
    # quality, so lowering the input quality would not lower the marked output — the marked size follows the
    # pixel count.
    from app.ai_marking import PAYLOAD, detect_bytes, mark_bytes

    base_dim = max_dim or max(im.size)
    last = ""
    for factor in (1.0, 0.75, 0.5):
        dim = max(int(base_dim * factor), 256)
        fitted, _ = _encode_within(im, max_bytes, dim)
        try:
            marked = mark_bytes(fitted, suffix=".jpg")
        except Exception as exc:      # noqa: BLE001 — a library refusal is a fact, not a crash
            last = f"the mark could not be embedded ({exc})"
            continue
        if detect_bytes(marked) != PAYLOAD:
            last = f"the mark did not read back out of the fitted image ({len(marked)} bytes)"
            continue
        if len(marked) <= max_bytes:
            return marked, "image/jpeg"
        last = (f"the marked image is {len(marked)} bytes at {dim}px, over the {max_bytes} byte cap")
    raise MarkingLost(
        f"refusing to publish this AI-generated asset to {platform}: {last}. The mark has to be in the bytes "
        f"that leave, so publishing an unmarked version would make the declaration false.")


def detect_kind(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in DOC_EXTS:
        return "doc"
    return "other"


def _ffprobe(path: Path) -> dict:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return {}
        d = json.loads(out.stdout)
        meta = {"duration_s": round(float(d.get("format", {}).get("duration", 0) or 0), 2)}
        for s in d.get("streams", []):
            if s.get("codec_type") == "video" and s.get("width"):
                meta["width"] = s["width"]
                meta["height"] = s["height"]
                break
        return meta
    except Exception:
        return {}


def _image_dims(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.width, im.height
    except Exception:
        return None, None


def _transcode(src: Path, dst: Path, kind: str) -> bool:
    try:
        if kind == "video":
            cmd = ["ffmpeg", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
                   "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", str(dst)]
        else:
            cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-c:a", "libmp3lame", "-q:a", "2", str(dst)]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return out.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    except Exception:
        return False


def ingest(file_bytes: bytes, original_name: str) -> dict:
    """Store + normalize an uploaded file. Returns metadata (no DB row).

    `stored_path` is relative to the project root (served behind auth).
    Video -> mp4 (H.264), audio -> mp3, image -> kept as-is (dimensions probed).

    Rejects unsupported extensions and non-image bytes (magic-byte check via PIL).
    """
    kind = detect_kind(original_name)
    ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else ""
    allowed = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | DOC_EXTS
    if ext not in allowed:
        raise ValueError(f"Type de fichier non supporté (.{ext})")

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex
    orig = MEDIA_DIR / f"{uid}_src.{ext}"
    orig.write_bytes(file_bytes)

    meta = {
        "kind": kind, "mime": MIME.get(ext), "width": None, "height": None,
        "duration_s": None, "size_bytes": len(file_bytes),
    }
    stored = orig

    if kind == "image":
        meta["width"], meta["height"] = _image_dims(orig)
        if meta["width"] is None:  # PIL couldn't decode it → not a real image
            orig.unlink(missing_ok=True)
            raise ValueError("Fichier image invalide")
    elif kind in ("video", "audio"):
        meta.update(_ffprobe(orig))
        target_ext = "mp4" if kind == "video" else "mp3"
        target = MEDIA_DIR / f"{uid}.{target_ext}"
        if _transcode(orig, target, kind):
            stored = target
            meta["mime"] = MIME[target_ext]
            orig.unlink(missing_ok=True)  # drop source after successful transcode
            if kind == "video":
                thumb = _video_thumb(target, uid)
                if thumb:
                    meta["thumb_path"] = str(thumb.relative_to(PROJECT_ROOT))

    meta["stored_path"] = str(stored.relative_to(PROJECT_ROOT))
    return meta


def _video_thumb(src: Path, uid: str) -> Path | None:
    """First-frame thumbnail for a transcoded video."""
    thumb = MEDIA_DIR / f"{uid}_thumb.jpg"
    try:
        cmd = ["ffmpeg", "-y", "-i", str(src), "-ss", "0", "-frames:v", "1",
               "-vf", "scale=320:-1", str(thumb)]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and thumb.exists() and thumb.stat().st_size > 0:
            return thumb
    except Exception:
        pass
    return None


def standardize_logo(file_bytes: bytes) -> str:
    """Fit an uploaded logo into a transparent square icon (contain — never crop).

    Any raster format PIL can decode is accepted; the result is a centered
    LOGO_SIZE x LOGO_SIZE PNG with the full logo visible and transparent padding
    around it (aspect ratio preserved, shape untouched). Returns `stored_path`
    relative to PROJECT_ROOT.
    """
    import io

    from PIL import Image

    try:
        im = Image.open(io.BytesIO(file_bytes))
        im.load()
    except Exception as e:  # noqa: BLE001
        raise ValueError("Fichier image invalide") from e

    im = im.convert("RGBA")
    im.thumbnail((LOGO_SIZE, LOGO_SIZE), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0, 0))
    canvas.paste(im, ((LOGO_SIZE - im.width) // 2, (LOGO_SIZE - im.height) // 2), im)

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex
    dst = MEDIA_DIR / f"logo_{uid}.png"
    canvas.save(dst, "PNG")
    return str(dst.relative_to(PROJECT_ROOT))
