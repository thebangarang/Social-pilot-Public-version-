"""OAuth flows for social platforms (authorize → callback → token storage)."""
import base64
import datetime
import hashlib
import secrets
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_db, require_admin
from app.models import Operator
from app.security import encrypt_secret
from app.services.accounts import upsert_account

router = APIRouter(prefix="/oauth", tags=["oauth"])

TIKTOK_AUTHORIZE = "https://www.tiktok.com/v2/auth/authorize"
TIKTOK_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_USER_INFO = "https://open.tiktokapis.com/v2/user/info/"
TIKTOK_SCOPES = "user.info.basic,video.publish,video.upload"
INSTAGRAM_AUTHORIZE = "https://www.instagram.com/oauth/authorize"
INSTAGRAM_TOKEN = "https://api.instagram.com/oauth/access_token"
INSTAGRAM_GRAPH = "https://graph.instagram.com/v26.0"
INSTAGRAM_SCOPES = "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_comments,instagram_business_manage_insights"
THREADS_AUTHORIZE = "https://www.threads.net/oauth/authorize"
THREADS_TOKEN = "https://graph.threads.net/oauth/access_token"
THREADS_GRAPH = "https://graph.threads.net/v1.0"
THREADS_SCOPES = "threads_basic,threads_content_publish,threads_manage_replies,threads_read_replies,threads_manage_insights"
FACEBOOK_AUTHORIZE = "https://www.facebook.com/v26.0/dialog/oauth"
FACEBOOK_TOKEN = "https://graph.facebook.com/v26.0/oauth/access_token"
FACEBOOK_GRAPH = "https://graph.facebook.com/v26.0"
FACEBOOK_SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,pages_manage_engagement,pages_read_user_content"
LINKEDIN_AUTHORIZE = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO = "https://api.linkedin.com/v2/userinfo"
LINKEDIN_SCOPES = "openid,profile,email,w_member_social"
PINTEREST_AUTHORIZE = "https://www.pinterest.com/oauth/"
PINTEREST_TOKEN = "https://api.pinterest.com/v5/oauth/token"
PINTEREST_API = "https://api.pinterest.com/v5"
PINTEREST_SCOPES = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"
X_AUTHORIZE = "https://x.com/i/oauth2/authorize"
X_TOKEN = "https://api.x.com/2/oauth2/token"
X_API = "https://api.x.com/2"
X_SCOPES = "tweet.read tweet.write users.read offline.access"

_STATE_COOKIE = "sp_oauth_state"
_STATE_MAX_AGE = 600


def _tiktok_redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/oauth/callback/tiktok"


def _instagram_redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/oauth/callback/instagram"


def _threads_redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/oauth/callback/threads"


def _facebook_redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/oauth/callback/facebook"


def _linkedin_redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/oauth/callback/linkedin"


def _pinterest_redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/oauth/callback/pinterest"


def _x_redirect_uri() -> str:
    return f"{settings.public_base_url.rstrip('/')}/oauth/callback/x"


_VERIFIER_COOKIE = "sp_oauth_verifier"


