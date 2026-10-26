"""LLM provider abstraction — constants + lightweight connectivity test.

The actual generation calls (Phase 3) live in this package too. Phase 2 only
needs provider constants, default base URLs, and `test_connection()` for the
Settings "Tester" button.
"""
import base64
import hashlib
import json

import httpx

PROVIDERS = ["openrouter", "openai_compatible", "ollama", "none", "demo"]

DEFAULT_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai_compatible": "",
    "ollama": "http://localhost:11434",
}

# Curated fallbacks for "auto" model selection (explicit selection always wins).
# Chosen for price/performance per task: nano-banana is proven for text+logo in
# images; claude-sonnet-4 for creative text; veo for video.
DEFAULT_MODELS = {
    "text": "anthropic/claude-sonnet-4",
    "image": "google/gemini-2.5-flash-image",
    "video": "google/veo-3.1",
}

# Appended to every image-generation prompt: a base render-quality bar.
IMAGE_BASE_HINT = (
    "\n\nHigh quality, well-composed, coherent lighting, harmonious color palette, "
    "sharp details, clean typography (no garbled text)."
)


def _usage_cost(usage: dict) -> float:
    """Safely read OpenRouter's `usage.cost` (credits, USD) from a response."""
    try:
        return float(usage.get("cost") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _provider_cost(usage: dict | None) -> float | None:
    """The provider's own cost, or ``None`` when it reported none.

    ``None`` ("we do not know") and ``0.0`` ("this call was free") are different
    statements: an openai_compatible endpoint such as DeepSeek direct never sends
    ``usage.cost``, so returning 0.0 there would record a silent $0 forever.
    Passing None lets ``app.usage_meter`` price the call from the pinned map
    instead. Parsing stays tolerant (``_usage_cost``) once a cost is present.
    """
    if not isinstance(usage, dict) or usage.get("cost") is None:
        return None
    return _usage_cost(usage)



# --- Mode démo -------------------------------------------------------------------------------------
# Aucun réseau, aucune clé, aucune donnée réelle. Le fournisseur « demo » rend des réponses
# déterministes, construites à partir de la demande de l'utilisateur, pour faire visiter le parcours
# complet — génération, tri des commentaires, brouillon de réponse — sans fournisseur de modèle.
# Ce n'est pas un faux produit : c'est un mode explicite, visible dans les réglages, qui ne peut pas
# se confondre avec une vraie génération.

DEMO_PLATFORMS = ("tiktok", "instagram", "linkedin", "facebook", "threads", "bluesky", "pinterest", "x")

_DEMO_CONCEPTS = (
    "Le geste quotidien qui change tout, montré en trois secondes.",
    "L'envers du décor : ce que personne ne filme jamais.",
    "Une preuve, pas une promesse : le résultat avant / après.",
)


def _demo_last_user(messages: list[dict]) -> str:
    for message in reversed(messages or []):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _demo_all_text(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content") or "") for m in (messages or []))


def _demo_is_classification(text: str) -> bool:
    return "green" in text and "orange" in text and "red" in text and "JSON" in text


def _demo_is_generation(text: str) -> bool:
    return '"proposals"' in text or "JSON valide" in text or '"variants"' in text


def demo_completion(messages: list[dict], model: str = "demo-text") -> dict:
    """La réponse d'un fournisseur absent : déterministe, honnête, jamais une imitation de modèle.

    Trois formes, selon ce que le code appelant attend : une liste de propositions (l'agent de
    génération), un tableau de niveaux (le tri des commentaires), ou un brouillon de réponse.
    """
    tout = _demo_all_text(messages)
    demande = _demo_last_user(messages).strip()
    graine = int(hashlib.sha256(demande.encode("utf-8")).hexdigest()[:8], 16)

    if _demo_is_classification(tout):
        # Une étiquette par commentaire numéroté dans la demande, dans l'ordre.
        import re as _re
        nombre = len(_re.findall(r"^\s*\d+\.\s", demande, flags=_re.MULTILINE)) or 1
        cycle = ("green", "orange", "red")
        content = json.dumps([cycle[i % 3] for i in range(nombre)])
    elif _demo_is_generation(tout):
        sujet = demande[:160] or "un post de démonstration"
        propositions = []
        for i in range(3):
            concept = _DEMO_CONCEPTS[(graine + i) % len(_DEMO_CONCEPTS)]
            variants = {}
            for plateforme in DEMO_PLATFORMS:
                variants[plateforme] = {
                    "caption": f"[démo] {concept} — {sujet}",
                    "hook": f"[démo] {concept}",
                    "cta": "Dites-moi ce que vous en pensez.",
                    "hashtags": ["#demo", "#socialpilot"],
                    "media_format": "9:16" if plateforme in ("tiktok", "instagram") else "1:1",
                }
            propositions.append({
                "number": i + 1,
                "concept": concept,
                "media_prompt": ("A simple flat illustration representing the idea, clean composition, "
                                 "soft natural light, coherent palette, no text"),
                "media_prompt_fr": "Une illustration plate et simple représentant l'idée, composition nette.",
                "style_notes": "[démo] Direction artistique simulée, aucun appel de modèle.",
                "variants": variants,
            })
        content = json.dumps({"proposals": propositions})
    else:
        niveau = "positif" if graine % 3 == 0 else ("question" if graine % 3 == 1 else "plainte")
        content = (f"[démo] Merci pour votre message — il est bien arrivé et il sera traité. "
                   f"(Réponse simulée : aucun modèle n'est appelé en mode démo.)")
    return {"content": content, "model": model, "provider": "demo",
            "tokens": {"prompt": 0, "completion": 0, "total": 0}}


def is_demo_mode() -> bool:
    """Le mode démo est actif quand le fournisseur de modèle est « demo ».

    Lu en base plutôt que dans l'environnement : c'est le même réglage que celui affiché dans les
    Réglages, donc un seul endroit à changer, et aucune divergence possible entre ce que l'écran dit
    et ce que le code fait. Une base absente ou vide vaut « pas en démo ».
    """
    try:
        from sqlalchemy import select

        from app.database import SessionLocal
        from app.models import LLMSetting

        db = SessionLocal()
        try:
            reglage = db.scalar(select(LLMSetting).limit(1))
        finally:
            db.close()
        return bool(reglage is not None and reglage.provider == "demo")
    except Exception:  # noqa: BLE001 — pas de base, pas de démo
        return False


def demo_image(prompt: str, size: str | None = None) -> bytes:
    """Une vignette générée localement (Pillow), pour que le parcours visuel tienne sans réseau."""
    from io import BytesIO

    from PIL import Image, ImageDraw

    largeur = hauteur = 1024
    if size and "x" in size:
        try:
            largeur, hauteur = (int(v) for v in size.lower().split("x", 1))
        except ValueError:
            largeur = hauteur = 1024
    largeur, hauteur = max(256, min(largeur, 1024)), max(256, min(hauteur, 1024))

    image = Image.new("RGB", (largeur, hauteur), (18, 22, 33))
    dessin = ImageDraw.Draw(image)
    for y in range(hauteur):
        teinte = int(18 + 52 * (y / max(hauteur - 1, 1)))
        dessin.line([(0, y), (largeur, y)], fill=(teinte, teinte + 6, teinte + 20))
    dessin.rectangle([24, 24, largeur - 24, hauteur - 24], outline=(90, 140, 220), width=3)
    mention = "[MODE DÉMO] image générée localement, aucun modèle appelé"
    dessin.text((40, hauteur - 70), mention, fill=(200, 210, 230))
    dessin.text((40, 40), (prompt or "")[:90], fill=(235, 240, 250))
    flux = BytesIO()
    image.save(flux, format="PNG")
    return flux.getvalue()


def resolve_model(setting, task: str = "text") -> str:
    """Resolve the model for a task. Explicit selection wins; 'auto'/empty → curated default."""
    val = {"text": setting.model, "image": setting.image_model, "video": setting.video_model}.get(
        task, setting.model
    )
    if val and val.strip().lower() != "auto":
        return val
    return DEFAULT_MODELS.get(task) or setting.model

MODELS_ENDPOINTS = {
    "openrouter": "/models",
    "openai_compatible": "/models",
    "ollama": "/api/tags",
}


def _declared_local_url() -> str:
    """The local provider endpoint this deployment declared (SP_LOCAL_LLM_URL), if any.

    A deployment fact, read from the environment. It is what the outbound guard consults before allowing a
    loopback address — deliberately not the stored setting, which is written through the API.
    """
    from app.config import get_settings

    return get_settings().local_llm_url or ""


def local_endpoint_allowed(provider: str, resolved_base_url: str | None,
                           declared_local_url: str = "") -> bool:
    """May the address about to be fetched be a loopback one?

    Only when it is an endpoint **this deployment declared** for a local provider: `SP_LOCAL_LLM_URL` from
    the environment — a file the operator writes — or the shipped default for that provider.

    Four versions of this rule were written and the reviews read each one, which is why it is spelled out
    here rather than inferred:

      * deciding from `bool(payload.base_url)` described a comparison the code did not make;
      * comparing against the *stored* setting was tautological;
      * testing "did the request name an address" refused the operator their own saved provider through the
        UI that exists;
      * and allowing whatever the stored setting says is **the SSRF this guard exists to prevent**, because
        that setting is written through the API by the same admin the guard is aimed at — two requests (save
        a loopback URL, then ask for a model list) put any loopback port of the host within reach.

    The line that holds: the allowance follows a *deployment* fact, not a stored one. It is checked against
    the address that will actually be fetched, so the decision and the connection cannot disagree.
    """
    if provider != "ollama":
        return False
    resolved = (resolved_base_url or "").rstrip("/")
    if not resolved:
        return False
    allowed = {(declared_local_url or "").rstrip("/"),
               (DEFAULT_BASE_URLS.get(provider) or "").rstrip("/")}
    allowed.discard("")
    return resolved in allowed


def test_connection(provider: str, base_url: str, api_key: str | None, *,
                    allow_local: bool = False) -> tuple[bool, str]:
    """Lightweight connectivity check against the provider's models endpoint.

    `base_url` is an address chosen outside this code — in the settings routes it arrives in the request
    body, and `require_admin` on a whitelabel instance means the *client's* admin, not us. A bare
    `client.get(base_url + endpoint)` therefore made this server fetch whatever the caller named: a
    second review pointed one at the cloud metadata service. It now goes through app/net_guard.py, the
    same single decision point as the media fetch: https, every resolved address globally routable.

    `allow_local` relaxes the loopback rule for exactly one case, and the rule is the predicate
    `local_endpoint_allowed`: the provider mode is the local one and the address about to be fetched is the
    endpoint we ship for it or one this **deployment** declared (`SP_LOCAL_LLM_URL`). A stored setting grants
    nothing on its own — it is request-writable by the same admin the guard constrains — and an address the
    request names takes the allowance only when it is one of those two.
    """
    if provider == "none":
        return False, "Aucun fournisseur configuré."
    if provider == "demo":
        return True, "Mode démo : aucun appel réseau, aucune clé."
    url = (base_url or DEFAULT_BASE_URLS.get(provider, "")).rstrip("/")
    if not url:
        return False, "Base URL manquante."
    from app.net_guard import MediaFetchRefused, guarded_request  # noqa: PLC0415
    endpoint = MODELS_ENDPOINTS.get(provider, "/models")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        r = guarded_request("GET", url + endpoint, headers=headers, timeout=8.0,
                            allow_loopback=(allow_local and provider == "ollama"))
    except MediaFetchRefused as exc:
        return False, f"Adresse refusée : {exc}"
    except httpx.HTTPError as e:
        return False, f"Connexion impossible : {e}"
    if r.status_code < 400:
        return True, f"Connecté ({r.status_code}) — {url}"
    return False, f"Erreur HTTP {r.status_code} : {r.text[:200]}"


def _commercial_name(name: str) -> str:
    """Strip vendor prefix and technical parenthetical from OpenRouter's `name`.

    "Google: Nano Banana (Gemini 2.5 Flash Image)" -> "Nano Banana"
    "Black Forest Labs: FLUX.2 Pro"                  -> "FLUX.2 Pro"
    """
    if not name:
        return ""
    n = name
    if ": " in n:
        n = n.split(": ", 1)[1]
    if " (" in n:
        n = n.split(" (", 1)[0]
    return n.strip()


def list_models(provider: str, base_url: str, api_key: str | None, modality: str | None = None, *,
                allow_local: bool = False) -> list[dict]:
    """List models available from the provider as [{id, name}] (for autocomplete).

    `name` is the cleaned commercial name (e.g. "Nano Banana") so the UI can show
    it in parentheses next to the technical id. `modality` (text|image|video)
    filters on the provider's output_modalities where available. Ollama has no
    per-model metadata → name is empty.
    """
    if provider == "none":
        return []
    if provider == "demo":
        return [{"id": "demo-text", "name": "Démo — texte"},
                {"id": "demo-image", "name": "Démo — image"}]
    url = (base_url or DEFAULT_BASE_URLS.get(provider, "")).rstrip("/")
    if not url:
        return []
    # Same guard as test_connection, for the same reason: this URL can arrive in a request body.
    from app.net_guard import MediaFetchRefused, guarded_request
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    local = allow_local and provider == "ollama"
    if provider == "ollama":
        try:
            r = guarded_request("GET", url + "/api/tags", headers=headers, timeout=8.0,
                                allow_loopback=local)
        except MediaFetchRefused as exc:
            raise RuntimeError(f"Adresse refusée : {exc}") from exc
        r.raise_for_status()
        return sorted(
            ({"id": m.get("name", ""), "name": ""} for m in r.json().get("models", []) if m.get("name")),
            key=lambda x: x["id"],
        )
    # openrouter / openai_compatible
    endpoint = url + "/models"
    if provider == "openrouter" and modality:
        # OpenRouter filters server-side via output_modalities (video models only
        # appear when requested).
        endpoint += f"?output_modalities={modality}"
    try:
        r = guarded_request("GET", endpoint, headers=headers, timeout=20.0, allow_loopback=local)
    except MediaFetchRefused as exc:
        raise RuntimeError(f"Adresse refusée : {exc}") from exc
    r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        mid = m.get("id")
        if not mid:
            continue
        if modality and provider != "openrouter":
            # Client-side filter for OpenAI-compatible providers that expose
            # architecture metadata. OpenRouter is already filtered above.
            outputs = (m.get("architecture") or {}).get("output_modalities")
            if outputs is not None and modality not in outputs:
                continue
        out.append({"id": mid, "name": _commercial_name(m.get("name", ""))})
    out.sort(key=lambda x: x["id"])
    return out


def _chat_endpoint(provider: str, base_url: str) -> str:
    url = base_url.rstrip("/")
    if provider == "ollama" and not url.endswith("/v1"):
        url += "/v1"  # Ollama exposes OpenAI-compat under /v1
    # The guard is applied where the request is made (chat_completion / image_generation), through
    # guarded_request: a check here would only cover the URL, and the connection would be resolved again
    # by httpx. Both surfaces are the provider's address — a value persisted by the settings route.
    return url + "/chat/completions"


def chat_completion(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> dict:
    """OpenAI-compatible chat completion against the active provider.

    Returns {"content", "model", "provider", "tokens"}. Records a UsageRecord.
    Raises RuntimeError with a French message on any failure.
    """
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import LLMSetting, UsageRecord
    from app.security import decrypt_secret
    from app.usage_meter import record_usage

    db = SessionLocal()
    try:
        setting = db.scalar(select(LLMSetting).limit(1))
    finally:
        db.close()
    if setting is None or setting.provider == "none":
        raise RuntimeError("Aucun fournisseur LLM configuré (voir Settings).")
    if setting.provider == "demo":
        return demo_completion(messages, model or "demo-text")
    base_url = (setting.base_url or DEFAULT_BASE_URLS.get(setting.provider, "")).rstrip("/")
    if not base_url:
        raise RuntimeError("Base URL manquante (voir Settings).")
    api_key = decrypt_secret(setting.api_key_enc) if setting.api_key_enc else None
    model = model or resolve_model(setting, "text")
    if not model:
        raise RuntimeError("Aucun modèle sélectionné (voir Settings).")

    endpoint = _chat_endpoint(setting.provider, base_url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    body: dict = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens:
        body["max_tokens"] = max_tokens
    from app.net_guard import MediaFetchRefused, guarded_request
    try:
        r = guarded_request("POST", endpoint, headers=headers, json_body=body, timeout=120.0,
                            allow_loopback=local_endpoint_allowed(setting.provider, base_url,
                                                                 _declared_local_url()))
    except MediaFetchRefused as exc:
        raise RuntimeError(f"Adresse du fournisseur refusée : {exc}") from exc
    except httpx.TimeoutException:
        raise RuntimeError("Le modèle a mis trop de temps à répondre — réessaie, ça devrait passer.")
    except httpx.HTTPError as e:
        raise RuntimeError(f"Appel LLM impossible : {e}")
    if r.status_code >= 400:
        raise RuntimeError(f"LLM erreur HTTP {r.status_code} : {r.text[:300]}")
    try:
        data = r.json()
        content = (data["choices"][0]["message"].get("content") or "").strip()
        usage = data.get("usage") or {}
    except (KeyError, IndexError, ValueError):
        raise RuntimeError("Réponse LLM inattendue.")

    tokens = usage.get("total_tokens") or (
        usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    )
    db = SessionLocal()
    try:
        record_usage(
            db, UsageRecord, kind="llm", provider=setting.provider, model=model,
            usage=usage, provider_cost=_provider_cost(usage),
        )
    finally:
        db.close()
    return {"content": content, "model": model, "provider": setting.provider, "tokens": tokens or 0}


def _extract_image(data: dict) -> str | None:
    """Pull an image out of an image-generation response (several known shapes).

    Returns a `data:` URL or an http(s) URL, or None if nothing is found.
    """
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError):
        return None
    # Shape A: message.images[0].image_url.url
    for img in (msg.get("images") or []):
        if isinstance(img, dict):
            u = (img.get("image_url") or {}).get("url")
            if u:
                return u
    # Shape B: message.content is a list of parts
    content = msg.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("image_url", "image"):
                iu = part.get("image_url") or {}
                u = iu.get("url") if isinstance(iu, dict) else part.get("url")
                if u:
                    return u
    # Shape C: message.content is a data URL string
    if isinstance(content, str) and content.startswith("data:"):
        return content
    return None


#: The largest decoded media we will hold for one asset. The https path enforces this while streaming;
#: a `data:` URL and a `b64_json` field arrive *already in memory* in the provider's response, so for
#: those two the payload is measured before it is decoded — otherwise the cap the [egress] policy names
#: would be bypassable by answering with one huge base64 string (a second review's fourth finding).
MAX_MEDIA_BYTES = 25 * 1024 * 1024


def _b64_limited(payload: str, what: str) -> bytes:
    """Decode base64 from a provider response, refusing anything over the cap before allocating."""
    import base64

    clean = "".join(payload.split())
    if len(clean) > MAX_MEDIA_BYTES * 4 // 3 + 8:
        raise RuntimeError(
            f"the provider's {what} is larger than the {MAX_MEDIA_BYTES} byte cap — refused before "
            f"decoding")
    try:
        data = base64.b64decode(clean, validate=True)
    except Exception as exc:
        raise RuntimeError(f"the provider's {what} is not valid base64: {exc}") from exc
    if len(data) > MAX_MEDIA_BYTES:
        raise RuntimeError(f"the provider's {what} is larger than the {MAX_MEDIA_BYTES} byte cap")
    return data


def _download_image_bytes(url_or_dataurl: str) -> bytes:
    """Bytes of the generated media, from a data URL or from the provider's own URL.

    The URL in the second case is chosen by the provider, not by us, so it goes through the one place
    that decides what this server may fetch (`app/net_guard.py`) — https only, every resolved address
    globally routable, no redirects, a size cap. A second review found this function doing a bare
    `httpx.get` on a URL taken from the response body, which is an SSRF sink.
    """
    import base64

    from app.net_guard import fetch_media

    if url_or_dataurl.startswith("data:"):
        _, _, b64 = url_or_dataurl.partition(",")
        return _b64_limited(b64, "data: URL")
    return fetch_media(url_or_dataurl)


def _extract_image_bytes(data: dict) -> bytes:
    """Decode an image-generation response (both /images and chat-completions shapes)."""
    # OpenRouter /images response: {"data": [{"b64_json": "...", "media_type": ...}]}
    item = None
    try:
        item = data["data"][0]
    except (KeyError, IndexError, TypeError):
        item = None
    if isinstance(item, dict):
        if item.get("b64_json"):
            return _b64_limited(item["b64_json"], "b64_json field")
        if item.get("url"):
            return _download_image_bytes(item["url"])
    # Fallback: chat-completions style (message.images / content parts)
    url = _extract_image(data)
    if url:
        return _download_image_bytes(url)
    raise RuntimeError("La réponse image ne contient pas de média exploitable.")


def image_generation(prompt: str, model: str | None = None, reference_images: list[bytes] | None = None, size: str | None = None) -> bytes:
    """Generate an image via the active provider's image model (OpenRouter).

    `reference_images` are passed as multimodal inputs (logo, imported media) so
    the model can integrate them. Returns raw image bytes. Raises RuntimeError
    with a French message on failure.
    """
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import LLMSetting, UsageRecord
    from app.security import decrypt_secret
    from app.usage_meter import record_usage

    db = SessionLocal()
    try:
        setting = db.scalar(select(LLMSetting).limit(1))
    finally:
        db.close()
    if setting is None or setting.provider == "none":
        raise RuntimeError("Aucun fournisseur LLM configuré (voir Settings).")
    if setting.provider == "demo":
        return demo_image(prompt, size)
    base_url = (setting.base_url or DEFAULT_BASE_URLS.get(setting.provider, "")).rstrip("/")
    if not base_url:
        raise RuntimeError("Base URL manquante (voir Settings).")
    api_key = decrypt_secret(setting.api_key_enc) if setting.api_key_enc else None
    model = model or resolve_model(setting, "image")
    if not model:
        raise RuntimeError("Aucun modèle image sélectionné (voir Settings).")

    # Use OpenRouter's native /images endpoint: reference images go through
    # `input_references` (the chat-completions multimodal path ignores them).
    endpoint = base_url.rstrip("/") + "/images"
    # The second of the two sinks that carry the client's own content (a prompt and, when present,
    # reference images of their brand). Guarded like the chat endpoint above for the same reason: the
    # saved base_url is an address chosen outside this code, and a second review found the generation
    # paths unguarded while the two listing functions were covered.
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    body: dict = {"model": model, "prompt": prompt + IMAGE_BASE_HINT, "n": 1}
    if size:
        body["size"] = size
    if reference_images:
        body["input_references"] = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img).decode()}}
            for img in reference_images
        ]
    from app.net_guard import MediaFetchRefused, guarded_request
    try:
        r = guarded_request("POST", endpoint, headers=headers, json_body=body, timeout=240.0,
                            allow_loopback=local_endpoint_allowed(setting.provider, base_url,
                                                                 _declared_local_url()))
    except MediaFetchRefused as exc:
        raise RuntimeError(f"Adresse du fournisseur refusée : {exc}") from exc
    except httpx.TimeoutException:
        raise RuntimeError("La génération d'image a mis trop de temps — réessaie.")
    except httpx.HTTPError as e:
        raise RuntimeError(f"Génération image impossible : {e}")
    if r.status_code >= 400:
        raise RuntimeError(f"Génération image erreur HTTP {r.status_code} : {r.text[:300]}")

    resp = r.json()
    data = _extract_image_bytes(resp)
    usage = resp.get("usage") if isinstance(resp, dict) else None

    db = SessionLocal()
    try:
        record_usage(
            db, UsageRecord, kind="media", provider=setting.provider, model=model,
            usage=usage, provider_cost=_provider_cost(usage),
        )
    finally:
        db.close()
    return data
