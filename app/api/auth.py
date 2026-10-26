"""Authentication endpoints: login (with 2FA), logout, self-service password + 2FA."""
import base64
import hashlib
import uuid

import pyotp
import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE_NAME,
    SESSION_TTL,
    authenticate,
    create_session,
    destroy_session,
    is_locked_out,
    make_challenge,
    record_attempt,
    totp_secret_plain,
    totp_uri,
    utcnow,
    verify_challenge,
    verify_totp,
)
from app.config import settings
from app.deps import get_current_operator, get_db
from app.models import Operator, UserSession
from app.schemas import (
    LoginIn,
    LoginResponse,
    MeUpdateIn,
    OperatorOut,
    PasswordChangeIn,
    TwoFAChallengeIn,
    TwoFADisableIn,
    TwoFAEnableIn,
    TwoFASetupOut,
)
from app.security import encrypt_secret, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def _qr_data_uri(data: str) -> str:
    qr = qrcode.QRCode(image_factory=qrcode.image.svg.SvgPathImage, border=1)
    qr.add_data(data)
    qr.make(fit=True)
    svg = qr.make_image().to_string()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode()


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    ip = request.client.host if request.client else "unknown"

    if is_locked_out(db, email):
        raise HTTPException(status_code=429, detail="Too many failed attempts — try again later")

    operator = authenticate(db, email, payload.password)
    if operator is None:
        record_attempt(db, email, ip, False)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    record_attempt(db, email, ip, True)
    if operator.totp_enabled:
        db.commit()
        return LoginResponse(requires_2fa=True, challenge=make_challenge(operator.id))
    token = create_session(db, operator)
    _set_cookie(response, token)
    operator.last_login_at = utcnow()
    db.commit()
    return LoginResponse(requires_2fa=False)


@router.post("/login/2fa", response_model=LoginResponse)
def login_2fa(payload: TwoFAChallengeIn, request: Request, response: Response, db: Session = Depends(get_db)):
    op_id = verify_challenge(payload.challenge)
    if op_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired 2FA challenge")
    operator = db.get(Operator, uuid.UUID(op_id))
    if operator is None or not operator.is_active:
        raise HTTPException(status_code=401, detail="Invalid or expired 2FA challenge")
    ip = request.client.host if request.client else "unknown"
    if is_locked_out(db, operator.email):
        raise HTTPException(status_code=429, detail="Too many failed attempts — try again later")
    secret = totp_secret_plain(operator)
    if not secret or not verify_totp(secret, payload.code):
        record_attempt(db, operator.email, ip, False)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid 2FA code")
    record_attempt(db, operator.email, ip, True)
    token = create_session(db, operator)
    _set_cookie(response, token)
    operator.last_login_at = utcnow()
    db.commit()
    return LoginResponse(requires_2fa=False)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    destroy_session(db, token)
    _clear_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=OperatorOut)
def me(operator: Operator = Depends(get_current_operator)):
    return operator


@router.patch("/me", response_model=OperatorOut)
def update_me(
    payload: MeUpdateIn,
    operator: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    """Self-service profile update (name / UI language)."""
    if payload.name is not None and payload.name.strip():
        operator.name = payload.name.strip()
    if payload.language is not None:
        operator.language = payload.language
    db.commit()
    db.refresh(operator)
    return operator


@router.post("/password")
def change_password(
    payload: PasswordChangeIn,
    request: Request,
    operator: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, operator.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    operator.password_hash = hash_password(payload.new_password)
    token = request.cookies.get(COOKIE_NAME)
    current_hash = hashlib.sha256(token.encode()).hexdigest() if token else None
    db.execute(
        delete(UserSession).where(
            UserSession.operator_id == operator.id,
            UserSession.token_hash != current_hash,
        )
    )
    db.commit()
    return {"ok": True}


@router.get("/2fa/setup", response_model=TwoFASetupOut)
def twofa_setup(operator: Operator = Depends(get_current_operator), db: Session = Depends(get_db)):
    if operator.totp_enabled:
        raise HTTPException(status_code=409, detail="2FA is already enabled")
    secret = totp_secret_plain(operator)
    if not secret:
        secret = pyotp.random_base32()
        operator.totp_secret = encrypt_secret(secret)
        operator.totp_enabled = False  # still pending verification
        db.commit()
    uri = totp_uri(operator.email, secret)
    return TwoFASetupOut(secret=secret, otpauth_uri=uri, qr_data_uri=_qr_data_uri(uri))


@router.post("/2fa/enable")
def twofa_enable(
    payload: TwoFAEnableIn,
    operator: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    secret = totp_secret_plain(operator)
    if not secret:
        raise HTTPException(status_code=400, detail="Run 2FA setup first")
    if not verify_totp(secret, payload.code):
        raise HTTPException(status_code=401, detail="Invalid 2FA code")
    operator.totp_enabled = True
    db.commit()
    return {"ok": True}


@router.post("/2fa/disable")
def twofa_disable(
    payload: TwoFADisableIn,
    operator: Operator = Depends(get_current_operator),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.password, operator.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password")
    operator.totp_secret = None
    operator.totp_enabled = False
    db.commit()
    return {"ok": True}
