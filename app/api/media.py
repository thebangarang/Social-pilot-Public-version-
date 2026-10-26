"""Media upload + list + authenticated file serving."""
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.deps import get_current_operator, get_db
from app.media import ingest
from app.models import MediaItem, Operator
from app.schemas import MediaItemOut
from app.security import verify_media_signature

router = APIRouter(prefix="/media", tags=["media"])
public_router = APIRouter(prefix="/m", tags=["media-public"])

MAX_UPLOAD = 200 * 1024 * 1024  # 200 MB
_CHUNK = 1024 * 1024  # 1 MB


def _clean_filename(name: str) -> str:
    """Strip control chars + truncate (stored for display only — never a path)."""
    return re.sub(r"[\x00-\x1f]", "", name)[:200]


@router.get("", response_model=list[MediaItemOut])
def list_media(
    brand_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    q = select(MediaItem)
    if brand_id:
        q = q.where(MediaItem.brand_id == brand_id)
    else:
        q = q.where(MediaItem.brand_id.is_(None))
    return db.scalars(q.order_by(MediaItem.usage_count.desc(), MediaItem.created_at.desc())).all()


@router.post("/upload", response_model=MediaItemOut, status_code=201)
async def upload(
    file: UploadFile = File(...),
    brand_id: uuid.UUID | None = Form(None),
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Nom de fichier manquant")
    # Stream with a hard cap — reject oversized uploads before buffering them fully
    # (prevents a memory-exhaustion DoS from a multi-GB body).
    chunks = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="Fichier trop volumineux (200 Mo max)")
        chunks.append(chunk)
    data = b"".join(chunks)
    try:
        meta = ingest(data, file.filename)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Ingestion échouée : {e}")

    item = MediaItem(
        owner_id=operator.id,
        brand_id=brand_id,
        original_name=_clean_filename(file.filename),
        stored_path=meta["stored_path"],
        kind=meta["kind"],
        mime=meta["mime"],
        width=meta["width"],
        height=meta["height"],
        duration_s=meta["duration_s"],
        thumb_path=meta.get("thumb_path"),
        size_bytes=meta["size_bytes"],
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/{media_id}/file")
def media_file(
    media_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Média introuvable")
    path = PROJECT_ROOT / item.stored_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier manquant sur le disque")
    return FileResponse(path, media_type=item.mime or "application/octet-stream")


@router.delete("/{media_id}", status_code=204)
def delete_media(
    media_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """Delete a media item + its files on disk (purge the library)."""
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Média introuvable")
    for rel in (item.stored_path, item.thumb_path):
        if rel:
            (PROJECT_ROOT / rel).unlink(missing_ok=True)
    db.delete(item)
    db.commit()


@public_router.get("/{media_id}")
def public_media_file(
    media_id: uuid.UUID,
    sig: str = Query(...),
    db: Session = Depends(get_db),
):
    """Unauthenticated media serving for platform fetchers (signed capability URL).

    Meta/TikTok download media over HTTPS without our session cookie; the HMAC
    signature over the media id proves the URL was minted by this instance, so a
    guessed/leaked internal id alone yields 404.
    """
    if not verify_media_signature(str(media_id), sig):
        raise HTTPException(status_code=404, detail="Média introuvable")
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Média introuvable")
    path = PROJECT_ROOT / item.stored_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="Fichier manquant sur le disque")
    return FileResponse(path, media_type=item.mime or "application/octet-stream")


@router.get("/{media_id}/thumb")
def media_thumb(
    media_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """Thumbnail (video first-frame, or the image itself for images)."""
    item = db.get(MediaItem, media_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Média introuvable")
    path = PROJECT_ROOT / (item.thumb_path or item.stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Miniature manquante")
    return FileResponse(path, media_type="image/jpeg" if path.suffix in (".jpg", ".jpeg") else (item.mime or "application/octet-stream"))
