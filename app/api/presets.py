"""Art-direction style presets (built-in + user portfolio)."""
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.deps import get_current_operator, get_db
from app.models import Operator, StylePreset
from app.schemas import StylePresetIn, StylePresetOut

router = APIRouter(prefix="/presets", tags=["presets"])

BUILTIN_PRESETS = [
    StylePreset(
        id="minimal-clean", name="Minimal & clean", is_builtin=True,
        directives="Esthétique minimaliste et épurée. Beaucoup d'espace blanc, une idée par visuel, typographie sobre, palette restreinte (2-3 couleurs), tons neutres.",
    ),
    StylePreset(
        id="bold-playful", name="Bold & playful", is_builtin=True,
        directives="Ton énergique et fun. Couleurs vives et contrastées, emojis assumés, typographie audacieuse, compositions dynamiques, humour léger.",
    ),
    StylePreset(
        id="premium-dark", name="Premium dark", is_builtin=True,
        directives="Esthétique premium sombre. Fonds noirs profonds, accents dorés ou métalliques, typographie élégante, rendu luxe et exclusif, éclairage dramatique.",
    ),
]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "preset"


def _seed(db: Session) -> None:
    if db.scalar(select(StylePreset).limit(1)) is None:
        db.add_all(BUILTIN_PRESETS)
        db.commit()


@router.get("", response_model=list[StylePresetOut])
def list_presets(
    brand_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    _: Operator = Depends(get_current_operator),
):
    _seed(db)
    q = select(StylePreset)
    if brand_id:
        q = q.where(or_(StylePreset.is_builtin.is_(True), StylePreset.brand_id == brand_id))
    else:
        q = q.where(or_(StylePreset.is_builtin.is_(True), StylePreset.brand_id.is_(None)))
    return db.scalars(q.order_by(StylePreset.is_builtin.desc(), StylePreset.name)).all()


@router.post("", response_model=StylePresetOut, status_code=status.HTTP_201_CREATED)
def create_preset(
    payload: StylePresetIn,
    db: Session = Depends(get_db),
    _: Operator = Depends(get_current_operator),
):
    preset = StylePreset(
        id=f"{_slugify(payload.name)}-{uuid.uuid4().hex[:4]}",
        name=payload.name.strip(),
        directives=payload.directives.strip(),
        is_builtin=False,
        brand_id=payload.brand_id,
    )
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return preset


@router.delete("/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preset(
    preset_id: str,
    db: Session = Depends(get_db),
    _: Operator = Depends(get_current_operator),
):
    preset = db.get(StylePreset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset introuvable")
    if preset.is_builtin:
        raise HTTPException(status_code=409, detail="Les presets intégrés ne sont pas supprimables")
    db.delete(preset)
    db.commit()
