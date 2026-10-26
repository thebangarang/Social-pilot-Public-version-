"""Community manager endpoints — comment inbox, triage, replies."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import utcnow
from app.deps import get_current_operator, get_db
from app.models import Comment, Operator, Post, Publication
from app.schemas import (
    BatchGenerateIn,
    BatchReplyIn,
    CommentOut,
    ReplyIn,
)
from app.services import community

router = APIRouter(prefix="/community", tags=["community"])

logger = logging.getLogger("social_pilot.community")


def _get_comment(db: Session, comment_id: uuid.UUID, operator: Operator) -> Comment:
    c = db.scalar(
        select(Comment)
        .join(Publication, Comment.publication_id == Publication.id)
        .join(Post, Publication.post_id == Post.id)
        .where(Comment.id == comment_id)
    )
    if c is None:
        raise HTTPException(status_code=404, detail="Commentaire introuvable")
    return c


@router.get("/comments", response_model=list[CommentOut])
def list_comments(
    level: str | None = None,
    status: str | None = None,
    brand_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    q = (
        select(Comment)
        .join(Publication, Comment.publication_id == Publication.id)
        .join(Post, Publication.post_id == Post.id)
    )
    if level and level != "all":
        q = q.where(Comment.level == level)
    if status:
        q = q.where(Comment.status == status)
    if brand_id is not None:
        q = q.where(Post.brand_id == brand_id)
    return db.scalars(q.order_by(Comment.created_at.desc())).all()


@router.post("/poll")
def poll(_: Operator = Depends(get_current_operator)):
    """Fetch new comments from the platforms (mock) into the inbox."""
    return community.poll_comments()


@router.post("/comments/{comment_id}/generate-reply", response_model=CommentOut)
def generate_reply(
    comment_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    comment = _get_comment(db, comment_id, operator)
    try:
        community.generate_reply(db, comment)
    except Exception as e:  # noqa: BLE001 — clean error to the user
        raise HTTPException(status_code=502, detail=f"Génération échouée : {e}")
    return comment


@router.post("/comments/{comment_id}/reply", response_model=CommentOut)
def reply(
    comment_id: uuid.UUID,
    payload: ReplyIn,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    comment = _get_comment(db, comment_id, operator)
    final = (payload.text or "").strip() or (comment.reply_draft or "").strip()
    if not final:
        raise HTTPException(status_code=400, detail="Générez ou saisissez une réponse d'abord.")
    comment.reply_draft = final
    try:
        community.post_reply(db, comment)
    except Exception as e:  # noqa: BLE001 — surface a clean error, don't mark replied
        raise HTTPException(status_code=502, detail=f"Envoi de la réponse échoué : {e}")
    comment.status = "replied"
    comment.replied_at = utcnow()
    db.commit()
    db.refresh(comment)
    return comment


@router.post("/batch-generate", response_model=list[CommentOut])
def batch_generate(
    payload: BatchGenerateIn,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    out = []
    for cid in payload.comment_ids:
        c = _get_comment(db, cid, operator)
        if c.status == "replied" or c.reply_draft:
            out.append(c)
            continue
        try:
            community.generate_reply(db, c)
        except Exception as e:  # noqa: BLE001
            logger.warning("batch generate failed for %s: %s", cid, e)
        out.append(c)
    return out


@router.post("/batch-reply")
def batch_reply(
    payload: BatchReplyIn,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    sent = 0
    for item in payload.items:
        c = _get_comment(db, item.comment_id, operator)
        if c.status == "replied":
            continue
        final = (item.text or "").strip() or (c.reply_draft or "").strip()
        if not final:
            continue
        c.reply_draft = final
        try:
            community.post_reply(db, c)
        except Exception as e:  # noqa: BLE001 — skip the bad item, keep the batch going
            logger.warning("batch reply failed for %s: %s", item.comment_id, e)
            continue
        c.status = "replied"
        c.replied_at = utcnow()
        sent += 1
    db.commit()
    return {"sent": sent}
