from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.audit import AuditEventOut
from app.services import audit_service

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=list[AuditEventOut])
async def list_audit_events(
    merchant_id: uuid.UUID | None = Query(default=None),
    buyer_id: uuid.UUID | None = Query(default=None),
    event_type: str | None = Query(
        default=None, description='e.g. "order", "payment", "policy_check"'
    ),
    success: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AuditEventOut]:
    events = await audit_service.list_events(
        db,
        merchant_id=merchant_id,
        buyer_id=buyer_id,
        event_type=event_type,
        success=success,
        limit=limit,
        offset=offset,
    )
    return [AuditEventOut.model_validate(e) for e in events]


@router.get("/{resource_id}", response_model=list[AuditEventOut])
async def get_audit_trail_for_resource(
    resource_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[AuditEventOut]:
    """Full, time-ordered audit trail for one resource (an order id, a
    payment id, ...) — enough for a human to answer: what happened, why,
    who/what initiated it, what was the amount, what policy applied, was
    the user asked, what did Razorpay return, was it verified, and what
    happened after any failure.
    """
    events = await audit_service.get_events_for_resource(db, resource_id)
    return [AuditEventOut.model_validate(e) for e in events]
