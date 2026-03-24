"""Health check API routes."""

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel
from sqlalchemy import text

from api.core.config import settings
from api.core.database import get_db_session
from api.core.redis import get_redis

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    timestamp: datetime
    services: dict[str, str] = {}


@router.get("", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def health_check() -> HealthResponse:
    """Check the health of all dependent services."""
    services: dict[str, str] = {}

    # Database check
    try:
        session = await get_db_session()
        try:
            await session.execute(text("SELECT 1"))
            services["database"] = "ok"
        finally:
            await session.close()
    except Exception as exc:
        services["database"] = f"error: {exc}"

    # Redis check
    try:
        redis = await get_redis()
        await redis.ping()
        services["redis"] = "ok"
    except Exception as exc:
        services["redis"] = f"error: {exc}"

    # Update review queue depth gauge
    try:
        from sqlalchemy import select, func
        from api.models.db import Document, DocumentStatus
        from api.core.metrics import review_queue_depth

        session = await get_db_session()
        try:
            result = await session.execute(
                select(func.count()).select_from(Document).where(
                    Document.status.in_([DocumentStatus.REVIEW, DocumentStatus.MANUAL_REVIEW])
                )
            )
            depth = result.scalar() or 0
            review_queue_depth.set(depth)
            services["review_queue_depth"] = str(depth)
        finally:
            await session.close()
    except Exception:
        pass

    overall = "healthy" if all(v == "ok" for v in services.values() if not v.startswith("error")) else "degraded"

    return HealthResponse(
        status=overall,
        version=settings.APP_VERSION,
        timestamp=datetime.utcnow(),
        services=services,
    )


@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check() -> dict[str, str]:
    """Check if the service is ready to accept traffic."""
    return {"status": "ready"}


@router.get("/live", status_code=status.HTTP_200_OK)
async def liveness_check() -> dict[str, str]:
    """Check if the service is alive."""
    return {"status": "alive"}
