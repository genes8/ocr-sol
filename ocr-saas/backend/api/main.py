"""OCR SaaS API - FastAPI Main Application."""

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from api.core.config import settings
from api.core.database import close_db, init_db
from api.core.metrics import app_info, http_request_duration_seconds
from api.core.redis import close_redis, get_redis
from api.core.storage import ensure_buckets
from api.routes import auth, documents, health, suppliers, webhooks


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan events."""
    # Startup
    # Fail fast if production secrets are defaults
    settings.validate_production_secrets()
    app_info.info({"version": settings.APP_VERSION, "environment": settings.ENVIRONMENT})

    # create_all() is only safe in development/staging.
    # In production, run: alembic upgrade head
    if settings.ENVIRONMENT in ("development", "staging"):
        await init_db()
    # Else: rely on Alembic migrations applied before deploy

    await get_redis()  # Initialize Redis connection
    await ensure_buckets()  # Ensure MinIO buckets exist

    yield

    # Shutdown
    await close_db()
    await close_redis()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="OCR/LLM SaaS Platform API for Document Processing",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.CORS_ALLOW_METHODS,
    allow_headers=settings.CORS_ALLOW_HEADERS,
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle uncaught exceptions."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


# Include routers
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(
    documents.router, prefix="/api/v1/documents", tags=["Documents"]
)
app.include_router(webhooks.router, prefix="/api/v1/webhooks", tags=["Webhooks"])
app.include_router(suppliers.router, prefix="/api/v1/suppliers", tags=["Suppliers"])


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    # Normalise path: strip UUIDs to avoid high cardinality
    path = request.url.path
    for segment in path.split("/"):
        if len(segment) == 36 and segment.count("-") == 4:
            path = path.replace(segment, "{id}")
    http_request_duration_seconds.labels(
        method=request.method,
        path=path,
        status_code=str(response.status_code),
    ).observe(duration)
    return response


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
    }
