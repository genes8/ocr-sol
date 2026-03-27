"""Webhook management and delivery API routes."""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.core.database import async_session_maker, get_db
from api.core.security import get_current_tenant
from api.models.db import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)
from api.routes.schemas import (
    WebhookCreate,
    WebhookListResponse,
    WebhookResponse,
)

router = APIRouter()

WEBHOOK_EVENTS = [
    "document.pending",
    "document.preprocessing",
    "document.ocr_started",
    "document.classified",
    "document.structuring",
    "document.reconciliation",
    "document.validating",
    "document.completed",
    "document.review",
    "document.failed",
]


def generate_signature(payload: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload."""
    return hmac.new(
        secret.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()


async def deliver_webhook(
    webhook_id: uuid.UUID,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Deliver a webhook payload to the configured URL."""
    async with async_session_maker() as db:
        result = await db.execute(
            select(Webhook).where(
                Webhook.id == webhook_id,
                Webhook.tenant_id == tenant_id,
            )
        )
        webhook = result.scalar_one_or_none()

        if not webhook or not webhook.is_active:
            return

        payload_json = json.dumps(payload, default=str)
        signature = generate_signature(payload_json, webhook.secret)

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={signature}",
            "X-Webhook-Event": event_type,
            "X-Webhook-Delivery-ID": str(uuid.uuid4()),
        }

        if webhook.headers:
            headers.update(webhook.headers)

        delivery = WebhookDelivery(
            webhook_id=webhook_id,
            document_id=payload.get("document_id"),
            event_type=event_type,
            payload=payload,
            attempts=0,
        )
        db.add(delivery)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    webhook.url,
                    content=payload_json,
                    headers=headers,
                )

            delivery.status_code = response.status_code
            delivery.response_body = response.text[:1000] if response.text else None
            delivery.delivered_at = datetime.now(timezone.utc)

        except httpx.TimeoutException as e:
            delivery.error_message = f"Timeout: {str(e)}"
            delivery.attempts += 1

        except httpx.RequestError as e:
            delivery.error_message = f"Request error: {str(e)}"
            delivery.attempts += 1

        await db.commit()


@router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    data: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> WebhookResponse:
    """Create a new webhook."""
    for event in data.events:
        if event not in WEBHOOK_EVENTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid event type: {event}. Valid events: {WEBHOOK_EVENTS}",
            )

    secret = f"whsec_{uuid.uuid4().hex}{uuid.uuid4().hex[:16]}"

    webhook = Webhook(
        tenant_id=tenant_id,
        name=data.name,
        url=str(data.url),
        secret=secret,
        events=data.events,
        headers=data.headers,
        retry_count=data.retry_count,
        retry_delay_seconds=data.retry_delay_seconds,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)

    return WebhookResponse.model_validate(webhook)


@router.get("", response_model=WebhookListResponse)
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> WebhookListResponse:
    """List all webhooks for the current tenant."""
    result = await db.execute(
        select(Webhook)
        .where(Webhook.tenant_id == tenant_id)
        .order_by(desc(Webhook.created_at))
    )
    webhooks = result.scalars().all()

    return WebhookListResponse(
        total=len(webhooks),
        items=[WebhookResponse.model_validate(w) for w in webhooks],
    )


@router.get("/{webhook_id}", response_model=WebhookResponse)
async def get_webhook(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> WebhookResponse:
    """Get a specific webhook by ID."""
    result = await db.execute(
        select(Webhook).where(
            Webhook.id == webhook_id,
            Webhook.tenant_id == tenant_id,
        )
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found",
        )

    return WebhookResponse.model_validate(webhook)


@router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: uuid.UUID,
    data: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> WebhookResponse:
    """Update a webhook."""
    result = await db.execute(
        select(Webhook).where(
            Webhook.id == webhook_id,
            Webhook.tenant_id == tenant_id,
        )
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found",
        )

    for event in data.events:
        if event not in WEBHOOK_EVENTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid event type: {event}",
            )

    webhook.name = data.name
    webhook.url = str(data.url)
    webhook.events = data.events
    webhook.headers = data.headers
    webhook.retry_count = data.retry_count
    webhook.retry_delay_seconds = data.retry_delay_seconds
    webhook.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(webhook)

    return WebhookResponse.model_validate(webhook)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> None:
    """Delete a webhook."""
    result = await db.execute(
        select(Webhook).where(
            Webhook.id == webhook_id,
            Webhook.tenant_id == tenant_id,
        )
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found",
        )

    await db.delete(webhook)
    await db.commit()


@router.post("/{webhook_id}/test")
async def test_webhook(
    webhook_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict[str, str]:
    """Send a test webhook delivery."""
    result = await db.execute(
        select(Webhook).where(
            Webhook.id == webhook_id,
            Webhook.tenant_id == tenant_id,
        )
    )
    webhook = result.scalar_one_or_none()

    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found",
        )

    payload = {
        "event": "webhook.test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "message": "This is a test webhook delivery",
            "webhook_id": str(webhook_id),
        },
    }

    background_tasks.add_task(
        deliver_webhook,
        webhook_id,
        tenant_id,
        "webhook.test",
        payload,
    )

    return {"message": "Test webhook queued for delivery"}


@router.get("/{webhook_id}/deliveries")
async def list_webhook_deliveries(
    webhook_id: uuid.UUID,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict[str, Any]:
    """List deliveries for a webhook."""
    webhook_result = await db.execute(
        select(Webhook).where(
            Webhook.id == webhook_id,
            Webhook.tenant_id == tenant_id,
        )
    )
    webhook = webhook_result.scalar_one_or_none()

    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found",
        )

    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(desc(WebhookDelivery.created_at))
        .offset(skip)
        .limit(limit)
    )
    deliveries = result.scalars().all()

    return {
        "webhook_id": str(webhook_id),
        "deliveries": [
            {
                "id": str(d.id),
                "event_type": d.event_type,
                "document_id": str(d.document_id) if d.document_id else None,
                "status_code": d.status_code,
                "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
                "error_message": d.error_message,
                "attempts": d.attempts,
                "created_at": d.created_at.isoformat(),
            }
            for d in deliveries
        ],
    }
