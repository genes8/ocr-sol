"""Review queue worker — handles documents that need human review.

Responsibilities:
- Dispatch webhook notifications to the tenant when a document enters review
- Record review queue entry time for SLA metering
- Escalate stale reviews (optional, triggered by periodic beat task)
"""

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy import select

from api.core.config import settings
from api.core.database import SyncSessionLocal
from api.models.db import AuditLog, Decision, Document, DocumentStatus, Webhook
from workers.celery_app import celery_app


def write_audit_event(
    tenant_id: str,
    event: str,
    document_id: str | None = None,
    actor: str = "system",
    payload: dict | None = None,
) -> None:
    """Write audit event from sync worker context."""
    session = SyncSessionLocal()
    try:
        session.add(AuditLog(
            tenant_id=uuid.UUID(tenant_id),
            document_id=uuid.UUID(document_id) if document_id else None,
            actor=actor,
            event=event,
            payload=payload,
        ))
        session.commit()
    finally:
        session.close()

logger = logging.getLogger(__name__)


def _dispatch_webhook(
    webhook: Webhook,
    event_type: str,
    payload: dict[str, Any],
) -> bool:
    """Send a single webhook delivery. Returns True on success."""
    body = json.dumps(payload, default=str)
    signature = hmac.new(
        webhook.secret.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-OCR-Signature": f"sha256={signature}",
        "X-OCR-Event": event_type,
        **(webhook.headers or {}),
    }

    try:
        resp = requests.post(
            str(webhook.url),
            data=body,
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(
            f"Webhook {webhook.id} delivered event={event_type} "
            f"status={resp.status_code}"
        )
        return True
    except Exception as exc:
        logger.warning(f"Webhook {webhook.id} delivery failed: {exc}")
        return False


@celery_app.task(bind=True, name="workers.review.tasks.handle_review")
def handle_review(
    self,
    document_id: str,
    tenant_id: str,
    decision: str,
    priority: int = 5,
) -> dict[str, Any]:
    """Handle document that has been flagged for review.

    Args:
        document_id: Document UUID
        tenant_id: Tenant UUID
        decision: Decision value ("review" or "manual")
        priority: Celery task priority

    Returns:
        Summary dict
    """
    start_time = time.time()
    logger.info(
        f"Review handler: document={document_id} decision={decision}"
    )

    try:
        _session = SyncSessionLocal()
        try:
            doc = _session.execute(
                select(Document).where(Document.id == uuid.UUID(document_id))
            ).scalar_one_or_none()

            webhooks = list(_session.execute(
                select(Webhook).where(
                    Webhook.tenant_id == uuid.UUID(tenant_id),
                    Webhook.is_active.is_(True),
                )
            ).scalars().all())
        finally:
            _session.close()

        if not doc:
            logger.warning(f"Document {document_id} not found in review handler")
            return {"document_id": document_id, "status": "skipped", "reason": "not_found"}

        # Build event payload
        event_type = (
            "document.manual_review_required"
            if decision == Decision.MANUAL
            else "document.review_required"
        )
        webhook_payload = {
            "event": event_type,
            "document_id": document_id,
            "tenant_id": tenant_id,
            "decision": decision,
            "document_type": doc.document_type.value if doc.document_type else None,
            "filename": doc.original_filename,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Dispatch webhooks that subscribed to this event type
        dispatched = 0
        for webhook in webhooks:
            subscribed_events = webhook.events or []
            if event_type in subscribed_events or "document.*" in subscribed_events:
                _dispatch_webhook(webhook, event_type, webhook_payload)
                dispatched += 1

        processing_time = time.time() - start_time
        logger.info(
            f"Review handled for document {document_id}: "
            f"decision={decision}, webhooks_dispatched={dispatched}"
        )

        write_audit_event(
            tenant_id, "pipeline.review.queued", document_id,
            actor="worker:review",
            payload={"decision": decision, "webhooks_dispatched": dispatched},
        )

        return {
            "document_id": document_id,
            "status": "handled",
            "decision": decision,
            "webhooks_dispatched": dispatched,
            "processing_time": processing_time,
        }

    except Exception as exc:
        logger.exception(f"Review handler failed for document {document_id}")
        raise self.retry(exc=exc, countdown=30, max_retries=3)


@celery_app.task(name="workers.review.tasks.escalate_stale_reviews")
def escalate_stale_reviews(stale_hours: int = 24) -> dict[str, Any]:
    """Escalate documents stuck in REVIEW status beyond the stale threshold.

    This task is intended to be called periodically via Celery Beat.
    It re-dispatches webhook events for stale review documents.
    """
    _session = SyncSessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=stale_hours)
        stale_docs = list(_session.execute(
            select(Document).where(
                Document.status.in_([DocumentStatus.REVIEW, DocumentStatus.MANUAL_REVIEW]),
                Document.processing_completed_at <= cutoff,
            )
        ).scalars().all())
    finally:
        _session.close()
    escalated = 0
    for doc in stale_docs:
        handle_review.apply_async(
            args=[str(doc.id), str(doc.tenant_id), doc.decision.value if doc.decision else "review"],
        )
        escalated += 1

    logger.info(f"Escalated {escalated} stale review documents")
    return {"escalated": escalated, "stale_hours": stale_hours}
