"""Post production pipeline — media generation, compliance scan, publication."""
import base64
import datetime
import json as _json
import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import breaker
from app.agents import _branding_usage_context
from app.auth import utcnow
from app.config import PROJECT_ROOT, get_settings
from app.llm import chat_completion, image_generation
from app.models import BrandAsset, MediaItem, Post, Publication

logger = logging.getLogger("social_pilot.pipeline")

MEDIA_DIR = PROJECT_ROOT / "media"


def variant_for(content: dict | None, platform: str) -> dict:
    """Return the per-platform variant of a post's content, falling back to flat fields."""
    c = content or {}
    variants = c.get("variants")
    v = variants.get(platform) if isinstance(variants, dict) else None
    if isinstance(v, dict):
        return v
    return {
        "caption": c.get("caption", ""),
        "hook": c.get("hook", ""),
        "cta": c.get("cta", ""),
        "hashtags": c.get("hashtags", []),
    }

_COMPLIANCE_PROMPTS = {
    "fr": (
        "Tu es expert en conformité réseaux sociaux (TikTok + Instagram) et propriété "
        "intellectuelle. Analyse cette publication et réponds STRICTEMENT en JSON, sans autre texte :\n"
        '{"verdict": "pass"|"risk"|"fail", "risks": ["risque 1", "risque 2"]}\n\n'
        "Critères : copyright/PI (marques tierces visibles, musique non licenciée, watermark), "
        "contenus interdits, claims trompeurs (santé, argent), contenu choquant, règles TikTok/Instagram. "
        '"pass" = aucun risque ; "risk" = risque mineur, ajustement recommandé ; "fail" = bloquant.\n\n'
        "Contenu :\n"
    ),
    "en": (
        "You are a social-media compliance and intellectual-property expert. Analyze this "
        "post and respond STRICTLY in JSON, no other text:\n"
        '{"verdict": "pass"|"risk"|"fail", "risks": ["risk 1", "risk 2"]}\n\n'
        "Criteria: copyright/IP (visible third-party brands, unlicensed music, watermarks), "
        "prohibited content, misleading claims (health, money), shocking content, TikTok/Instagram rules. "
        '"pass" = no risk; "risk" = minor risk, adjustment recommended; "fail" = blocking.\n\n'
        "Content:\n"
    ),
}

_INCONCLUSIVE = {
    "fr": "Analyse inconclusive — vérifier manuellement.",
    "en": "Inconclusive analysis — review manually.",
}


