"""LLM provider settings (read for all, write/test for admin)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as cfg
from app.deps import get_current_operator, get_db, require_admin
from app.llm import DEFAULT_BASE_URLS, _declared_local_url, list_models, local_endpoint_allowed, test_connection
from app.models import LLMSetting, Operator
from app.schemas import LLMSettingIn, LLMSettingOut, LLMTestIn, LLMTestOut, ModelsOut
from app.security import decrypt_secret, encrypt_secret

router = APIRouter(prefix="/settings/llm", tags=["llm"])


def _get_or_create(db: Session) -> LLMSetting:
    setting = db.scalar(select(LLMSetting).limit(1))
    if setting is None:
        setting = LLMSetting(
            provider=cfg.llm_provider,
            base_url=cfg.llm_base_url,
            api_key_enc=encrypt_secret(cfg.llm_api_key) if cfg.llm_api_key else None,
            model=cfg.llm_model,
            image_model=cfg.llm_image_model,
            video_model=cfg.llm_video_model,
        )
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


def _to_out(s: LLMSetting) -> LLMSettingOut:
    return LLMSettingOut(
        provider=s.provider,
        base_url=s.base_url,
        model=s.model,
        image_model=s.image_model,
        video_model=s.video_model,
        monthly_limit=s.monthly_limit,
        topup_mode=s.topup_mode,
        is_active=s.is_active,
        has_api_key=s.api_key_enc is not None,
        updated_at=s.updated_at,
    )


@router.get("", response_model=LLMSettingOut)
def get_llm(db: Session = Depends(get_db), _: Operator = Depends(get_current_operator)):
    return _to_out(_get_or_create(db))


@router.put("", response_model=LLMSettingOut)
def update_llm(
    payload: LLMSettingIn,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    setting = _get_or_create(db)
    new_provider = payload.provider
    new_url = (payload.base_url or DEFAULT_BASE_URLS.get(payload.provider, "")).rstrip("/")
    endpoint_changed = (new_provider, new_url) != (setting.provider or "",
                                                   (setting.base_url or "").rstrip("/"))
    supplied_key = (payload.api_key or "").strip()
    if endpoint_changed and setting.api_key_enc and not supplied_key:
        # Two ordinary requests used to be enough to move the stored key to a host of the caller's
        # choosing: save a new base_url with no key (the old key is kept), then ask for a model list with
        # no base_url — the resolved URL is then the stored one, which is where the key was attached. A
        # key belongs to one endpoint, so changing the endpoint means entering the key again. Refused
        # rather than cleared, so nothing disappears without a word.
        #
        # `supplied_key` is stripped, not truth-tested: a whitespace-only api_key used to read as a key,
        # so a PUT with " " moved the endpoint *and* replaced the stored key with an empty one — the
        # stored credential was destroyed and the address walked, in one request (a review measured it).
        raise HTTPException(
            status_code=400,
            detail="Changer l'adresse du fournisseur demande la clé API à nouveau : une clé enregistrée "
                   "n'est jamais reportée sur une nouvelle adresse.")
    setting.provider = payload.provider
    setting.base_url = payload.base_url or DEFAULT_BASE_URLS.get(payload.provider, "")
    setting.model = payload.model or setting.model
    setting.image_model = payload.image_model or setting.image_model
    setting.video_model = payload.video_model or setting.video_model
    setting.monthly_limit = payload.monthly_limit
    setting.topup_mode = payload.topup_mode
    if supplied_key:
        setting.api_key_enc = encrypt_secret(supplied_key)
    db.commit()
    db.refresh(setting)
    return _to_out(setting)


@router.post("/test", response_model=LLMTestOut)
def test_llm(
    payload: LLMTestIn,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    setting = _get_or_create(db)
    resolved = ((payload.base_url or "").strip() or DEFAULT_BASE_URLS.get(payload.provider, "")
                or setting.base_url or "").rstrip("/")
    allow_local = local_endpoint_allowed(payload.provider, resolved, _declared_local_url())
    ok, detail = test_connection(payload.provider, resolved, (payload.api_key or "").strip(),
                                 allow_local=allow_local)
    return LLMTestOut(ok=ok, detail=detail)


@router.post("/models", response_model=ModelsOut)
def models(
    payload: LLMTestIn,
    modality: str | None = None,
    db: Session = Depends(get_db),
    _: Operator = Depends(require_admin),
):
    """List models for autocomplete, filtered by output modality if requested.

    Falls back to the saved base_url / key. Model listing is free (no credits).
    """
    if modality is not None and modality not in ("text", "image", "video"):
        raise HTTPException(status_code=400, detail="modality invalide")
    setting = _get_or_create(db)
    base_url = payload.base_url or DEFAULT_BASE_URLS.get(payload.provider, "") or setting.base_url
    # The stored key belongs to the stored endpoint, and the test is on the URL that will be *fetched*,
    # not on which field the request filled in. The previous version attached it whenever the request
    # named no base_url — so naming a different provider picked up that provider's shipped default and the
    # stored key went with it, which is the credential-disclosure finding it was written to close, still
    # reachable through a request field (a review reached it with the app's own test client). Now: the key
    # is attached only when the resolved URL is exactly the stored one. A request that names an address
    # OTHER than the stored one gets only the key it supplies — naming the stored address is not a leak, it
    # is the stored endpoint, and the route above refuses to let the stored endpoint move without the key.
    supplied = (payload.base_url or "").strip()
    resolved = (supplied or DEFAULT_BASE_URLS.get(payload.provider, "") or setting.base_url or "").rstrip("/")
    stored_url = (setting.base_url or "").strip().rstrip("/")
    supplied_key = (payload.api_key or "").strip()
    api_key = supplied_key
    if not api_key and setting.api_key_enc and stored_url and resolved == stored_url:
        api_key = decrypt_secret(setting.api_key_enc)
    # The allowance is decided by local_endpoint_allowed from the provider mode and the address that will be
    # fetched — not from who supplied it (see that function; a request naming ollama's shipped endpoint takes
    # it, deliberately, and the bounds are stated there).
    allow_local = local_endpoint_allowed(payload.provider, base_url, _declared_local_url())
    try:
        return ModelsOut(models=list_models(payload.provider, base_url, api_key, modality,
                                           allow_local=allow_local))
    except Exception as e:  # noqa: BLE001 — surface a clean error to the admin
        raise HTTPException(status_code=400, detail=f"Impossible de lister les modèles : {e}")
