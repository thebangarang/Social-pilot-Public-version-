"""Mark AI-generated images so the mark survives being published (AI Act Article 50(2)).

Why a pixel mark and not a label in the file's metadata: our provider already embeds C2PA Content
Credentials, and metadata does not survive anything — a re-save with untouched pixels destroys it, and every
platform strips it on upload. Article 50(2) asks for a mark "in a machine-readable format" that is
"detectable as artificially generated", so it has to live in the pixels.

Measured on this machine (2026-10-05, same library and sizes this app uses):

    byte copy of a marked PNG .................. the mark is detected
    JPEG re-encode at quality 82 (every upload)  the mark is detected
    resize to 640x640 then JPEG ................. NOT detected, and the unmarked control is correctly
                                                 reported as unmarked

So the mark belongs in the pixels of the asset **at publish size**. That is where this pipeline applies it:
`generate_media` asks the provider for the platform's own size (`_media_format_sizes`), so the bytes that come
back are already the bytes that will be published — no local resize happens afterwards, which is the one
operation that would erase the mark.

Discipline borrowed from the reference implementation (projects/aiturn/scripts/ai_marking.py), which measured
the same things: a mark only counts when the decoded payload equals one we issued (the extractor returns
plausible noise rather than an error when the shape is wrong), and nothing is written to the media directory
until the mark has been read back out of the bytes we are about to store. If the mark cannot be verified the
asset is stored unmarked and `ai_marking` stays `"none"` — the publish gate on the production ring then
refuses it, which is the intended outcome: better no mark and no post than a post that claims one.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("social_pilot.ai_marking")

# OpenCV reads this at load time, before any setLogLevel call can take effect — the "Unsupported depth image"
# warning comes from its writer and is printed to stderr regardless of the Python logging configuration.
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")

def password() -> int:
    """The watermark key, from the deployment's settings (`SP_AI_MARKING_KEY`). No fallback in source.

    It was a literal here, shared with the reference implementation, and two reviews said the same thing about
    it: key material committed to a repository, unscoped per installation, unrotatable by construction. The
    first fix kept the literal as a fallback, which is the same key still published — so there is none now: an
    installation that has not set the value cannot mark, its assets are stored unmarked, and the production-ring
    publish gate refuses them. That is the intended failure (visible, and on the safe side).

    The *value* must never change once content is published: a mark written with one key cannot be read with
    another, so `.env` holds it and it stays.
    """
    from app.config import get_settings

    raw = (get_settings().ai_marking_key or "").strip()
    if not raw:
        raise RuntimeError(
            "SP_AI_MARKING_KEY is not set: this deployment cannot mark AI-generated content, so the publication "
            "gate will refuse to publish any of it. Set it once (a number) in the instance's .env.")
    try:
        return int(raw)
    except ValueError:
        # Deliberately silent about what was configured: the first version let the raw string into
        # `int()`'s message, and `mark_generated` logs the exception verbatim — a key written into a log
        # is a key written where keys must not go. Name the variable, never the value.
        raise RuntimeError(
            "SP_AI_MARKING_KEY is not a number: it must be the numeric key this installation marks with. "
            "The configured value is deliberately not echoed here.") from None

#: The payload the detector looks for. Short on purpose — the library packs the utf-8 bytes into one integer
#: and drops leading zeros, so the bit-length is the extractor's shape: a short, fixed payload needs fewer
#: bits and survives more re-encoding than an id-bearing string would.
PAYLOAD = "AI-generated aiturn.io"

REGISTRY = "data/ai-marking-registry.jsonl"


def wm_shape_for(payload: str) -> tuple[int, int]:
    """The bit-shape of a payload, exactly as the library computes it (see the module docstring)."""
    bits = bin(int(payload.encode("utf-8").hex(), base=16))[2:]
    return (1, len(bits))


def _watermark():
    """A WaterMark instance. Imported here so a machine without the stack can still import the app."""
    _quiet()
    from blind_watermark import WaterMark

    return WaterMark(password_wm=password(), password_img=password())


def _quiet():
    """Silence the library's own chatter — at the source, not by swapping the process's streams.

    The first version wrapped the embed and extract calls in `contextlib.redirect_stdout/redirect_stderr`.
    Those are **process-global**, and this code runs in FastAPI's threadpool (`/posts/{id}/generate-media` is a
    sync route) for 1-7 seconds at a time: a second review pointed out that while one thread marks an image,
    every other thread's output — log lines, warnings, another library's banner — was written into a throwaway
    StringIO. The fix is to stop the two sources directly: blind-watermark's import banner (`bw_notes.close()`)
    and OpenCV's writer warning (`cv2.setLogLevel`, a global setting that is idempotent and not per-call).

    Cost, measured on this machine: 1.1 s at 512x512, 4.0 s at 896x1120, 7.2 s at 1792x1024. That cost is
    accepted; the noise was not.
    """
    # Both calls are best-effort — a missing entry point means noisy logs, not a broken feature — but they
    # must not fail *silently*: the first version wrapped them in `contextlib.suppress(Exception)`, so a library
    # upgrade that renamed either one would have brought the noise back with nothing to read (a reviewer's
    # point: a suppression nothing can observe is the same shape as a check that cannot fail).
    try:
        from blind_watermark import bw_notes

        bw_notes.close()
    except Exception as exc:      # noqa: BLE001 — recorded, not swallowed
        logger.warning("could not silence blind-watermark's banner (%s: %s) — logs will be noisy",
                       type(exc).__name__, exc)
    try:
        import cv2

        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
    except Exception as exc:      # noqa: BLE001 — recorded, not swallowed
        logger.warning("could not silence OpenCV's writer warnings (%s: %s)", type(exc).__name__, exc)


def mark_bytes(data: bytes, payload: str = PAYLOAD, suffix: str = ".png") -> bytes:
    """The same image with `payload` embedded in its pixels. Raises on anything the library refuses.

    `suffix` decides the container the marked image comes back in. It matters for one caller: the platform fit
    re-encodes to JPEG to respect a size cap, so the mark has to be re-applied to a JPEG and come back as one —
    and the measurement in the module docstring is that a JPEG re-encode *keeps* the mark, so this is the same
    operation the platforms perform, not a new risk.
    """
    if suffix not in (".png", ".jpg", ".jpeg"):
        raise ValueError(f"unsupported output container {suffix!r}")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.png"
        dst = Path(tmp) / f"out{suffix}"
        src.write_bytes(data)
        bwm = _watermark()
        bwm.read_img(str(src))
        bwm.read_wm(payload, mode="str")
        bwm.embed(str(dst))
        return dst.read_bytes()


def detect_bytes(data: bytes, candidates: list[str] | None = None) -> str | None:
    """The payload that decodes cleanly out of these bytes, or None.

    Exact equality, never a similarity score: a wrong shape decodes to noise, and noise that happens to look
    like a phrase must not count as a mark.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "probe.png"
        src.write_bytes(data)
        for payload in (candidates or [PAYLOAD]):
            try:
                out = _watermark().extract(str(src), wm_shape=wm_shape_for(payload), mode="str")
            except Exception:      # noqa: BLE001 — the library raises several types for "no mark here"
                continue
            if isinstance(out, bytes):
                out = out.decode("utf-8", "ignore")
            if isinstance(out, str) and out.strip("\x00") == payload:
                return payload
    return None