def _save_generated_image(db: Session, owner_id, data: bytes) -> MediaItem:
    """Store one generated image, with the Article 50(2) mark in its pixels.

    The mark is applied here because the bytes are already at publish size: `generate_media` asks the provider
    for the platform's own dimensions, so nothing resizes this image afterwards — and a resize is the one
    operation measured to erase the mark (app/ai_marking.py's docstring carries the three measurements).

    `ai_marking` is set from what was read back out of the marked bytes, never from the intent to mark: an
    unmarked asset is stored as unmarked, and the production-ring publish gate then refuses it.
    """
    from app.ai_marking import mark_generated

    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex
    path = MEDIA_DIR / f"{uid}.png"
    marked, verified = mark_generated(data, uid)
    path.write_bytes(marked)
    item = MediaItem(
        owner_id=owner_id,
        kind="image",
        original_name=f"genere-{uid[:8]}.png",
        stored_path=f"media/{uid}.png",
        mime="image/png",
        size_bytes=len(marked),
        # Article 50(2), set from the verification and not from the intention to mark.
        ai_generated=True,
        ai_marking="watermark" if verified else "none",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


#: What counts as the durable mark. `none` is the absence of one, and `pending` is a mark that was
#: meant to be applied and was not — neither may reach a client's account.
MARKED = ("watermark", "c2pa", "watermark+c2pa")


class UnmarkedGeneratedMedia(RuntimeError):
    """A generated asset with no durable mark was about to be published (AI Act Article 50(2))."""


def unmarked_generated_media(db: Session, post) -> list[MediaItem]:
    """This post's AI-generated assets that carry no mark, newest provenance first.

    Deliberately a query over the media rows rather than a flag on the post: the provenance lives with
    the asset, so a post that mixes a generated image with an uploaded one still fails.
    """
    ids: list[str] = []
    for value in list(getattr(post, "media_ids", None) or []):
        ids.append(str(value))
    variants = getattr(post, "media_variants", None)
    if isinstance(variants, dict):
        ids.extend(str(v) for v in variants.values())
    if not ids:
        return []
    wanted = []
    for value in ids:
        try:
            wanted.append(uuid.UUID(value))
        except (TypeError, ValueError):
            continue
    if not wanted:
        return []
    rows = db.scalars(select(MediaItem).where(MediaItem.id.in_(wanted))).all()
    return [m for m in rows if getattr(m, "ai_generated", False)
            and (getattr(m, "ai_marking", "none") or "none") not in MARKED]


#: The rings that may publish an unmarked AI-generated asset. An allow-list, not a check for
#: "production": a second review pointed out that the first version refused only when `SP_ENV` said
#: `production`, while the setting *defaults* to `development` — so a deployment that forgot the variable,
#: misspelled it, or came from a newer template would have had the control silently off. For a gate
#: holding a legal duty the conservative direction is "anything I cannot recognise is production", so the
#: rings that are allowed to proceed are named here, one by one, and everything else refuses.
UNMARKED_ALLOWED_RINGS = ("test", "devnet")


def assert_publishable(db: Session, pub: "Publication") -> None:
    """Refuse to publish an unmarked generated asset on any ring but the named dev ones.

    Called at the top of `publish_publication`, before any adapter is reached, so the failure happens
    here — visibly, in the publication's own error — instead of at the platform.

    Scoped by ring because the exposure the duty is about is *a client's account*: the production ring
    publishes to one, the test suite and the devnet publish to test accounts, and blocking those would
    break the demo of a flow whose marking is a declared, dated open item. The ring comes from the
    deployment's own `SP_ENV`, and an unrecognised value is treated as production.
    """
    post = db.get(Post, pub.post_id) if getattr(pub, "post_id", None) else None
    if post is None:
        return
    unmarked = unmarked_generated_media(db, post)
    if not unmarked:
        return
    names = ", ".join(str(m.id)[:8] for m in unmarked[:4])
    detail = (f"{len(unmarked)} AI-generated asset(s) with no durable mark ({names}). AI Act Article "
              f"50(2) asks for a mark detectable in the published file, and the mark has to be in the "
              f"pixels at publish size: the platform re-encodes and strips metadata. Mark the asset, "
              f"set media_items.ai_marking, then publish.")
    ring = (get_settings().env or "").strip().lower()
    if ring not in UNMARKED_ALLOWED_RINGS:
        known = "ring unknown" if not ring else f"ring {ring!r}"
        raise UnmarkedGeneratedMedia(
            f"refusing to publish {detail} This is {known}, and only these rings may publish unmarked "
            f"generated media: {', '.join(UNMARKED_ALLOWED_RINGS)} (SP_ENV).")
    logger.warning("publishing unmarked AI-generated media on the %s ring: %s", ring, detail)


def _media_bytes(db: Session, media_id) -> bytes | None:
    """Read an image MediaItem's bytes (for multimodal reference)."""
    try:
        mid = uuid.UUID(str(media_id))
    except (ValueError, TypeError):
        return None
    item = db.get(MediaItem, mid)
    if item is None or item.kind != "image":
        return None
    path = PROJECT_ROOT / item.stored_path
    return path.read_bytes() if path.exists() else None


def _reference_images(db: Session, post: Post) -> list[bytes]:
    """Logo (from the branding kit) + media attached to the original request."""
    refs: list[bytes] = []
    logo = db.scalar(
        select(BrandAsset)
        .where(BrandAsset.kind == "logo", BrandAsset.media_id.is_not(None))
        .order_by(BrandAsset.created_at)
        .limit(1)
    )
    if logo is not None:
        b = _media_bytes(db, logo.media_id)
        if b:
            refs.append(b)
    for mid in (post.source_media_ids or []):
        b = _media_bytes(db, mid)
        if b:
            refs.append(b)
    return refs


def _build_media_prompt(db: Session, post: Post, feedback: str | None = None) -> str:
    prompt = str((post.content or {}).get("media_prompt") or "").strip()
    if not prompt:
        raise RuntimeError("La proposition validée n'a pas de prompt média.")
    parts = [prompt]
    if post.media_brief:
        parts.append("Directive média de l'utilisateur (à respecter strictement) : " + post.media_brief)
    usage = _branding_usage_context(db, post.branding_usage)
    if usage:
        parts.append(usage)
    if feedback:
        parts.append("Ajustement demandé par l'utilisateur : " + feedback)
    return "\n\n".join(parts)


_MEDIA_FORMAT_SIZES = {
    "9:16": "1024x1792",
    "1:1": "1024x1024",
    "4:5": "896x1120",
    "3:4": "896x1195",
    "16:9": "1792x1024",
    "1.91:1": "1344x704",
    "2:3": "896x1344",
}


def _media_size(media_format: str | None) -> str | None:
    return _MEDIA_FORMAT_SIZES.get((media_format or "").strip())


def generate_media(db: Session, post: Post, operator, feedback: str | None = None) -> Post:
    """Generate per-platform media: one image per target platform, at its aspect ratio."""
    prompt = _build_media_prompt(db, post, feedback)
    refs = _reference_images(db, post)
    variants = (post.content or {}).get("variants")
    variants = variants if isinstance(variants, dict) else {}

    media_variants: dict[str, str] = {}
    for platform, v in variants.items():
        if not isinstance(v, dict):
            continue
        data = image_generation(prompt, reference_images=refs, size=_media_size(v.get("media_format")))
        item = _save_generated_image(db, operator.id, data)
        media_variants[platform] = str(item.id)

    if media_variants:
        post.media_ids = [media_variants[p] for p in media_variants]
        post.media_variants = media_variants
    else:
        # Legacy single-platform content (no variants): one shared image.
        data = image_generation(prompt, reference_images=refs)
        item = _save_generated_image(db, operator.id, data)
        post.media_ids = [str(item.id)]
        post.media_variants = None
    post.status = "media_ready"
    db.commit()
    db.refresh(post)
    return post


def _parse_verdict(raw: str, language: str = "en") -> dict:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1].lstrip("json").strip()
    try:
        d = _json.loads(s)
    except Exception:
        d = None
    if not isinstance(d, dict):
        return {"verdict": "risk", "risks": [_INCONCLUSIVE.get(language, _INCONCLUSIVE["en"])]}
    risks = d.get("risks", []) if isinstance(d.get("risks"), list) else []
    return {"verdict": d.get("verdict", "risk"), "risks": risks}


