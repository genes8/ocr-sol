"""Audit trail helper for writing audit events."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.db import AuditLog


async def write_audit(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    event: str,
    document_id: uuid.UUID | None = None,
    actor: str = "system",
    payload: dict[str, Any] | None = None,
) -> None:
    """Append audit event. Caller is responsible for commit."""
    session.add(AuditLog(
        tenant_id=tenant_id,
        document_id=document_id,
        actor=actor,
        event=event,
        payload=payload,
    ))
