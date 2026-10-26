"""Editorial calendar — scheduled + published publications, month grid."""
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_current_operator, get_db
from app.models import MediaItem, Operator, Post, Publication
from app.schemas import CalendarEntryOut

router = APIRouter(prefix="/calendar", tags=["calendar"])


def _first_media(db: Session, media_ids: list) -> dict | None:
    """Resolve the first media item (for a chip thumbnail): {id, kind}."""
    if not media_ids:
        return None
    try:
        ids = [uuid.UUID(str(x)) for x in media_ids]
    except (ValueError, TypeError):
        return None
    m = db.scalars(select(MediaItem).where(MediaItem.id.in_(ids)).limit(1)).first()
    return {"id": str(m.id), "kind": m.kind} if m else None


@router.get("", response_model=list[CalendarEntryOut])
def calendar_entries(
    start: date,
    end: date,
    brand_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """Scheduled + published publications within [start, end] (inclusive)."""
    if start > end:
        start, end = end, start

    q = (
        select(Publication)
        .join(Post, Publication.post_id == Post.id)
        .where(
            Publication.status.in_(("scheduled", "published")),
        )
    )
    if brand_id is not None:
        q = q.where(Post.brand_id == brand_id)
    pubs = db.scalars(q).all()

    entries = []
    for p in pubs:
        dt = p.scheduled_at if p.status == "scheduled" else p.published_at
        if dt is None:
            continue
        d = dt.date() if isinstance(dt, datetime) else dt
        if not (start <= d <= end):
            continue
        entries.append(
            CalendarEntryOut(
                publication_id=p.id,
                post_id=p.post_id,
                platform=p.platform,
                caption=p.caption or "",
                status=p.status,
                date=d.isoformat(),
                time=dt.strftime("%H:%M"),
                scheduled_at=p.scheduled_at,
                published_at=p.published_at,
                remote_url=p.remote_url,
                media=_first_media(db, p.media_ids),
            )
        )

    return sorted(entries, key=lambda e: (e.date, e.time or ""))