def _parse_json_dict(raw: str) -> dict | None:
    """Tolerant JSON-object parse (handles ```fences``` and stray surrounding text)."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1].lstrip("json").strip()
    try:
        d = _json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if not m:
            return None
        try:
            d = _json.loads(m.group(0))
        except Exception:
            return None
    return d if isinstance(d, dict) else None


_TEXT_HINTS = (
    "text", "texte", "wording", "slogan", "headline", "title", "label",
    "écrire", "inscrire", "afficher", "mention", "write", "read", "say", "quote",
)


def _requests_onscreen_text(media_prompt: str, media_prompt_fr: str) -> bool:
    """Heuristic: does the media prompt ask for specific text rendered on the image?"""
    blob = ((media_prompt or "") + " " + (media_prompt_fr or "")).lower()
    # Quoted strings are the strongest signal (the exact wording to display).
    if '"' in blob or "«" in blob or "“" in blob:
        return True
    return any(h in blob for h in _TEXT_HINTS)


def _check_media_text(db: Session, post: Post) -> list[str]:
    """Vision-check the on-image text against the request. Returns risk strings."""
    if not (post.media_ids or []):
        return []
    on_mt = post.on_media_text or {}
    mode = on_mt.get("mode") or "auto"
    mp = str((post.content or {}).get("media_prompt") or "")
    mp_fr = str((post.content or {}).get("media_prompt_fr") or "")
    if mode == "no":
        return []
    if mode == "yes":
        requested = (on_mt.get("text") or "").strip()
    else:
        if not _requests_onscreen_text(mp, mp_fr):
            return []
        requested = mp
    if not requested:
        return []
    item = db.get(MediaItem, uuid.UUID(str(post.media_ids[0])))
    if item is None or item.kind != "image":
        return []
    path = PROJECT_ROOT / item.stored_path
    if not path.exists():
        return []
    prompt = (
        "Voici l'image générée pour une publication. Le texte demandé était :\n"
        + requested + "\n\n"
        "Vérifie STRICTEMENT le texte visible DANS l'image (pas la caption) :\n"
        "1. Le texte correspond-il exactement à celui demandé (orthographe exacte) ?\n"
        "2. Y a-t-il des fautes, caractères tronqués ou texte illisible ?\n"
        "Réponds en JSON strict, sans autre texte : "
        '{"match": true|false, "text_found": "texte exact lu dans l\'image (ou vide)", "issues": ["problème 1"]}'
    )
    content = [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()}},
        {"type": "text", "text": prompt},
    ]
    raw = chat_completion([{"role": "user", "content": content}], temperature=0.1)["content"]
    d = _parse_json_dict(raw)
    if not d:
        return []
    issues = d.get("issues") if isinstance(d.get("issues"), list) else []
    if d.get("match") is False:
        issues = ["Le texte sur l'image ne correspond pas exactement à la demande."] + issues
    return [str(i).strip() for i in issues if str(i).strip()]


def scan_compliance(db: Session, post: Post, language: str = "en") -> Post:
    """Agent-driven copyright / platform-rule compliance scan."""
    c = post.content or {}
    parts = []
    variants = c.get("variants") if isinstance(c.get("variants"), dict) else {}
    for platform, v in variants.items():
        if not isinstance(v, dict):
            continue
        parts.extend([
            f"[{platform}] Caption : {v.get('caption', '')}",
            f"[{platform}] Hashtags : " + ", ".join(v.get("hashtags", []) or []),
            f"[{platform}] Accroche : {v.get('hook', '')}",
            f"[{platform}] CTA : {v.get('cta', '')}",
        ])
    if not variants:
        parts.extend([
            "Caption : " + str(c.get("caption", "")),
            "Hashtags : " + ", ".join(c.get("hashtags", []) or []),
            "Accroche : " + str(c.get("hook", "")),
            "CTA : " + str(c.get("cta", "")),
        ])
    text = "\n".join(filter(None, parts))
    raw = chat_completion(
        [{"role": "user", "content": _COMPLIANCE_PROMPTS.get(language, _COMPLIANCE_PROMPTS["en"]) + text}],
        temperature=0.1,
    )["content"]
    verdict = _parse_verdict(raw, language)
    # On-image text verification (vision) when the prompt asked for specific text.
    try:
        media_risks = _check_media_text(db, post)
    except Exception:  # noqa: BLE001 — a non-vision model must not block compliance
        media_risks = []
    if media_risks:
        risks = list(verdict.get("risks") or []) + media_risks
        verdict["risks"] = risks
        if verdict.get("verdict") == "pass":
            verdict["verdict"] = "risk"  # on-image text issues = minor risk to review
    post.compliance = verdict
    post.status = "failed" if verdict.get("verdict") == "fail" else "compliance_ok"
    db.commit()
    db.refresh(post)
    return post


def publish_publication(db: Session, pub: Publication) -> Publication:
    """Publish one publication through the platform adapter (idempotent).

    The adapter MUST honour `pub.idempotency_key` so a retry of the same
    publication returns the same remote id instead of double-posting.
    """
    from app.models import ConnectedAccount
    from app.platforms import get_adapter

    adapter = get_adapter(pub.platform)
    account = db.get(ConnectedAccount, pub.account_id) if pub.account_id else None
    assert_publishable(db, pub)          # Article 50(2): no unmarked generated asset leaves here
    # After the local refusals, before the network call: a dependency with an open breaker is not called at
    # all, and the trial is not spent on a row that would have been refused anyway.
    if not breaker.allow(pub.platform):
        raise breaker.DependencyHalted(pub.platform, breaker.breaker_for(pub.platform).state())
    try:
        remote_id, url = adapter.publish(pub, account)
    except Exception:
        # `Exception`, not `BaseException`: a KeyboardInterrupt, a SystemExit or an asyncio cancellation is an
        # event on THIS side of the call, not a fact about the dependency, and recording it on the same counter
        # as a refused connection halts a platform that did nothing wrong. A cancellation therefore leaves the
        # trial unrecorded, which is why `allow()` hands out a new one after a cooldown window rather than
        # waiting for a report that will never come.
        breaker.record(pub.platform, False)
        raise
    breaker.record(pub.platform, True)
    pub.status = "published"
    pub.remote_post_id = remote_id
    pub.remote_url = url
    pub.published_at = utcnow()
    db.commit()
    db.refresh(pub)
    return pub


STALE_PUBLISHING_MINUTES = 5
MAX_PUBLISH_ATTEMPTS = 3


def _close_post(db: Session, post_id) -> None:
    """Derive the post's terminal status from its publications."""
    post = db.get(Post, post_id)
    if post is None:
        return
    pubs = db.scalars(select(Publication).where(Publication.post_id == post_id)).all()
    if not pubs:
        return
    if all(p.status == "published" for p in pubs):
        post.status = "published"
    elif any(p.status == "failed" for p in pubs):
        post.status = "failed"
    # else: still scheduled/publishing — leave the post's status as-is
    db.commit()


