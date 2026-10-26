"""Post production pipeline API — media generation, compliance, publish."""
import logging
import uuid
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_current_operator, get_db, require_publish
from app.models import ConnectedAccount, Operator, Post, Publication
from app.platforms import get_adapter
from app.schemas import PostOut, PublicationCreateIn, PublicationOut, PublishIn, RegenerateMediaIn
from app.services import pipeline

logger = logging.getLogger("social_pilot.posts")

router = APIRouter(prefix="/posts", tags=["posts"])


def _get_post(db: Session, post_id: uuid.UUID, operator: Operator) -> Post:
    # Single-tenant team: any member can open any post (owner_id is just the creator).
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Publication introuvable")
    return post


def _default_account(db: Session, platform: str, brand_id) -> ConnectedAccount | None:
    """The active connected account for a platform (brand-scoped, default first)."""
    q = select(ConnectedAccount).where(
        ConnectedAccount.platform == platform,
        ConnectedAccount.is_active.is_(True),
    )
    if brand_id:
        q = q.where(ConnectedAccount.brand_id == brand_id)
    else:
        q = q.where(ConnectedAccount.brand_id.is_(None))
    return db.scalar(q.order_by(ConnectedAccount.is_default.desc(), ConnectedAccount.created_at))


_MEDIA_REQUIRED_PLATFORMS = {"tiktok", "instagram"}


