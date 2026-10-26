"""Metrics service — poll platform insights, snapshot, and derived analytics."""
import logging
import uuid
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import utcnow
from app.models import MetricSnapshot, Post, Publication

logger = logging.getLogger("social_pilot.metrics")

METRIC_KEYS = ("impressions", "reach", "views", "likes", "comments", "shares", "saves")
ENGAGEMENT_KEYS = ("likes", "comments", "shares", "saves")


def _audience(m: dict) -> float | None:
    """First positive audience metric (impressions → reach → views), else None."""
    for k in ("impressions", "reach", "views"):
        v = m.get(k)
        if v is not None and v > 0:
            return float(v)
    return None


def engagement_rate_of(m: dict) -> float | None:
    """Engagement rate with a platform-aware denominator, or None when unknown.

    Denominator fallback: impressions → reach → views. Platforms that expose no
    audience metric (e.g. Bluesky) yield None → the UI shows "—", never a fake 0.
    """
    engagements = sum(float(m.get(k) or 0) for k in ENGAGEMENT_KEYS)
    audience = _audience(m)
    if audience is None:
        return None
    return round(engagements / audience, 4)


def snapshot_publication(db: Session, pub: Publication, metrics: dict) -> int:
    """Store one row per metric for a publication (same capture timestamp)."""
    now = utcnow()
    n = 0
    for key, val in metrics.items():
        if val is None:
            continue
        db.add(MetricSnapshot(
            publication_id=pub.id,
            platform=pub.platform,
            metric_key=key,
            value=float(val),
            captured_at=now,
        ))
        n += 1
    db.commit()
    return n


def poll_insights() -> dict:
    """Poll insights for every published publication and store snapshots."""
    from app.database import SessionLocal
    from app.models import ConnectedAccount
    from app.platforms import get_adapter

    db = SessionLocal()
    try:
        pubs = db.scalars(select(Publication).where(Publication.status == "published")).all()
        polled = 0
        snapshots = 0
        for pub in pubs:
            fetch = getattr(get_adapter(pub.platform), "insights", None)
            if fetch is None:
                continue
            account = db.get(ConnectedAccount, pub.account_id) if pub.account_id else None
            try:
                metrics = fetch(pub, account)
            except Exception as e:  # noqa: BLE001 — one bad poll must not kill the loop
                logger.warning("insights failed for %s: %s", pub.id, e)
                continue
            if metrics:
                snapshots += snapshot_publication(db, pub, metrics)
                polled += 1
        return {"polled": polled, "snapshots": snapshots}
    finally:
        db.close()


def overview(db: Session, operator, platform: str | None = None, brand_id: uuid.UUID | None = None) -> dict:
    """Aggregated KPIs + per-post latest metrics + a trend series for the dashboard."""
    q = (
        select(Publication)
        .join(Post, Publication.post_id == Post.id)
        .where(Publication.status == "published")
    )
    if platform:
        q = q.where(Publication.platform == platform)
    if brand_id is not None:
        q = q.where(Post.brand_id == brand_id)
    pubs = db.scalars(q.order_by(Publication.published_at.desc())).all()
    pub_ids = [p.id for p in pubs]

    snaps = []
    if pub_ids:
        snaps = db.scalars(
            select(MetricSnapshot)
            .where(MetricSnapshot.publication_id.in_(pub_ids))
            .order_by(MetricSnapshot.captured_at)
        ).all()

    latest: dict = {}  # pub_id -> {metric_key: value}
    for s in snaps:
        latest.setdefault(s.publication_id, {})[s.metric_key] = s.value

    totals = {k: 0.0 for k in METRIC_KEYS}
    available: set[str] = set()
    posts = []
    for p in pubs:
        m = latest.get(p.id, {})
        for k in METRIC_KEYS:
            if k in m:
                available.add(k)
                totals[k] += m.get(k, 0.0)
        posts.append({
            "publication_id": p.id,
            "post_id": p.post_id,
            "platform": p.platform,
            "caption": p.caption,
            "published_at": p.published_at,
            "remote_url": p.remote_url,
            "metrics": {k: int(v) for k, v in m.items()},
            "engagement_rate": engagement_rate_of(m),
        })

    totals_out = {k: (int(totals[k]) if k in available else None) for k in METRIC_KEYS}
    engagement_rate = engagement_rate_of(totals)

    # Trend: total views/likes/comments summed per capture bucket (chronological).
    by_ts = defaultdict(lambda: {"views": 0.0, "likes": 0.0, "comments": 0.0})
    for s in snaps:
        if s.metric_key in ("views", "likes", "comments"):
            ts = s.captured_at.strftime("%m-%d %H:%M") if s.captured_at else ""
            by_ts[ts][s.metric_key] += s.value
    trend = [
        {"t": ts, "views": int(v["views"]), "likes": int(v["likes"]), "comments": int(v["comments"])}
        for ts, v in sorted(by_ts.items())
    ]

    return {
        "totals": totals_out,
        "engagement_rate": engagement_rate,
        "post_count": len(posts),
        "posts": posts,
        "trend": trend,
    }


