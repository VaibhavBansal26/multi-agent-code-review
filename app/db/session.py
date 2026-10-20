"""Database session management — engines created lazily to avoid import-time DB connections."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from app.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

_async_engine: AsyncEngine | None = None
_async_session_factory: Any = None
_sync_engine: Engine | None = None


def _get_async_factory():
    global _async_engine, _async_session_factory
    if _async_engine is None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        settings = get_settings()
        _async_engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            echo=not settings.is_production,
        )
        _async_session_factory = async_sessionmaker(_async_engine, expire_on_commit=False)
    return _async_session_factory


def get_sync_engine() -> Engine:
    global _sync_engine
    if _sync_engine is None:
        from sqlalchemy import create_engine
        settings = get_settings()
        _sync_engine = create_engine(
            settings.database_url_sync,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _sync_engine


async def get_db() -> AsyncGenerator[AsyncSession, None]:  # type: ignore[type-arg]
    """FastAPI dependency: yield an async DB session."""
    factory = _get_async_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
