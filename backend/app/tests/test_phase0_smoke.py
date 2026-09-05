"""Phase 0 smoke tests — prove the skeleton actually works end-to-end
against a real Postgres database, not just that it imports cleanly.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Merchant

pytestmark = pytest.mark.asyncio


async def test_health_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_ready_endpoint_reports_database_connected(client: AsyncClient) -> None:
    resp = await client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["database"] == "connected"


async def test_merchant_round_trip_uses_server_side_uuid(
    db_session: AsyncSession,
) -> None:
    merchant = Merchant(
        business_name="Sharma General Store",
        owner_name="Ramesh Sharma",
        phone="9876500001",
        city="Delhi",
        state="Delhi",
    )
    db_session.add(merchant)
    await db_session.commit()
    await db_session.refresh(merchant)

    assert merchant.id is not None
    assert merchant.created_at is not None
    assert merchant.active is True  # column default applied

    fetched = (
        await db_session.execute(
            select(Merchant).where(Merchant.business_name == "Sharma General Store")
        )
    ).scalar_one()
    assert fetched.id == merchant.id
    assert fetched.city == "Delhi"