def post_detail(db: Session, operator, publication_id) -> dict | None:
    """Per-post detail: latest metrics + the post's own time series."""
    pub = db.get(Publication, publication_id)
    if pub is None:
        return None
    post = db.get(Post, pub.post_id)
    if post is None or post.owner_id != operator.id:
        return None

    snaps = db.scalars(
        select(MetricSnapshot)
        .where(MetricSnapshot.publication_id == publication_id)
        .order_by(MetricSnapshot.captured_at)
    ).all()

    latest: dict = {}
    by_ts = defaultdict(lambda: {})
    for s in snaps:
        latest[s.metric_key] = s.value
        ts = s.captured_at.strftime("%m-%d %H:%M") if s.captured_at else ""
        by_ts[ts][s.metric_key] = s.value
    series = [
        {"t": ts, **{k: int(v) for k, v in vals.items()}}
        for ts, vals in sorted(by_ts.items())
    ]

    return {
        "publication_id": pub.id,
        "platform": pub.platform,
        "caption": pub.caption,
        "published_at": pub.published_at,
        "remote_url": pub.remote_url,
        "metrics": {k: int(v) for k, v in latest.items()},
        "series": series,
    }


def insights(db: Session, operator, platform: str | None = None, brand_id: uuid.UUID | None = None) -> dict:
    """Derived analytics: leaderboard, winning format, anomalies vs the median."""
    q = (
        select(Publication)
        .join(Post, Publication.post_id == Post.id)
        .where(Publication.status == "published")
    )
    if platform:
        q = q.where(Publication.platform == platform)
    if brand_id is not None:
        q = q.where(Post.brand_id == brand_id)
    pubs = db.scalars(q.order_by(Publication.published_at.desc())).all()
    pub_ids = [p.id for p in pubs]
    post_ids = [p.post_id for p in pubs]

    media_by_post: dict = {}
    if post_ids:
        for post in db.scalars(select(Post).where(Post.id.in_(post_ids))).all():
            media_by_post[post.id] = post.media_type

    snaps = []
    if pub_ids:
        snaps = db.scalars(
            select(MetricSnapshot).where(MetricSnapshot.publication_id.in_(pub_ids))
        ).all()

    latest: dict = {}
    for s in snaps:
        latest.setdefault(s.publication_id, {})[s.metric_key] = s.value

    stats = []
    for p in pubs:
        m = latest.get(p.id, {})
        stats.append({
            "publication_id": p.id,
            "platform": p.platform,
            "caption": p.caption,
            "published_at": p.published_at,
            "media_type": media_by_post.get(p.post_id),
            "views": int(m.get("views", 0)),
            "engagement_rate": engagement_rate_of(m),
        })

    # leaderboard: top 5 by engagement rate (posts without a rate sort last)
    def _rank(s):
        r = s["engagement_rate"]
        return r if r is not None else -1.0

    leaderboard = sorted(stats, key=lambda x: -_rank(x))[:5]

    # winning format: image / video / text / auto, avg over posts with a rate
    by_fmt = defaultdict(lambda: {"count": 0, "rate_sum": 0.0, "rate_n": 0})
    for s in stats:
        fmt = s["media_type"] or "auto"
        by_fmt[fmt]["count"] += 1
        if s["engagement_rate"] is not None:
            by_fmt[fmt]["rate_sum"] += s["engagement_rate"]
            by_fmt[fmt]["rate_n"] += 1
    formats = [
        {
            "format": k,
            "count": v["count"],
            "avg_engagement_rate": round(v["rate_sum"] / v["rate_n"], 4) if v["rate_n"] else None,
        }
        for k, v in sorted(
            by_fmt.items(),
            key=lambda kv: -(kv[1]["rate_sum"] / kv[1]["rate_n"] if kv[1]["rate_n"] else 0.0),
        )
    ]

    # anomalies: ≥2× median (viral) or ≤0.5× median (flop), only among posts with a rate
    rates = sorted(
        s["engagement_rate"]
        for s in stats
        if s["engagement_rate"] is not None and s["engagement_rate"] > 0
    )
    median = rates[len(rates) // 2] if rates else 0.0
    anomalies = []
    if median > 0:
        for s in stats:
            r = s["engagement_rate"]
            if r is None:
                continue
            if r >= 2 * median:
                anomalies.append({**s, "kind": "high"})
            elif r <= 0.5 * median:
                anomalies.append({**s, "kind": "low"})

    return {
        "leaderboard": leaderboard,
        "formats": formats,
        "anomalies": anomalies,
        "median_engagement_rate": round(median, 4) if median > 0 else None,
    }


def best_time(db: Session, operator, platform: str | None = None, brand_id: uuid.UUID | None = None) -> dict:
    """Best-time-to-post grid: day × hour scored against the post median (180 j)."""
    q = (
        select(Publication)
        .join(Post, Publication.post_id == Post.id)
        .where(
            Publication.status == "published",
            Publication.published_at.is_not(None),
            Publication.published_at >= utcnow() - timedelta(days=180),
        )
    )
    if platform:
        q = q.where(Publication.platform == platform)
    if brand_id is not None:
        q = q.where(Post.brand_id == brand_id)
    pubs = db.scalars(q).all()
    pub_ids = [p.id for p in pubs]

    snaps = []
    if pub_ids:
        snaps = db.scalars(
            select(MetricSnapshot).where(MetricSnapshot.publication_id.in_(pub_ids))
        ).all()

    latest: dict = {}
    for s in snaps:
        latest.setdefault(s.publication_id, {})[s.metric_key] = s.value

    cells = defaultdict(lambda: {"count": 0, "rate_sum": 0.0, "rate_n": 0})
    rates = []
    for p in pubs:
        m = latest.get(p.id, {})
        rate = engagement_rate_of(m)
        if rate is not None:
            rates.append(rate)
        dt = p.published_at
        if dt is None:
            continue
        cells[(dt.weekday(), dt.hour)]["count"] += 1
        if rate is not None:
            cells[(dt.weekday(), dt.hour)]["rate_sum"] += rate
            cells[(dt.weekday(), dt.hour)]["rate_n"] += 1

    median = sorted(rates)[len(rates) // 2] if rates else 0.0

    grid = []
    for wd in range(7):
        for hr in range(24):
            c = cells.get((wd, hr))
            if c and c["count"]:
                avg = c["rate_sum"] / c["rate_n"] if c["rate_n"] else None
                score = round(avg / median, 3) if (avg is not None and median > 0) else None
                grid.append({
                    "day": wd, "hour": hr, "count": c["count"],
                    "avg_rate": round(avg, 4) if avg is not None else None, "score": score,
                })
            else:
                grid.append({"day": wd, "hour": hr, "count": 0, "avg_rate": None, "score": None})

    top_slots = sorted(
        (g for g in grid if g["score"] is not None),
        key=lambda x: -(x["score"] or 0),
    )[:5]

    return {
        "median_engagement_rate": round(median, 4) if median > 0 else None,
        "top_slots": top_slots,
        "grid": grid,
    }
