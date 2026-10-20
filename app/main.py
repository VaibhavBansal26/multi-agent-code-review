"""FastAPI application entry point."""
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, status
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
