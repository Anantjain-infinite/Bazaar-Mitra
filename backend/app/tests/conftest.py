"""Shared fixtures for the `app/` test suite.

Tests run against a real Postgres database (`bazaar_mitra_test` by
default, override with TEST_DATABASE_URL) rather than SQLite or mocks —
several of the things this project needs to prove (UUID server defaults,
numeric precision, cascade deletes, unique constraints) don't reliably
match between SQLite and Postgres, and this is a Postgres-only project.

Each test function gets the schema created fresh and dropped afterward,
so tests can't leak state into each other.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/bazaar_mitra_test",
)


@pytest_asyncio.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(TEST_DATABASE_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(
    test_engine: AsyncEngine, db_session: AsyncSession
) -> AsyncGenerator[AsyncClient, None]:
    """An httpx client wired to the FastAPI app.

    Deliberately does NOT reuse `db_session` for the app's own DB
    dependency — each simulated HTTP request gets its OWN fresh session
    from a factory bound to the same test engine/schema, exactly
    mirroring production's `get_db()` (a new `AsyncSessionLocal()` per
    request). This isn't cosmetic: a shared single session/transaction
    across "requests" made a real bug invisible in earlier tests — a
    tool function that flushed but never committed a state change still
    looked correct, because the next "request" was secretly reading the
    same uncommitted transaction rather than a fresh one. Isolating
    sessions here means these tests now exercise the same commit/
    rollback boundaries real traffic does.

    `db_session` is still a fixture dependency (for the test's own
    arrange/assert code, and so its schema setup/teardown wraps this
    fixture too) even though the app doesn't use that exact session.
    """
    from app.db.session import get_db
    from app.main import app

    session_factory = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
