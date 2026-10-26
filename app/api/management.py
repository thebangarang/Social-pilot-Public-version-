"""Instance management / telemetry API for the multi-client command center.

Auth: ``X-Management-Token`` header, compared in constant time against
``settings.management_token``. Returns ONLY non-personal telemetry (counters,
health, cost) — never client data, post/comment content, operator emails, or
media — so the command center stays RGPD-light: no personal data leaves the
client instance.

Disabled (404) until ``SP_MANAGEMENT_TOKEN`` is set in the instance's ``.env``.
"""
import hmac
import time
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import breaker
from app import usage_meter
from app.auth import utcnow
from app.build_info import build_info
from app.config import settings
from app.database import get_db
from app.models import Comment, ConnectedAccount, Operator, Post, Publication, UsageRecord

router = APIRouter(prefix="/api/v1/management", tags=["management"])

APP_VERSION = "0.1.0"
_START_TIME = time.time()

# Dimensions the ledger can be aggregated on. Rows always expose the same keys;
# the ones not selected by `group_by` come back as null.
_GROUP_KEYS = ("day", "provider", "model", "price_source")


def _require_token(x_management_token: str | None = Header(default=None)) -> None:
    if not settings.management_token:
        raise HTTPException(status_code=404, detail="Management API disabled")
    if x_management_token is None or not hmac.compare_digest(
        x_management_token.encode(), settings.management_token.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid management token")


def _instance_id() -> str:
    """Stable per-instance id derived from the instance secret (never exposed)."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, settings.secret_key))


@router.get("/health", dependencies=[Depends(_require_token)])
def health() -> dict:
    db_ok = True
    try:
        from app.database import SessionLocal

        s = SessionLocal()
        try:
            s.execute(select(1))
        finally:
            s.close()
    except Exception:  # noqa: BLE001
        db_ok = False
    return {
        "instance_id": _instance_id(),
        "label": settings.instance_label,
        "version": APP_VERSION,
        "build": build_info(),
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else "error",
        "uptime_seconds": int(time.time() - _START_TIME),
        "timestamp": utcnow().isoformat(),
    }


@router.get("/stats", dependencies=[Depends(_require_token)])
def stats(db: Session = Depends(get_db)) -> dict:
    def count(model) -> int:
        return db.scalar(select(func.count()).select_from(model)) or 0

    def count_where(model, *conds) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*conds)) or 0

    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    usage_month = db.scalar(
        select(func.coalesce(func.sum(UsageRecord.cost), 0.0)).where(
            UsageRecord.created_at >= month_start
        )
    ) or 0.0
    usage_all = db.scalar(select(func.coalesce(func.sum(UsageRecord.cost), 0.0))) or 0.0
    tokens_all = db.scalar(select(func.coalesce(func.sum(UsageRecord.tokens), 0))) or 0

    return {
        "instance_id": _instance_id(),
        "label": settings.instance_label,
        "operators": count(Operator),
        "connected_accounts": count(ConnectedAccount),
        "posts_total": count(Post),
        "publications_published": count_where(Publication, Publication.status == "published"),
        "publications_scheduled": count_where(Publication, Publication.status == "scheduled"),
        "comments_new": count_where(Comment, Comment.status == "new"),
        "comments_replied": count_where(Comment, Comment.status == "replied"),
        "usage_month_usd": round(usage_month, 4),
        "usage_all_time_usd": round(usage_all, 4),
        "tokens_all_time": int(tokens_all),
        "timestamp": utcnow().isoformat(),
    }


# --------------------------------------------------------------------------- #
# /usage — the billing-grade detail behind the "measured model by model, billed
# at real cost" promise. Aggregates only: no prompts, no outputs, no operator.
# --------------------------------------------------------------------------- #
def _parse_day(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Date invalide (format YYYY-MM-DD).")


def _parse_period(since: str | None, until: str | None) -> tuple[datetime, datetime]:
    """Resolve the window. Defaults to the current month; `until` is inclusive."""
    now = utcnow()
    start = _parse_day(since, now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    stop = _parse_day(until, now) + timedelta(days=1)
    return start, stop


def _parse_group_by(value: str) -> tuple[str, ...]:
    keys = tuple(k.strip() for k in (value or "").split(",") if k.strip())
    keys = keys or ("day", "model")
    unknown = [k for k in keys if k not in _GROUP_KEYS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"group_by inconnu : {', '.join(unknown)} (attendu : {', '.join(_GROUP_KEYS)})",
        )
    return keys


def _group_value(row: UsageRecord, key: str) -> str | None:
    if key == "day":
        return row.created_at.strftime("%Y-%m-%d") if row.created_at else None
    if key == "price_source":
        # Legacy rows predate the meter and carry no provenance.
        return row.price_source or "unknown"
    return getattr(row, key, None)


@router.get("/dependencies", dependencies=[Depends(_require_token)])
def dependencies() -> dict:
    """The circuit breakers, as this instance sees them right now (R-ENG-022).

    Telemetry, never personal data: a dependency's name and a state. It exists because a halted dependency is
    invisible otherwise — the rows stop publishing, the log says why once per run, and the dashboard the
    operator actually looks at would show a quiet queue instead of a platform that is refusing.
    """
    states = breaker.states()
    return {
        "instance_id": _instance_id(),
        "breakers": states,
        "halted": sorted(name for name, state in states.items() if state["state"] == "open"),
    }


@router.get("/usage", dependencies=[Depends(_require_token)])
def usage(
    since: str | None = None,
    until: str | None = None,
    group_by: str = Query(default="day,model"),
    db: Session = Depends(get_db),
) -> dict:
    """Aggregated consumption for a period, per group_by dimension.

    ``unpriced_rows`` counts rows that cannot be billed: price ``unknown``, or no
    provenance at all (rows written before the meter existed). A statement must
    not be generated for a period that contains any (see app/usage_meter.py).
    """
    start, stop = _parse_period(since, until)
    keys = _parse_group_by(group_by)

    rows = db.scalars(
        select(UsageRecord)
        .where(UsageRecord.created_at >= start, UsageRecord.created_at < stop)
        .order_by(UsageRecord.created_at)
    ).all()

    buckets: dict[tuple, dict] = {}
    for row in rows:
        grouping = tuple(_group_value(row, k) for k in keys)
        bucket = buckets.get(grouping)
        if bucket is None:
            bucket = {
                "day": None, "provider": None, "model": None, "price_source": None,
                "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0,
                "cost_usd": 0.0, "calls": 0, "currency": None, "_sources": set(),
                "_currencies": set(),
            }
            buckets[grouping] = bucket
        for key, value in zip(keys, grouping):
            bucket[key] = value
        bucket["tokens_in"] += row.tokens_in or 0
        bucket["tokens_out"] += row.tokens_out or 0
        bucket["tokens_cached"] += row.tokens_cached or 0
        bucket["cost_usd"] += row.cost or 0.0
        bucket["calls"] += 1
        bucket["_sources"].add(row.price_source or "unknown")
        bucket["_currencies"].add(row.currency or "USD")

    out = []
    for bucket in buckets.values():
        sources = bucket.pop("_sources")
        currencies = bucket.pop("_currencies")
        bucket["price_source"] = sources.pop() if len(sources) == 1 else "mixed"
        # Currency travels with the row: OVH prices in EUR, OpenRouter in USD, and a
        # bucket must never be summed across both. "mixed" is a loud anomaly.
        bucket["currency"] = currencies.pop() if len(currencies) == 1 else "mixed"
        bucket["cost_usd"] = round(bucket["cost_usd"], 8)
        # `cost_usd` is the historical wire name and is kept for compatibility, but the
        # value is expressed in the row's own currency (EUR from OVH, USD from OpenRouter).
        # `cost` is the honest name; the hub reads it first. Do not sum across rows
        # without checking their currency.
        bucket["cost"] = bucket["cost_usd"]
        out.append(bucket)
    out.sort(key=lambda b: tuple("" if b[k] is None else str(b[k]) for k in _GROUP_KEYS))

    currencies = {r.currency for r in rows if r.currency}
    return {
        "instance_id": _instance_id(),
        "label": settings.instance_label,
        "since": start.strftime("%Y-%m-%d"),
        "until": (stop - timedelta(days=1)).strftime("%Y-%m-%d"),
        "currency": currencies.pop() if len(currencies) == 1 else ("mixed" if currencies else "USD"),
        "total_cost_usd": round(sum(r.cost or 0.0 for r in rows), 6),
        "total_tokens": sum(
            (r.tokens_in or 0) + (r.tokens_out or 0) if (r.tokens_in or r.tokens_out) else (r.tokens or 0)
            for r in rows
        ),
        "calls": len(rows),
        "rows": out,
        "unpriced_rows": sum(1 for r in rows if r.price_source in usage_meter.UNPRICED),
    }
