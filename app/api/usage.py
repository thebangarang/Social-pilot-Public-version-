"""Usage / budget endpoints (cost tracking for LLM + media generation)."""
import csv
import datetime
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import utcnow
from app.deps import get_current_operator, get_db
from app.models import LLMSetting, Operator, UsageRecord
from app.schemas import UsageRecordOut, UsageSummaryOut

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=list[UsageRecordOut])
def list_usage(
    limit: int = 50,
    db: Session = Depends(get_db),
    _: Operator = Depends(get_current_operator),
):
    n = max(1, min(limit, 200))
    return db.scalars(select(UsageRecord).order_by(UsageRecord.created_at.desc()).limit(n)).all()


@router.get("/summary", response_model=UsageSummaryOut)
def summary(db: Session = Depends(get_db), _: Operator = Depends(get_current_operator)):
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    records = db.scalars(
        select(UsageRecord).where(UsageRecord.created_at >= month_start)
    ).all()
    llm_cost = sum(r.cost for r in records if r.kind == "llm")
    media_cost = sum(r.cost for r in records if r.kind == "media")
    total = llm_cost + media_cost
    total_all_time = sum(r.cost for r in db.scalars(select(UsageRecord)).all())
    setting = db.scalar(select(LLMSetting).limit(1))
    limit_val = setting.monthly_limit if setting else None
    remaining = (limit_val - total) if limit_val is not None else None
    return UsageSummaryOut(
        month=month_start.strftime("%Y-%m"),
        provider=(setting.provider if setting else "none"),
        total_cost=round(total, 4),
        llm_cost=round(llm_cost, 4),
        media_cost=round(media_cost, 4),
        total_all_time=round(total_all_time, 4),
        monthly_limit=limit_val,
        remaining=round(remaining, 4) if remaining is not None else None,
        topup_mode=(setting.topup_mode if setting else "manual"),
        record_count=len(records),
    )


@router.get("/export")
def export_usage(
    month: str | None = None,
    db: Session = Depends(get_db),
    _: Operator = Depends(get_current_operator),
):
    """Export the usage history as CSV, optionally filtered to one month (YYYY-MM)."""
    q = select(UsageRecord).order_by(UsageRecord.created_at)
    filename = "usage"
    if month:
        try:
            y, m = month.split("-")
            start = datetime.datetime(int(y), int(m), 1)
            if int(m) == 12:
                end = datetime.datetime(int(y) + 1, 1, 1)
            else:
                end = datetime.datetime(int(y), int(m) + 1, 1)
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Mois invalide (format YYYY-MM).")
        q = q.where(UsageRecord.created_at >= start, UsageRecord.created_at < end)
        filename = f"usage-{month}"

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["date", "type", "provider", "modele", "tokens", "cout_usd"])
    for r in db.scalars(q).all():
        w.writerow([
            r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
            r.kind,
            r.provider,
            r.model or "",
            r.tokens or "",
            f"{r.cost:.6f}".replace(".", ","),
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
