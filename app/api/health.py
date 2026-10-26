"""Liveness endpoint (bare — no info disclosure like operator count / db kind)."""
from fastapi import APIRouter

from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok", version="0.1.0")
