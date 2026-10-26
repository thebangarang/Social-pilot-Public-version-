"""Brand management — a client manages multiple brands (whitelabel multi-brand)."""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.deps import get_current_operator, get_db, require_admin
from app.media import standardize_logo
from app.models import (
    Brand,
    BrandAsset,
    Comment,
    ConnectedAccount,
    MediaItem,
    MetricSnapshot,
    Operator,
    Post,
    Publication,
    StylePreset,
)
from app.schemas import BrandOut, BrandUpdate

router = APIRouter(prefix="/brands", tags=["brands"])


def _unlink_media(item) -> None:
    for rel in (item.stored_path, item.thumb_path):
        if rel:
            (PROJECT_ROOT / rel).unlink(missing_ok=True)


@router.get("", response_model=list[BrandOut])
def list_brands(db: Session = Depends(get_db), _: Operator = Depends(get_current_operator)):
    return db.scalars(select(Brand).order_by(Brand.name)).all()


@router.post("", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
async def create_brand(
    name: str = Form(...),
    logo: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    """Create a brand, optionally with a logo standardized to a fit icon."""
    brand = Brand(name=name.strip())
    if logo is not None:
        data = await logo.read()
        if data:
            try:
                brand.logo_path = standardize_logo(data)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
    db.add(brand)
    db.commit()
    db.refresh(brand)
    return brand


@router.get("/{brand_id}/logo")
def brand_logo(
    brand_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Operator = Depends(get_current_operator),
):
    """Serve a brand's standardized logo icon (authenticated, team-visible)."""
    brand = db.get(Brand, brand_id)
    if brand is None or not brand.logo_path:
        raise HTTPException(status_code=404, detail="Logo introuvable")
    path = PROJECT_ROOT / brand.logo_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="Logo introuvable")
    return FileResponse(path, media_type="image/png")


@router.patch("/{brand_id}/logo", response_model=BrandOut)
async def update_brand_logo(
    brand_id: uuid.UUID,
    logo: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    """Replace a brand's logo (re-standardizes; removes the previous icon)."""
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=404, detail="Marque introuvable")
    data = await logo.read()
    if not data:
        raise HTTPException(status_code=400, detail="Fichier vide")
    try:
        new_path = standardize_logo(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if brand.logo_path:
        (PROJECT_ROOT / brand.logo_path).unlink(missing_ok=True)
    brand.logo_path = new_path
    db.commit()
    db.refresh(brand)
    return brand


@router.patch("/{brand_id}", response_model=BrandOut)
def rename_brand(
    brand_id: uuid.UUID,
    payload: BrandUpdate,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=404, detail="Marque introuvable")
    brand.name = payload.name.strip()
    db.commit()
    db.refresh(brand)
    return brand


@router.delete("/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_brand(
    brand_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=404, detail="Marque introuvable")
    if brand.logo_path:
        (PROJECT_ROOT / brand.logo_path).unlink(missing_ok=True)
    # Unassign (not delete) everything tied to this brand. Explicit SET NULL
    # because the migrated SQLite schema lacks the FK ondelete rules the model
    # declares. Posts, publications, metrics, comments and sessions all survive
    # as "unassigned".
    for model in (ConnectedAccount, Post, MediaItem, StylePreset, BrandAsset):
        db.execute(update(model).where(model.brand_id == brand.id).values(brand_id=None))
    db.delete(brand)
    db.commit()


@router.delete("/{brand_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_brand(
    brand_id: uuid.UUID,
    confirm: str,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    """Hard-delete a brand and everything tagged with it.

    Requires the exact brand name in `confirm` (typed confirmation on the client)
    so the destructive flush cannot be triggered by accident.
    """
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(status_code=404, detail="Marque introuvable")
    if confirm.strip() != brand.name:
        raise HTTPException(
            status_code=400,
            detail="Confirmation invalide : le nom saisi ne correspond pas au nom de la marque.",
        )

    # 1. Posts tagged with this brand, and their publications/metrics/comments.
    #    Explicit deletes (the migrated schema lacks the FK cascades).
    post_ids = list(db.scalars(select(Post.id).where(Post.brand_id == brand.id)).all())
    pub_ids = (
        list(db.scalars(select(Publication.id).where(Publication.post_id.in_(post_ids))).all())
        if post_ids
        else []
    )
    if pub_ids:
        db.execute(delete(MetricSnapshot).where(MetricSnapshot.publication_id.in_(pub_ids)))
        db.execute(delete(Comment).where(Comment.publication_id.in_(pub_ids)))
        db.execute(delete(Publication).where(Publication.id.in_(pub_ids)))

    # 2. Media to remove: brand-tagged media + generated media referenced by the posts.
    media_ids: set[str] = {str(mid) for mid in db.scalars(select(MediaItem.id).where(MediaItem.brand_id == brand.id)).all()}
    if post_ids:
        for p in db.scalars(select(Post).where(Post.id.in_(post_ids))).all():
            for mid in p.media_ids or []:
                media_ids.add(str(mid))
    media_uuids: list[uuid.UUID] = []
    for mid in media_ids:
        try:
            media_uuids.append(uuid.UUID(mid))
        except (ValueError, TypeError):
            continue
    if media_uuids:
        for m in db.scalars(select(MediaItem).where(MediaItem.id.in_(media_uuids))).all():
            _unlink_media(m)
        db.execute(delete(MediaItem).where(MediaItem.id.in_(media_uuids)))

    # 3. Posts, presets and branding assets tagged with the brand.
    if post_ids:
        db.execute(delete(Post).where(Post.id.in_(post_ids)))
    db.execute(delete(StylePreset).where(StylePreset.brand_id == brand.id))
    db.execute(delete(BrandAsset).where(BrandAsset.brand_id == brand.id))

    # 4. Keep the OAuth connections (social accounts) but unassign them.
    db.execute(update(ConnectedAccount).where(ConnectedAccount.brand_id == brand.id).values(brand_id=None))

    # 5. The brand itself and its logo.
    if brand.logo_path:
        (PROJECT_ROOT / brand.logo_path).unlink(missing_ok=True)
    db.delete(brand)
    db.commit()
