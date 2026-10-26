"""Whitelabel theme endpoints (list/read for all, activate for admin)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_current_operator, get_db, require_admin
from app.models import Operator, Theme
from app.schemas import ThemeOut

router = APIRouter(prefix="/theme", tags=["theme"])


def _active_theme(db: Session) -> Theme | None:
    theme = db.scalar(select(Theme).where(Theme.is_active.is_(True)))
    if theme is None:
        theme = db.scalar(select(Theme).order_by(Theme.id).limit(1))
    return theme


@router.get("", response_model=ThemeOut)
def get_theme(db: Session = Depends(get_db), _: Operator = Depends(get_current_operator)):
    theme = _active_theme(db)
    if theme is None:
        raise HTTPException(status_code=404, detail="No theme configured")
    return theme


@router.get("/all", response_model=list[ThemeOut])
def list_themes(db: Session = Depends(get_db), _: Operator = Depends(get_current_operator)):
    return db.scalars(select(Theme).order_by(Theme.id)).all()


@router.post("/{theme_id}/activate", response_model=ThemeOut)
def activate_theme(
    theme_id: str,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    theme = db.get(Theme, theme_id)
    if theme is None:
        raise HTTPException(status_code=404, detail="Theme not found")
    for other in db.scalars(select(Theme).where(Theme.is_active.is_(True))).all():
        other.is_active = False
    theme.is_active = True
    db.commit()
    db.refresh(theme)
    return theme
