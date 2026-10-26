"""Shared FastAPI dependencies."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth import get_session
from app.database import get_db  # noqa: F401  (re-exported for endpoint use)
from app.models import Operator

__all__ = ["get_db", "get_current_operator", "require_admin", "require_publish"]


def get_current_operator(request: Request, db: Session = Depends(get_db)) -> Operator:
    """Resolve the logged-in operator from the session cookie, or 401."""
    token = request.cookies.get("sp_session")
    sess = get_session(db, token)
    if sess is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    operator = db.get(Operator, sess.operator_id)
    if operator is None or not operator.is_active:
        raise HTTPException(status_code=401, detail="Account disabled")
    return operator


def require_admin(operator: Operator = Depends(get_current_operator)) -> Operator:
    """Same as get_current_operator but enforces the admin role."""
    if operator.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return operator


def require_publish(operator: Operator = Depends(get_current_operator)) -> Operator:
    """Enforce the publish capability (admins have it implicitly; operators only
    when an admin has granted `can_publish`)."""
    if operator.role != "admin" and not operator.can_publish:
        raise HTTPException(status_code=403, detail="Publish permission required")
    return operator
