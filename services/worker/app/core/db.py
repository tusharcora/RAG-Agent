from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

_engine = None
_session_factory = None


async def init_engine() -> None:
    global _engine, _session_factory
    _engine = create_async_engine(settings.database_url, pool_pre_ping=True, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


async def dispose_engine() -> None:
    if _engine is not None:
        await _engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Worker equivalent of the api's FastAPI-dependency get_session — plain
    async context manager since the worker isn't a FastAPI app."""
    async with _session_factory() as session:
        yield session
