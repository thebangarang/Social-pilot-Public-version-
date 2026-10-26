"""User management (admin only): create, edit, disable, delete, reset 2FA."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models import Operator
from app.schemas import OperatorCreate, OperatorOut, OperatorUpdate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


def _get_operator(db: Session, operator_id: uuid.UUID) -> Operator:
    op = db.get(Operator, operator_id)
    if op is None:
        raise HTTPException(status_code=404, detail="User not found")
    return op


def _active_admin_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Operator)
            .where(Operator.role == "admin", Operator.is_active.is_(True))
        )
        or 0
    )


def _guard_last_admin(db: Session, target: Operator):
    """Refuse to remove/demote/deactivate the last active admin (lockout guard)."""
    if target.role != "admin" or not target.is_active:
        return
    if _active_admin_count(db) <= 1:
        raise HTTPException(status_code=409, detail="Cannot remove the last admin")


@router.get("", response_model=list[OperatorOut])
def list_users(db: Session = Depends(get_db), _: Operator = Depends(require_admin)):
    return db.scalars(select(Operator).order_by(Operator.created_at)).all()


@router.post("", response_model=OperatorOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: OperatorCreate,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    email = payload.email.strip().lower()
    if db.scalar(select(Operator).where(func.lower(Operator.email) == email)):
        raise HTTPException(status_code=409, detail="Email already in use")
    operator = Operator(
        email=email,
        name=payload.name.strip(),
        role=payload.role,
        can_publish=payload.can_publish,
        password_hash=hash_password(payload.password),
    )
    db.add(operator)
    db.commit()
    db.refresh(operator)
    return operator


@router.patch("/{operator_id}", response_model=OperatorOut)
def update_user(
    operator_id: uuid.UUID,
    payload: OperatorUpdate,
    db: Session = Depends(get_db),
    admin: Operator = Depends(require_admin),
):
    target = _get_operator(db, operator_id)
    changes = payload.model_dump(exclude_unset=True)

    demoting = changes.get("role") is not None and changes["role"] != "admin"
    deactivating = changes.get("is_active") is False
    if (demoting or deactivating) and target.id == admin.id:
        raise HTTPException(status_code=409, detail="You cannot edit your own admin access")
    if demoting or deactivating:
        _guard_last_admin(db, target)

    if "password" in changes and changes["password"]:
        target.password_hash = hash_password(changes.pop("password"))
    for field, value in changes.items():
        setattr(target, field, value)
    db.commit()
    db.refresh(target)
    return target


@router.delete("/{operator_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    operator_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: Operator = Depends(require_admin),
):
    target = _get_operator(db, operator_id)
    if target.id == admin.id:
        raise HTTPException(status_code=409, detail="You cannot delete yourself")
    _guard_last_admin(db, target)
    db.delete(target)
    db.commit()


@router.post("/{operator_id}/reset-2fa")
def reset_user_2fa(
    operator_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    """Clear a user's 2FA (e.g. lost phone). They re-enroll on next login."""
    target = _get_operator(db, operator_id)
    target.totp_secret = None
    target.totp_enabled = False
    db.commit()
    return {"ok": True}
