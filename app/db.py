from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.settings import get_settings


def _ensure_data_dir() -> None:
    os.makedirs("data", exist_ok=True)


def create_engine() -> AsyncEngine:
    _ensure_data_dir()
    settings = get_settings()
    return create_async_engine(settings.database_url, echo=False, future=True)


engine = create_engine()
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session