def mark_generated(data: bytes, media_id: str) -> tuple[bytes, bool]:
    """Bytes to store for an asset this app generated, and whether the mark is in them.

    Never raises: generation must not fail because marking did. The caller stores the returned bytes and sets
    `ai_marking` from the second value, so an unmarked asset is stored honestly as unmarked — and refused at
    publish time on the production ring, where the exposure is a client's account.
    """
    try:
        marked = mark_bytes(data)
    except Exception as exc:      # noqa: BLE001 — a refusal to mark is a fact to record, not a crash
        logger.warning("could not mark generated media %s: %s", media_id[:8], exc)
        return data, False
    if detect_bytes(marked) != PAYLOAD:
        logger.warning("the mark did not read back out of generated media %s — storing it unmarked",
                       media_id[:8])
        return data, False
    record(media_id, data, marked)
    return marked, True


def record(media_id: str, original: bytes, marked: bytes) -> None:
    """One line of provenance per marked asset: hashes only, no prompt, no client text.

    The registry is what ties a published file back to the fact that we generated it (the payload itself is
    deliberately short and carries no identifying data) — the same shape the reference implementation uses.
    """
    entry = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "media_id": media_id,
        "payload": PAYLOAD,
        "sha256_original": hashlib.sha256(original).hexdigest(),
        "sha256_marked": hashlib.sha256(marked).hexdigest(),
        "issuer": "social-pilot",
    }
    try:
        path = Path(REGISTRY)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:        # the mark is already in the pixels; a registry line is bookkeeping
        logger.warning("could not append the AI-content registry line for %s: %s", media_id[:8], exc)
