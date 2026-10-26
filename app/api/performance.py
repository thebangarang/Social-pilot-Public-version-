"""Performance analytics endpoints (unified metrics model)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.deps import get_current_operator, get_db
from app.models import Operator
from app.schemas import BestTimeOut, InsightsOut, PerformanceOverviewOut, PostDetailOut
from app.services import metrics, report

router = APIRouter(prefix="/performance", tags=["performance"])


@router.get("/overview", response_model=PerformanceOverviewOut)
def overview(
    platform: str | None = None,
    brand_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """Aggregated KPIs, per-post latest metrics, and a trend series."""
    return metrics.overview(db, operator, platform, brand_id)


@router.get("/posts/{publication_id}", response_model=PostDetailOut)
def post_detail(
    publication_id: uuid.UUID,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """One post's latest metrics + its own time series."""
    detail = metrics.post_detail(db, operator, publication_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Publication introuvable")
    return detail


@router.get("/insights", response_model=InsightsOut)
def insights(
    platform: str | None = None,
    brand_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """Derived analytics: leaderboard, winning format, anomalies."""
    return metrics.insights(db, operator, platform, brand_id)


@router.get("/best-time", response_model=BestTimeOut)
def best_time(
    platform: str | None = None,
    brand_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """Best-time-to-post grid (day × hour) scored against the user's own median."""
    return metrics.best_time(db, operator, platform, brand_id)


@router.get("/report")
def report_pdf(
    platform: str | None = None,
    brand_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    operator: Operator = Depends(get_current_operator),
):
    """Export the performance summary as a client-facing PDF report."""
    pdf = report.build_performance_pdf(db, operator, platform, brand_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="social-pilot-performance.pdf"'},
    )