def _naive_utc(dt):
    """Normalize a (possibly tz-aware) datetime to naive UTC for SQLite."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@router.get("", response_model=list[PostOut])
def list_posts(db: Session = Depends(get_db), operator: Operator = Depends(get_current_operator)):
    return db.scalars(select(Post).order_by(Post.created_at.desc())).all()


@router.get("/{post_id}", response_model=PostOut)
def get_post(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    return _get_post(db, post_id, operator)


@router.post("/{post_id}/generate-media", response_model=PostOut)
def generate_media(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    post = _get_post(db, post_id, operator)
    if post.status != "concept_validated":
        raise HTTPException(status_code=400, detail=f"Action invalide (statut : {post.status}).")
    if post.media_type == "text":
        raise HTTPException(status_code=400, detail="Post en texte seul — aucun média à générer.")
    try:
        return pipeline.generate_media(db, post, operator)
    except Exception as e:  # noqa: BLE001 — clean error to the user
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{post_id}/regenerate-media", response_model=PostOut)
def regenerate_media(
    post_id: uuid.UUID,
    payload: RegenerateMediaIn,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """Re-adjust the generated media after the fact (feedback → regenerate)."""
    post = _get_post(db, post_id, operator)
    if post.status not in ("media_ready", "compliance_ok"):
        raise HTTPException(status_code=400, detail=f"Action invalide (statut : {post.status}).")
    try:
        return pipeline.generate_media(db, post, operator, feedback=payload.feedback)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{post_id}/compliance", response_model=PostOut)
def compliance_scan(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    post = _get_post(db, post_id, operator)
    if post.status not in ("media_ready", "concept_validated"):
        raise HTTPException(status_code=400, detail=f"Action invalide (statut : {post.status}).")
    if post.status == "concept_validated" and post.media_type != "text":
        raise HTTPException(status_code=400, detail="Générez le média d'abord.")
    try:
        return pipeline.scan_compliance(db, post, operator.language)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{post_id}/publications", response_model=list[PublicationOut])
def create_publications(
    post_id: uuid.UUID,
    payload: PublicationCreateIn,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    post = _get_post(db, post_id, operator)
    if post.status != "compliance_ok":
        raise HTTPException(status_code=400, detail="Générez le média et scannez la conformité d'abord.")
    # Re-selection: drop existing draft publications for this post.
    for p in db.scalars(select(Publication).where(Publication.post_id == post.id, Publication.status == "draft")).all():
        db.delete(p)
    db.commit()

    pubs = []
    for platform in payload.platforms:
        if platform in _MEDIA_REQUIRED_PLATFORMS and not post.media_ids:
            raise HTTPException(
                status_code=400,
                detail=f"{platform} requiert une image ou une vidéo (post en mode texte). "
                       "Générez le média, ou choisissez un réseau qui accepte le texte.",
            )
        account = _default_account(db, platform, post.brand_id)
        variant_media = (post.media_variants or {}).get(platform)
        pub = Publication(
            post_id=post.id,
            platform=platform,
            account_id=account.id if account else None,
            caption=str(pipeline.variant_for(post.content, platform).get("caption", "")),
            media_ids=[variant_media] if variant_media else list(post.media_ids or []),
            status="draft",
        )
        db.add(pub)
        pubs.append(pub)
    db.commit()
    for p in pubs:
        db.refresh(p)
    return pubs


@router.get("/{post_id}/publications", response_model=list[PublicationOut])
def list_publications(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    _get_post(db, post_id, operator)
    return db.scalars(
        select(Publication).where(Publication.post_id == post_id).order_by(Publication.created_at)
    ).all()


@router.delete("/{post_id}/publications/{publication_id}", status_code=200)
def delete_publication(
    post_id: uuid.UUID,
    publication_id: uuid.UUID,
    confirm: str,
    db: Session = Depends(get_db),
    operator: Operator = Depends(require_publish),
):
    """Delete one publication — locally AND on the platform.

    Destructive + missclick-guarded: `confirm=SUPPRIMER` (exact) or refused.
    If the publication was published and its account is still connected, the
    platform post is deleted first (best-effort); a platform failure is reported
    in the response but the local record is still removed. Deleting the last
    publication of a post also drops the now-empty post.
    """
    post = _get_post(db, post_id, operator)
    pub = db.get(Publication, publication_id)
    if pub is None or pub.post_id != post.id:
        raise HTTPException(status_code=404, detail="Publication introuvable")
    if confirm.strip() not in ("SUPPRIMER", "DELETE"):
        raise HTTPException(status_code=400, detail="Confirmation invalide — tapez « SUPPRIMER » (ou « DELETE ») pour supprimer.")

    # Delete on the platform first (best-effort), then locally.
    platform_deleted = True
    platform_error = None
    if pub.remote_post_id and pub.account_id:
        account = db.get(ConnectedAccount, pub.account_id)
        adapter = get_adapter(pub.platform)
        try:
            adapter.delete(pub, account)
        except Exception as e:  # noqa: BLE001 — best-effort; local delete still proceeds
            platform_deleted = False
            platform_error = str(e)[:200]
            logger.warning("platform delete failed for %s %s: %s", pub.platform, pub.id, e)

    db.delete(pub)
    db.flush()
    if not db.scalars(select(Publication).where(Publication.post_id == post.id)).first():
        db.delete(post)
    db.commit()
    return {"platform_deleted": platform_deleted, "platform_error": platform_error}


@router.post("/{post_id}/approve", response_model=PostOut)
def approve_post(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    post = _get_post(db, post_id, operator)
    if post.status != "compliance_ok":
        raise HTTPException(status_code=400, detail=f"Action invalide (statut : {post.status}).")
    pubs = db.scalars(select(Publication).where(Publication.post_id == post.id)).all()
    if not pubs:
        raise HTTPException(status_code=400, detail="Sélectionnez d'abord les réseaux.")
    post.status = "approved"
    db.commit()
    db.refresh(post)
    return post


@router.post("/{post_id}/step-back", response_model=PostOut)
def step_back(
    post_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """Move the post back one pipeline step so a detail can be corrected."""
    post = _get_post(db, post_id, operator)
    if post.status == "approved":
        post.status = "compliance_ok"
    elif post.status == "compliance_ok":
        pubs = db.scalars(select(Publication).where(Publication.post_id == post.id)).all()
        drafts = [p for p in pubs if p.status == "draft"]
        if drafts:
            # undo the network selection (re-select networks)
            for p in drafts:
                db.delete(p)
            # stay compliance_ok → the network picker reappears
        else:
            post.compliance = None
            post.status = "concept_validated" if post.media_type == "text" else "media_ready"
    elif post.status == "media_ready":
        post.status = "concept_validated"
    else:
        raise HTTPException(status_code=400, detail=f"Impossible de revenir en arrière (statut : {post.status}).")
    db.commit()
    db.refresh(post)
    return post


@router.post("/{post_id}/publish", response_model=list[PublicationOut])
def publish_post(
    post_id: uuid.UUID,
    payload: PublishIn,
    db: Session = Depends(get_db),
    operator: Operator = Depends(require_publish),
):
    post = _get_post(db, post_id, operator)
    if post.status != "approved":
        raise HTTPException(status_code=400, detail="Le post doit être approuvé avant publication.")
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Confirmation explicite requise (double approbation).")
    pubs = db.scalars(select(Publication).where(Publication.post_id == post.id)).all()
    if not pubs:
        raise HTTPException(status_code=400, detail="Aucune publication à envoyer.")

    if payload.caption is not None:
        for p in pubs:
            p.caption = payload.caption

    if payload.scheduled_at:
        scheduled = _naive_utc(payload.scheduled_at)
        for p in pubs:
            p.status = "scheduled"
            p.scheduled_at = scheduled
        post.status = "scheduled"
        db.commit()
        results = pubs
    else:
        results = []
        for p in pubs:
            try:
                results.append(pipeline.publish_publication(db, p))
            except RuntimeError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        post.status = "published"
        db.commit()
    for p in results:
        db.refresh(p)
    db.refresh(post)
    return results
