"""FastAPI application entry point."""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Path, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.webhook.router import router as webhook_router

log = structlog.get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("macrv_starting", env=settings.app_env)
    yield
    log.info("macrv_stopping")


app = FastAPI(
    title="MACRV — Multi-Agent Code Review",
    description="Automated code review system powered by Claude agents",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

app.include_router(webhook_router)


@app.get("/health", status_code=status.HTTP_200_OK, tags=["health"])
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": "0.1.0", "env": settings.app_env})


@app.get("/", include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse({"service": "macrv", "docs": "/docs"})


@app.get("/reviews/{task_id}/progress", tags=["reviews"])
async def review_progress(
    task_id: str = Path(..., description="Celery task ID returned by the webhook"),
) -> JSONResponse:
    """Return the current progress/status of a PR review task."""
    from app.queue.celery_app import celery_app as _celery
    result = _celery.AsyncResult(task_id)
    state = result.state  # PENDING | STARTED | SUCCESS | FAILURE | RETRY

    if state == "PENDING":
        return JSONResponse({"task_id": task_id, "status": "pending", "progress_pct": 0})

    if state == "FAILURE":
        return JSONResponse(
            {"task_id": task_id, "status": "failed", "error": str(result.info)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if state == "SUCCESS":
        info = result.result or {}
        return JSONResponse({
            "task_id": task_id,
            "status": "completed",
            "progress_pct": 100,
            "decision": info.get("decision"),
            "total_findings": info.get("total_findings", 0),
            "critical": info.get("critical", 0),
            "suggestive": info.get("suggestive", 0),
            "optimisation": info.get("optimisation", 0),
        })

    # STARTED or RETRY — task stores progress in meta via update_state
    meta = result.info or {}
    return JSONResponse({
        "task_id": task_id,
        "status": "in_progress",
        "progress_pct": meta.get("progress_pct", 0),
        "reviewed": meta.get("reviewed", 0),
        "total": meta.get("total", 0),
    })