def _pkce_pair() -> tuple[str, str]:
    """PKCE (S256) code verifier + challenge for the X OAuth flow."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


@router.get("/tiktok/authorize")
def tiktok_authorize(_: Operator = Depends(require_admin)):
    """Start TikTok OAuth: redirect the admin to the TikTok consent screen."""
    if not settings.tiktok_client_key:
        raise HTTPException(status_code=503, detail="TikTok client_key non configuré.")
    state = secrets.token_urlsafe(16)
    url = (
        f"{TIKTOK_AUTHORIZE}/?client_key={settings.tiktok_client_key}"
        f"&scope={TIKTOK_SCOPES}&response_type=code"
        f"&redirect_uri={_tiktok_redirect_uri()}&state={state}"
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(_STATE_COOKIE, state, max_age=_STATE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@router.get("/callback/tiktok")
def tiktok_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    """Exchange the OAuth code for a token and store the account."""
    if not settings.tiktok_client_secret:
        raise HTTPException(status_code=503, detail="TikTok client_secret non configuré.")
    if not request.cookies.get(_STATE_COOKIE) or request.cookies.get(_STATE_COOKIE) != state:
        raise HTTPException(status_code=400, detail="État OAuth invalide.")

    try:
        r = httpx.post(
            TIKTOK_TOKEN,
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": _tiktok_redirect_uri(),
            },
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Échec réseau échange de code TikTok: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Échange de code TikTok échoué: {r.text[:200]}")
    data = r.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    open_id = data.get("open_id")
    expires_in = int(data.get("expires_in") or 0)
    if not access_token:
        raise HTTPException(status_code=400, detail="Pas de access_token dans la réponse TikTok.")

    handle = ""
    try:
        info = httpx.get(
            TIKTOK_USER_INFO,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "display_name,username"},
            timeout=30.0,
        )
        if info.status_code == 200:
            handle = ((info.json().get("data") or {}).get("user") or {}).get("username", "")
    except Exception:  # noqa: BLE001 — handle is best-effort
        pass

    upsert_account(
        db,
        platform="tiktok",
        handle=handle or open_id,
        account_meta={"open_id": open_id, "scope": data.get("scope", ""), "source": "oauth"},
        token_enc=encrypt_secret(access_token),
        refresh_token_enc=encrypt_secret(refresh_token) if refresh_token else None,
        token_expires_at=(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)
            if expires_in else None
        ),
        identity_key="open_id",
    )

    resp = RedirectResponse("/#settings", status_code=302)
    resp.delete_cookie(_STATE_COOKIE)
    return resp


@router.get("/instagram/authorize")
def instagram_authorize(_: Operator = Depends(require_admin)):
    """Start Instagram OAuth: redirect the admin to the Instagram consent screen."""
    if not settings.instagram_client_id:
        raise HTTPException(status_code=503, detail="Instagram client_id non configuré.")
    state = secrets.token_urlsafe(16)
    url = (
        f"{INSTAGRAM_AUTHORIZE}/?enable_fb_login=0"
        f"&client_id={settings.instagram_client_id}"
        f"&redirect_uri={_instagram_redirect_uri()}&response_type=code"
        f"&scope={INSTAGRAM_SCOPES}&state={state}"
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(_STATE_COOKIE, state, max_age=_STATE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@router.get("/callback/instagram")
def instagram_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    """Exchange the OAuth code for a token and store the account."""
    if not settings.instagram_client_secret:
        raise HTTPException(status_code=503, detail="Instagram client_secret non configuré.")
    if not request.cookies.get(_STATE_COOKIE) or request.cookies.get(_STATE_COOKIE) != state:
        raise HTTPException(status_code=400, detail="État OAuth invalide.")

    try:
        r = httpx.post(
            INSTAGRAM_TOKEN,
            data={
                "client_id": settings.instagram_client_id,
                "client_secret": settings.instagram_client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": _instagram_redirect_uri(),
                "code": code,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Échec réseau échange de code Instagram: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Échange de code Instagram échoué: {r.text[:200]}")
    data = r.json()
    access_token = data.get("access_token")
    ig_user_id = str(data.get("user_id") or "")
    if not access_token:
        raise HTTPException(status_code=400, detail="Pas de access_token dans la réponse Instagram.")

    # Exchange the short-lived token (~1h) for a long-lived one (~60d).
    try:
        ll = httpx.get(
            f"{INSTAGRAM_GRAPH}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": settings.instagram_client_secret,
                "access_token": access_token,
            },
            timeout=30.0,
        )
        if ll.status_code == 200:
            access_token = ll.json().get("access_token", access_token)
    except Exception:  # noqa: BLE001 — long-lived exchange is best-effort
        pass

    handle = ""
    try:
        info = httpx.get(
            f"{INSTAGRAM_GRAPH}/me",
            params={"fields": "username", "access_token": access_token},
            timeout=30.0,
        )
        if info.status_code == 200:
            handle = info.json().get("username", "")
    except Exception:  # noqa: BLE001 — handle is best-effort
        pass

    upsert_account(
        db,
        platform="instagram",
        handle=handle or ig_user_id,
        account_meta={"ig_user_id": ig_user_id, "source": "oauth"},
        token_enc=encrypt_secret(access_token),
        identity_key="ig_user_id",
    )

    resp = RedirectResponse("/#settings", status_code=302)
    resp.delete_cookie(_STATE_COOKIE)
    return resp


@router.get("/threads/authorize")
def threads_authorize(_: Operator = Depends(require_admin)):
    """Start Threads OAuth: redirect the admin to the Threads consent screen."""
    if not settings.threads_client_id:
        raise HTTPException(status_code=503, detail="Threads client_id non configuré.")
    state = secrets.token_urlsafe(16)
    url = (
        f"{THREADS_AUTHORIZE}/?client_id={settings.threads_client_id}"
        f"&redirect_uri={_threads_redirect_uri()}&response_type=code"
        f"&scope={THREADS_SCOPES}&state={state}"
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(_STATE_COOKIE, state, max_age=_STATE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@router.get("/callback/threads")
def threads_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    """Exchange the OAuth code for a token and store the account."""
    if not settings.threads_client_secret:
        raise HTTPException(status_code=503, detail="Threads client_secret non configuré.")
    if not request.cookies.get(_STATE_COOKIE) or request.cookies.get(_STATE_COOKIE) != state:
        raise HTTPException(status_code=400, detail="État OAuth invalide.")

    try:
        r = httpx.post(
            THREADS_TOKEN,
            data={
                "client_id": settings.threads_client_id,
                "client_secret": settings.threads_client_secret,
                "grant_type": "authorization_code",
                "redirect_uri": _threads_redirect_uri(),
                "code": code,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Échec réseau échange de code Threads: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Échange de code Threads échoué: {r.text[:200]}")
    data = r.json()
    access_token = data.get("access_token")
    threads_user_id = str(data.get("user_id") or "")
    if not access_token:
        raise HTTPException(status_code=400, detail="Pas de access_token dans la réponse Threads.")

    # Exchange the short-lived token (~1h) for a long-lived one (~60d).
    try:
        ll = httpx.get(
            f"{THREADS_GRAPH}/access_token",
            params={
                "grant_type": "th_exchange_token",
                "client_secret": settings.threads_client_secret,
                "access_token": access_token,
            },
            timeout=30.0,
        )
        if ll.status_code == 200:
            access_token = ll.json().get("access_token", access_token)
    except Exception:  # noqa: BLE001 — long-lived exchange is best-effort
        pass

    handle = ""
    try:
        info = httpx.get(
            f"{THREADS_GRAPH}/me",
            params={"fields": "username", "access_token": access_token},
            timeout=30.0,
        )
        if info.status_code == 200:
            handle = info.json().get("username", "")
    except Exception:  # noqa: BLE001 — handle is best-effort
        pass

    upsert_account(
        db,
        platform="threads",
        handle=handle or threads_user_id,
        account_meta={"threads_user_id": threads_user_id, "source": "oauth"},
        token_enc=encrypt_secret(access_token),
        identity_key="threads_user_id",
    )

    resp = RedirectResponse("/#settings", status_code=302)
    resp.delete_cookie(_STATE_COOKIE)
    return resp


@router.get("/facebook/authorize")
def facebook_authorize(_: Operator = Depends(require_admin)):
    """Start Facebook Login OAuth: redirect the admin to the Facebook consent screen."""
    if not settings.facebook_client_id:
        raise HTTPException(status_code=503, detail="Facebook client_id non configuré.")
    state = secrets.token_urlsafe(16)
    url = (
        f"{FACEBOOK_AUTHORIZE}/?client_id={settings.facebook_client_id}"
        f"&redirect_uri={_facebook_redirect_uri()}&response_type=code"
        f"&scope={FACEBOOK_SCOPES}&state={state}"
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(_STATE_COOKIE, state, max_age=_STATE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@router.get("/callback/facebook")
def facebook_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    """Exchange the OAuth code, resolve the Page access token, and store the account."""
    if not settings.facebook_client_secret:
        raise HTTPException(status_code=503, detail="Facebook client_secret non configuré.")
    if not request.cookies.get(_STATE_COOKIE) or request.cookies.get(_STATE_COOKIE) != state:
        raise HTTPException(status_code=400, detail="État OAuth invalide.")

    # 1. Exchange the code for a short-lived user access token.
    try:
        r = httpx.post(
            FACEBOOK_TOKEN,
            data={
                "client_id": settings.facebook_client_id,
                "client_secret": settings.facebook_client_secret,
                "redirect_uri": _facebook_redirect_uri(),
                "code": code,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Échec réseau échange de code Facebook: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Échange de code Facebook échoué: {r.text[:200]}")
    data = r.json()
    user_token = data.get("access_token")
    if not user_token:
        raise HTTPException(status_code=400, detail="Pas de access_token dans la réponse Facebook.")

    # 2. Exchange for a long-lived user token (~60d).
    try:
        ll = httpx.get(
            f"{FACEBOOK_GRAPH}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.facebook_client_id,
                "client_secret": settings.facebook_client_secret,
                "fb_exchange_token": user_token,
            },
            timeout=30.0,
        )
        if ll.status_code == 200:
            user_token = ll.json().get("access_token", user_token)
    except Exception:  # noqa: BLE001 — long-lived exchange is best-effort
        pass

    # 3. Resolve the Page(s) this user manages; publish targets the first one.
    pages = []
    pages_raw = ""
    try:
        pr = httpx.get(
            f"{FACEBOOK_GRAPH}/me/accounts",
            params={"fields": "id,name,access_token", "access_token": user_token},
            timeout=30.0,
        )
        if pr.status_code == 200:
            pages = pr.json().get("data") or []
        else:
            pages_raw = f"{pr.status_code}: {pr.text[:200]}"
    except Exception as e:  # noqa: BLE001
        pages_raw = f"error: {e}"

    if not pages:
        raise HTTPException(
            status_code=400,
            detail=f"Aucune page Facebook gérée par ce compte. (me/accounts: {pages_raw or 'réponse vide'})",
        )

    page = pages[0]
    page_id = page.get("id", "")
    page_name = page.get("name", "")
    page_token = page.get("access_token", "")
    if not page_id or not page_token:
        raise HTTPException(status_code=400, detail="Page Facebook sans id/access_token.")

    user_id = ""
    try:
        me = httpx.get(
            f"{FACEBOOK_GRAPH}/me",
            params={"fields": "id", "access_token": user_token},
            timeout=30.0,
        )
        if me.status_code == 200:
            user_id = str(me.json().get("id", ""))
    except Exception:  # noqa: BLE001 — user_id is best-effort
        pass

    upsert_account(
        db,
        platform="facebook",
        handle=page_name or page_id,
        account_meta={"page_id": page_id, "page_name": page_name, "user_id": user_id, "source": "oauth"},
        token_enc=encrypt_secret(page_token),
        refresh_token_enc=encrypt_secret(user_token),
        identity_key="page_id",
    )

    resp = RedirectResponse("/#settings", status_code=302)
    resp.delete_cookie(_STATE_COOKIE)
    return resp


@router.get("/linkedin/authorize")
def linkedin_authorize(_: Operator = Depends(require_admin)):
    """Start LinkedIn OAuth: redirect the admin to the LinkedIn consent screen."""
    if not settings.linkedin_client_id:
        raise HTTPException(status_code=503, detail="LinkedIn client_id non configuré.")
    state = secrets.token_urlsafe(16)
    url = (
        f"{LINKEDIN_AUTHORIZE}?response_type=code"
        f"&client_id={settings.linkedin_client_id}"
        f"&redirect_uri={_linkedin_redirect_uri()}"
        f"&scope={LINKEDIN_SCOPES}&state={state}"
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(_STATE_COOKIE, state, max_age=_STATE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@router.get("/callback/linkedin")
def linkedin_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    """Exchange the OAuth code, resolve the member URN, and store the account."""
    if not settings.linkedin_client_secret:
        raise HTTPException(status_code=503, detail="LinkedIn client_secret non configuré.")
    if not request.cookies.get(_STATE_COOKIE) or request.cookies.get(_STATE_COOKIE) != state:
        raise HTTPException(status_code=400, detail="État OAuth invalide.")

    # 1. Exchange the code for an access token.
    try:
        r = httpx.post(
            LINKEDIN_TOKEN,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.linkedin_client_id,
                "client_secret": settings.linkedin_client_secret,
                "redirect_uri": _linkedin_redirect_uri(),
            },
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Échec réseau échange de code LinkedIn: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Échange de code LinkedIn échoué: {r.text[:200]}")
    data = r.json()
    access_token = data.get("access_token")
    expires_in = int(data.get("expires_in") or 0)
    if not access_token:
        raise HTTPException(status_code=400, detail="Pas de access_token dans la réponse LinkedIn.")

    # 2. Resolve the member id (sub) + name via OpenID userinfo.
    sub = ""
    name = ""
    email = ""
    try:
        info = httpx.get(
            LINKEDIN_USERINFO,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30.0,
        )
        if info.status_code == 200:
            u = info.json()
            sub = str(u.get("sub") or "")
            name = u.get("name") or ""
            email = u.get("email") or ""
    except Exception:  # noqa: BLE001 — userinfo is best-effort
        pass

    if not sub:
        raise HTTPException(status_code=400, detail="Impossible de résoudre l'identifiant LinkedIn (sub).")

    author_urn = f"urn:li:person:{sub}"

    upsert_account(
        db,
        platform="linkedin",
        handle=name or email or sub,
        account_meta={"sub": sub, "author_urn": author_urn, "name": name, "email": email, "source": "oauth"},
        token_enc=encrypt_secret(access_token),
        token_expires_at=(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)
            if expires_in else None
        ),
        identity_key="sub",
    )

    resp = RedirectResponse("/#settings", status_code=302)
    resp.delete_cookie(_STATE_COOKIE)
    return resp


@router.get("/pinterest/authorize")
def pinterest_authorize(_: Operator = Depends(require_admin)):
    """Start Pinterest OAuth: redirect the admin to the Pinterest consent screen."""
    if not settings.pinterest_client_id:
        raise HTTPException(status_code=503, detail="Pinterest client_id non configuré.")
    state = secrets.token_urlsafe(16)
    url = (
        f"{PINTEREST_AUTHORIZE}?client_id={settings.pinterest_client_id}"
        f"&redirect_uri={_pinterest_redirect_uri()}&response_type=code"
        f"&scope={PINTEREST_SCOPES}&state={state}"
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(_STATE_COOKIE, state, max_age=_STATE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@router.get("/callback/pinterest")
def pinterest_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    """Exchange the OAuth code, resolve the username + first board, and store the account."""
    if not settings.pinterest_client_secret:
        raise HTTPException(status_code=503, detail="Pinterest client_secret non configuré.")
    if not request.cookies.get(_STATE_COOKIE) or request.cookies.get(_STATE_COOKIE) != state:
        raise HTTPException(status_code=400, detail="État OAuth invalide.")

    # 1. Exchange the code (HTTP Basic auth with app_id:app_secret).
    try:
        r = httpx.post(
            PINTEREST_TOKEN,
            auth=(settings.pinterest_client_id, settings.pinterest_client_secret),
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": _pinterest_redirect_uri()},
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Échec réseau échange de code Pinterest: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Échange de code Pinterest échoué: {r.text[:200]}")
    data = r.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Pas de access_token dans la réponse Pinterest.")

    # 2. Resolve the username + first board (the Pin's destination).
    username = ""
    board_id = ""
    board_name = ""
    try:
        u = httpx.get(f"{PINTEREST_API}/user_account", headers={"Authorization": f"Bearer {access_token}"}, timeout=30.0)
        if u.status_code == 200:
            username = u.json().get("username", "")
    except Exception:  # noqa: BLE001
        pass
    try:
        b = httpx.get(f"{PINTEREST_API}/boards", headers={"Authorization": f"Bearer {access_token}"}, timeout=30.0)
        if b.status_code == 200:
            items = b.json().get("items") or []
            if items:
                board_id = items[0].get("id", "")
                board_name = items[0].get("name", "")
    except Exception:  # noqa: BLE001
        pass

    if not username:
        raise HTTPException(status_code=400, detail="Impossible de résoudre le compte Pinterest (username).")

    upsert_account(
        db,
        platform="pinterest",
        handle=username,
        account_meta={"username": username, "board_id": board_id, "board_name": board_name, "source": "oauth"},
        token_enc=encrypt_secret(access_token),
        refresh_token_enc=encrypt_secret(refresh_token) if refresh_token else None,
        identity_key="username",
    )

    resp = RedirectResponse("/#settings", status_code=302)
    resp.delete_cookie(_STATE_COOKIE)
    return resp


@router.get("/x/authorize")
def x_authorize(_: Operator = Depends(require_admin)):
    """Start X OAuth (Authorization Code + PKCE): redirect to the X consent screen."""
    if not settings.x_client_id:
        raise HTTPException(status_code=503, detail="X client_id non configuré.")
    state = secrets.token_urlsafe(16)
    verifier, challenge = _pkce_pair()
    url = (
        f"{X_AUTHORIZE}?response_type=code&client_id={settings.x_client_id}"
        f"&redirect_uri={_x_redirect_uri()}&scope={quote(X_SCOPES)}"
        f"&state={state}&code_challenge={challenge}&code_challenge_method=S256"
    )
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(_STATE_COOKIE, state, max_age=_STATE_MAX_AGE, httponly=True, samesite="lax")
    resp.set_cookie(_VERIFIER_COOKIE, verifier, max_age=_STATE_MAX_AGE, httponly=True, samesite="lax")
    return resp


@router.get("/callback/x")
def x_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db),
):
    """Exchange the code (Basic auth + PKCE verifier), resolve the @username, store the account."""
    if not settings.x_client_secret:
        raise HTTPException(status_code=503, detail="X client_secret non configuré.")
    if not request.cookies.get(_STATE_COOKIE) or request.cookies.get(_STATE_COOKIE) != state:
        raise HTTPException(status_code=400, detail="État OAuth invalide.")
    verifier = request.cookies.get(_VERIFIER_COOKIE)
    if not verifier:
        raise HTTPException(status_code=400, detail="Vérificateur PKCE manquant.")

    try:
        r = httpx.post(
            X_TOKEN,
            auth=(settings.x_client_id, settings.x_client_secret),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _x_redirect_uri(),
                "code_verifier": verifier,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Échec réseau échange de code X: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Échange de code X échoué: {r.text[:200]}")
    data = r.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Pas de access_token dans la réponse X.")

    username = ""
    user_id = ""
    try:
        me = httpx.get(f"{X_API}/users/me", headers={"Authorization": f"Bearer {access_token}"}, timeout=30.0)
        if me.status_code == 200:
            d = me.json().get("data") or {}
            username = d.get("username", "")
            user_id = str(d.get("id", ""))
    except Exception:  # noqa: BLE001
        pass

    if not username:
        raise HTTPException(status_code=400, detail="Impossible de résoudre le compte X (username).")

    upsert_account(
        db,
        platform="x",
        handle=username,
        account_meta={"username": username, "user_id": user_id, "source": "oauth"},
        token_enc=encrypt_secret(access_token),
        refresh_token_enc=encrypt_secret(refresh_token) if refresh_token else None,
        identity_key="username",
    )

    resp = RedirectResponse("/#settings", status_code=302)
    resp.delete_cookie(_STATE_COOKIE)
    resp.delete_cookie(_VERIFIER_COOKIE)
    return resp
