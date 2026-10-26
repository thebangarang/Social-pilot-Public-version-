"""Branding asset kit (the user's default brand elements)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_current_operator, get_db
from app.models import BrandAsset, Operator
from app.schemas import BrandAssetIn, BrandAssetOut

router = APIRouter(prefix="/branding", tags=["branding"])


@router.get("", response_model=list[BrandAssetOut])
def list_branding(
    brand_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _: Operator = Depends(get_current_operator),
):
    q = select(BrandAsset).order_by(BrandAsset.created_at)
    if brand_id is not None:
        q = q.where(BrandAsset.brand_id == brand_id)
    return db.scalars(q).all()


@router.post("", response_model=BrandAssetOut, status_code=status.HTTP_201_CREATED)
def add_brand_asset(
    payload: BrandAssetIn,
    db: Session = Depends(get_db),
    _: Operator = Depends(get_current_operator),
):
    asset = BrandAsset(
        kind=payload.kind,
        filename=payload.filename.strip(),
        notes=payload.notes.strip(),
        media_id=payload.media_id,
        brand_id=payload.brand_id,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_brand_asset(
    asset_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Operator = Depends(get_current_operator),
):
    asset = db.get(BrandAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Élément introuvable")
    db.delete(asset)
    db.commit()