def publish_due() -> dict:
    """Publish every scheduled publication whose time has come.

    Idempotency chain: the platform adapter dedups by `idempotency_key`, and we
    claim each row (`scheduled` → `publishing`) before the network call so a
    concurrent loop or crash can't double-send. Returns {"published", "failed"}.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        now = utcnow()

        # Recover rows stuck in "publishing" by a crash mid-send. Safe to retry:
        # the platform idempotency key guarantees no double post.
        stale = db.scalars(
            select(Publication).where(
                Publication.status == "publishing",
                Publication.updated_at < now - datetime.timedelta(minutes=STALE_PUBLISHING_MINUTES),
            )
        ).all()
        for p in stale:
            p.status = "scheduled"
        if stale:
            db.commit()

        pubs = db.scalars(
            select(Publication).where(
                Publication.status == "scheduled",
                Publication.scheduled_at.is_not(None),
                Publication.scheduled_at <= now,
            )
        ).all()

        published = 0
        failed = 0
        halted: set[str] = set()
        post_ids: set = set()
        for p in pubs:
            p.status = "publishing"  # claim before the network call
            db.commit()
            try:
                publish_publication(db, p)
                published += 1
            except breaker.DependencyHalted as halt:
                # A halt is not this row's failure: the dependency is known down, so the attempt is not
                # consumed and the row goes back to waiting for the breaker's next trial. Counting it would
                # burn the three attempts against a service nobody has proved is back.
                p.status = "scheduled"
                db.commit()
                halted.add(halt.name)
            except Exception as e:  # noqa: BLE001 — per-row failure handling
                p.attempt_count = (p.attempt_count or 0) + 1
                p.last_error = str(e)[:500]
                p.status = "scheduled" if p.attempt_count < MAX_PUBLISH_ATTEMPTS else "failed"
                db.commit()
                failed += 1
                logger.warning(
                    "publish failed for %s (attempt %s): %s", p.id, p.attempt_count, e
                )
            post_ids.add(p.post_id)

        for pid in post_ids:
            _close_post(db, pid)
        for name in sorted(halted):
            logger.warning("publish deferred: %s is halted — %s", name, breaker.breaker_for(name).state())
        return {"published": published, "failed": failed, "halted": len(halted)}
    finally:
        db.close()

