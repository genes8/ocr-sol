"""Validation worker tasks - Schema validation and decision engine."""

import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft7Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.core.database import get_db_session
from api.models.db import (
    Decision,
    Document,
    DocumentStatus,
    DocumentType,
    ReconciliationLog,
    Supplier,
    StructuredResult,
    Tenant,
)
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Schema cache
SCHEMA_CACHE: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Confidence resolution helpers
# ---------------------------------------------------------------------------

# Maps canonical threshold key → ordered list of LLM-output key candidates.
# The first matching key wins. Handles flat, nested-dot, and parent-object forms
# that different LLM outputs may use.
_CONFIDENCE_CANDIDATES: dict[str, list[str]] = {
    "invoice_number": ["invoice_number"],
    "invoice_date": ["invoice_date"],
    "issue_date": ["issue_date"],
    "supplier_name": ["supplier_name", "supplier.name", "supplier"],
    "supplier_pib": ["supplier_pib", "supplier.pib"],
    "total_amount": ["total_amount", "totals.grand_total", "grand_total", "totals"],
    "grand_total": ["grand_total", "totals.grand_total", "total_amount", "totals"],
    "vat_amount": ["vat_amount", "totals.vat_amount", "totals.vat_total", "vat_total"],
    "vat_total": ["vat_total", "totals.vat_total", "totals.vat_amount", "vat_amount"],
}

# Critical fields that trigger MANUAL decision if confidence < CRITICAL_FIELD_THRESHOLD
_CRITICAL_FIELD_CANDIDATES: dict[str, list[str]] = {
    "grand_total": ["grand_total", "totals.grand_total", "total_amount", "totals"],
    "invoice_number": ["invoice_number"],
    "invoice_date": ["invoice_date"],
    "issue_date": ["issue_date"],
    "supplier_name": ["supplier_name", "supplier.name", "supplier"],
}


def _resolve_confidence(
    confidences: dict[str, float],
    *candidate_keys: str,
) -> float | None:
    """Return the first matching confidence value from a list of candidate keys."""
    for key in candidate_keys:
        if key in confidences:
            return confidences[key]
    return None


def write_audit_event(
    tenant_id: str,
    event: str,
    document_id: str | None = None,
    actor: str = "system",
    payload: dict[str, Any] | None = None,
) -> None:
    """Write audit event from sync worker context."""
    import asyncio

    async def _write():
        from api.core.audit import write_audit
        session = await get_db_session()
        try:
            await write_audit(
                session,
                uuid.UUID(tenant_id),
                event,
                document_id=uuid.UUID(document_id) if document_id else None,
                actor=actor,
                payload=payload,
            )
            await session.commit()
        finally:
            await session.close()

    asyncio.run(_write())


# get_db_session imported from api.core.database


def update_document(
    document_id: str,
    status: DocumentStatus | None = None,
    decision: Decision | None = None,
    error_message: str | None = None,
    processing_completed_at: bool = False,
) -> None:
    """Update document in database."""
    import asyncio

    async def _update():
        session = await get_db_session()
        try:
            result = await session.execute(
                select(Document).where(Document.id == uuid.UUID(document_id))
            )
            doc = result.scalar_one_or_none()
            if doc:
                if status is not None:
                    doc.status = status
                if decision is not None:
                    doc.decision = decision
                if error_message is not None:
                    doc.error_message = error_message
                if processing_completed_at:
                    doc.processing_completed_at = datetime.utcnow()
                await session.commit()
        finally:
            await session.close()

    asyncio.run(_update())


def load_schema(document_type: DocumentType) -> dict[str, Any]:
    """Load JSON schema for document type."""
    if document_type.value in SCHEMA_CACHE:
        return SCHEMA_CACHE[document_type.value]

    schema_dir = Path(__file__).parent.parent.parent / "api" / "schemas"
    schema_path = schema_dir / f"{document_type.value}_schema.json"

    if schema_path.exists():
        with open(schema_path) as f:
            schema = json.load(f)
            SCHEMA_CACHE[document_type.value] = schema
            return schema

    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": document_type.value,
        "type": "object",
        "properties": {},
        "required": [],
    }
    SCHEMA_CACHE[document_type.value] = schema
    return schema


def validate_schema(
    data: dict[str, Any],
    schema: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    """Validate data against JSON schema."""
    validator = Draft7Validator(schema)
    errors = []

    for error in validator.iter_errors(data):
        path = ".".join(str(p) for p in error.path) if error.path else "root"
        errors.append({
            "field": path,
            "message": error.message,
            "validator": error.validator,
            "value": str(error.instance)[:100],
        })

    return len(errors) == 0, errors


def validate_business_rules(
    data: dict[str, Any],
    document_type: DocumentType,
) -> list[dict[str, str]]:
    """Validate business rules for document type."""
    violations = []

    if "pib" in data:
        pib = str(data["pib"])
        if not (pib.isdigit() and len(pib) in [9, 10]):
            violations.append({
                "rule": "pib_format",
                "message": f"PIB must be 9 or 10 digits, got: {pib}",
            })

    date_fields = ["invoice_date", "issue_date", "due_date", "valid_until"]
    import re
    date_pattern = re.compile(r"^\d{2}[./]\d{2}[./]\d{4}$|^\d{4}-\d{2}-\d{2}$")

    for field in date_fields:
        if field in data:
            value = str(data[field])
            if not date_pattern.match(value):
                violations.append({
                    "rule": "date_format",
                    "message": f"{field} must be in DD.MM.YYYY or ISO format, got: {value}",
                })

    totals = data.get("totals", {}) or {}
    amount_sources = {
        "grand_total": totals.get("grand_total") or data.get("grand_total") or data.get("total_amount"),
        "subtotal": totals.get("subtotal") or data.get("subtotal"),
        "vat_total": totals.get("vat_total") or data.get("vat_amount"),
    }
    for field, value in amount_sources.items():
        if value is not None:
            try:
                amount = float(str(value).replace(",", ""))
                if amount < 0:
                    violations.append({
                        "rule": "negative_amount",
                        "message": f"{field} cannot be negative: {amount}",
                    })
            except ValueError:
                violations.append({
                    "rule": "invalid_amount",
                    "message": f"{field} is not a valid number: {value}",
                })

    required_fields = {
        DocumentType.INVOICE: ["invoice_number", "invoice_date", "totals"],
        DocumentType.PROFORMA: ["proforma_number", "issue_date"],
        DocumentType.DELIVERY_NOTE: ["delivery_note_number", "issue_date"],
        DocumentType.CONTRACT: ["contract_number", "issue_date"],
    }

    if document_type in required_fields:
        for field in required_fields[document_type]:
            if field not in data or not data[field]:
                violations.append({
                    "rule": "required_field",
                    "message": f"Required field missing: {field}",
                })

    return violations


def get_tenant_confidence_thresholds(tenant: Tenant | None) -> dict[str, float]:
    """Get per-tenant confidence thresholds, falling back to global defaults."""
    defaults = {
        "invoice_number": settings.DEFAULT_INVOICE_NUMBER_CONFIDENCE,
        "invoice_date": settings.DEFAULT_INVOICE_DATE_CONFIDENCE,
        "issue_date": settings.DEFAULT_INVOICE_DATE_CONFIDENCE,
        "supplier_name": settings.DEFAULT_SUPPLIER_CONFIDENCE,
        "supplier_pib": settings.DEFAULT_SUPPLIER_CONFIDENCE,
        "total_amount": settings.DEFAULT_TOTAL_AMOUNT_CONFIDENCE,
        "grand_total": settings.DEFAULT_TOTAL_AMOUNT_CONFIDENCE,
        "vat_amount": settings.DEFAULT_VAT_AMOUNT_CONFIDENCE,
        "vat_total": settings.DEFAULT_VAT_AMOUNT_CONFIDENCE,
    }
    if tenant and tenant.settings and "confidence_thresholds" in tenant.settings:
        defaults.update(tenant.settings["confidence_thresholds"])
    return defaults


async def lookup_supplier(
    tenant_id: uuid.UUID,
    extracted_data: dict[str, Any],
    session: AsyncSession,
) -> dict[str, Any] | None:
    """Look up a supplier by PIB from extracted data.

    Returns supplier dict if found, None otherwise.
    """
    supplier_section = extracted_data.get("supplier", {}) or {}
    pib = supplier_section.get("pib") or extracted_data.get("supplier_pib")

    if not pib:
        return None

    result = await session.execute(
        select(Supplier).where(
            Supplier.tenant_id == tenant_id,
            Supplier.pib == str(pib),
            Supplier.is_active.is_(True),
        )
    )
    supplier = result.scalar_one_or_none()

    if not supplier:
        return None

    return {
        "id": str(supplier.id),
        "name": supplier.name,
        "pib": supplier.pib,
        "mb": supplier.mb,
        "iban": supplier.iban,
        "address": supplier.address,
    }


async def detect_duplicate(
    tenant_id: uuid.UUID,
    extracted_data: dict[str, Any],
    document_id: uuid.UUID,
    session: AsyncSession,
) -> bool:
    """Detect potential duplicate invoices.

    Checks for same supplier PIB + invoice_number within last 90 days.
    Secondary: same PIB + total_amount within 1% tolerance.
    """
    supplier_section = extracted_data.get("supplier", {}) or {}
    pib = supplier_section.get("pib") or extracted_data.get("supplier_pib")
    invoice_number = extracted_data.get("invoice_number")

    if not pib:
        return False

    cutoff = datetime.utcnow() - timedelta(days=90)

    # extracted_data is JSONB — subscript path queries use ->> operator
    pib_path = StructuredResult.extracted_data["supplier"]["pib"].as_string()

    # Primary: same PIB + invoice_number
    if invoice_number:
        result = await session.execute(
            select(StructuredResult.id)
            .join(Document, Document.id == StructuredResult.document_id)
            .where(
                Document.tenant_id == tenant_id,
                Document.id != document_id,
                Document.created_at >= cutoff,
                pib_path == str(pib),
                StructuredResult.extracted_data["invoice_number"].as_string() == str(invoice_number),
            )
            .limit(1)
        )
        if result.scalar_one_or_none():
            return True

    # Secondary: same PIB + total_amount within 1% tolerance.
    # Candidates are limited to 200 rows to avoid unbounded scans on busy tenants.
    totals = extracted_data.get("totals", {}) or {}
    total_raw = (
        totals.get("grand_total")
        or extracted_data.get("grand_total")
        or extracted_data.get("total_amount")
    )
    if total_raw is not None:
        try:
            total_amount = float(str(total_raw).replace(",", ""))
            tolerance = total_amount * 0.01

            result = await session.execute(
                select(StructuredResult.id, StructuredResult.extracted_data)
                .join(Document, Document.id == StructuredResult.document_id)
                .where(
                    Document.tenant_id == tenant_id,
                    Document.id != document_id,
                    Document.created_at >= cutoff,
                    pib_path == str(pib),
                )
                .limit(200)
            )
            for _, c_data in result.all():
                c_totals = (c_data.get("totals") or {}) if c_data else {}
                c_total_raw = (
                    c_totals.get("grand_total")
                    or c_data.get("grand_total")
                    or c_data.get("total_amount")
                )
                if c_total_raw is not None:
                    try:
                        if abs(float(str(c_total_raw).replace(",", "")) - total_amount) <= tolerance:
                            return True
                    except (ValueError, TypeError):
                        pass
        except (ValueError, TypeError):
            pass

    return False


def determine_decision(
    structured: StructuredResult,
    reconciliation: ReconciliationLog | None,
    schema_valid: bool,
    business_violations: list,
    tenant: Tenant | None = None,
    is_duplicate: bool = False,
    supplier_match: dict[str, Any] | None = None,
) -> tuple[Decision, str]:
    """Determine processing decision based on validation results."""
    confidences = structured.field_confidences or {}
    thresholds = get_tenant_confidence_thresholds(tenant)

    # Feature 4: Duplicate detection takes priority
    if is_duplicate:
        return Decision.REVIEW, "possible_duplicate"

    # Feature 4: Unknown supplier triggers review
    supplier_section = structured.extracted_data.get("supplier", {}) or {}
    supplier_pib = supplier_section.get("pib") or structured.extracted_data.get("supplier_pib")
    if supplier_pib and supplier_match is None:
        return Decision.REVIEW, "supplier_not_found"

    # Check for critical fields below absolute manual threshold — use canonical
    # resolution so that nested LLM keys like "totals.grand_total" are matched.
    critical_low = [
        (canonical, conf)
        for canonical, candidates in _CRITICAL_FIELD_CANDIDATES.items()
        if (conf := _resolve_confidence(confidences, *candidates)) is not None
        and conf < settings.CRITICAL_FIELD_THRESHOLD
    ]
    if critical_low:
        return Decision.MANUAL, f"Critical fields below threshold: {critical_low}"

    if reconciliation and reconciliation.reconciliation_status == "fail":
        return Decision.MANUAL, "Reconciliation failed: math mismatch"

    fields_below_threshold = [
        (field, conf, min_conf)
        for field, min_conf in thresholds.items()
        if (conf := _resolve_confidence(
            confidences,
            *_CONFIDENCE_CANDIDATES.get(field, [field]),
        )) is not None
        and conf < min_conf
    ]
    if fields_below_threshold:
        return Decision.REVIEW, f"Fields below auto threshold: {fields_below_threshold}"

    if reconciliation and reconciliation.reconciliation_status == "warn":
        return Decision.REVIEW, "Reconciliation warning: minor discrepancy"

    if business_violations:
        return Decision.REVIEW, f"Business rule violations: {len(business_violations)}"

    if not schema_valid:
        return Decision.REVIEW, "Schema validation failed"

    # Check aggregate line-item confidence if present
    line_item_confs = [
        v for k, v in confidences.items()
        if k.startswith("line_items") or k == "items"
    ]
    if line_item_confs:
        avg_li_conf = sum(line_item_confs) / len(line_item_confs)
        if avg_li_conf < settings.DEFAULT_LINE_ITEM_CONFIDENCE:
            return Decision.REVIEW, f"Low avg line-item confidence: {avg_li_conf:.2f}"

    return Decision.AUTO, "All validations passed"


def calculate_overall_confidence(
    structured: StructuredResult,
    reconciliation: ReconciliationLog | None,
) -> float:
    """Calculate overall document confidence score."""
    confidences = structured.field_confidences or {}

    if not confidences:
        return 0.5

    field_confidence = sum(confidences.values()) / len(confidences)

    reconciliation_factor = 1.0
    if reconciliation:
        if reconciliation.reconciliation_status == "fail":
            reconciliation_factor = 0.5
        elif reconciliation.reconciliation_status == "warn":
            reconciliation_factor = 0.8
        else:
            reconciliation_factor = 1.0

    overall = field_confidence * 0.7 + reconciliation_factor * 0.3

    return min(max(overall, 0.0), 1.0)


@celery_app.task(bind=True, name="workers.validation.tasks.validate_document")
def validate_document(self, document_id: str, tenant_id: str, priority: int = 5) -> dict[str, Any]:
    """Validate document and determine processing decision.

    Args:
        document_id: Document UUID
        tenant_id: Tenant UUID
        priority: Task priority (0=highest, 9=lowest)

    Returns:
        Validation result
    """
    start_time = time.time()
    logger.info(f"Starting validation for document {document_id}")

    try:
        update_document(document_id, status=DocumentStatus.VALIDATING)

        import asyncio

        async def _get_data():
            session = await get_db_session()
            try:
                doc_result = await session.execute(
                    select(Document).where(Document.id == uuid.UUID(document_id))
                )
                doc = doc_result.scalar_one_or_none()

                structured_result = await session.execute(
                    select(StructuredResult).where(
                        StructuredResult.document_id == uuid.UUID(document_id)
                    )
                )
                structured = structured_result.scalar_one_or_none()

                reconciliation_result = await session.execute(
                    select(ReconciliationLog).where(
                        ReconciliationLog.document_id == uuid.UUID(document_id)
                    )
                )
                reconciliation = reconciliation_result.scalar_one_or_none()

                tenant_result = await session.execute(
                    select(Tenant).where(Tenant.id == uuid.UUID(tenant_id))
                )
                tenant = tenant_result.scalar_one_or_none()

                # Feature 4: Supplier lookup and duplicate detection
                supplier_match = None
                is_duplicate = False
                if structured:
                    supplier_match = await lookup_supplier(
                        uuid.UUID(tenant_id),
                        structured.extracted_data,
                        session,
                    )
                    is_duplicate = await detect_duplicate(
                        uuid.UUID(tenant_id),
                        structured.extracted_data,
                        uuid.UUID(document_id),
                        session,
                    )
                    # Persist supplier lookup result
                    structured.supplier_lookup_result = supplier_match
                    await session.commit()

                return doc, structured, reconciliation, tenant, supplier_match, is_duplicate
            finally:
                await session.close()

        doc, structured, reconciliation, tenant, supplier_match, is_duplicate = asyncio.run(_get_data())

        if not doc or not structured:
            raise ValueError(f"Document {document_id} data incomplete")

        schema = load_schema(doc.document_type)
        schema_valid, schema_errors = validate_schema(
            structured.extracted_data,
            schema,
        )

        business_violations = validate_business_rules(
            structured.extracted_data,
            doc.document_type,
        )

        decision, reasoning = determine_decision(
            structured,
            reconciliation,
            schema_valid,
            business_violations,
            tenant=tenant,
            is_duplicate=is_duplicate,
            supplier_match=supplier_match,
        )

        overall_confidence = calculate_overall_confidence(structured, reconciliation)

        if decision == Decision.AUTO:
            final_status = DocumentStatus.COMPLETED
        elif decision == Decision.REVIEW:
            final_status = DocumentStatus.REVIEW
        else:
            final_status = DocumentStatus.MANUAL_REVIEW

        update_document(
            document_id,
            status=final_status,
            decision=decision,
            processing_completed_at=True,
        )

        write_audit_event(
            tenant_id,
            "validation.decision",
            document_id=document_id,
            actor="worker:validation",
            payload={
                "decision": decision.value,
                "reasoning": reasoning,
                "overall_confidence": overall_confidence,
                "schema_valid": schema_valid,
                "is_duplicate": is_duplicate,
            },
        )

        # Dispatch to review queue so the review worker can send notifications/webhooks
        if decision in (Decision.REVIEW, Decision.MANUAL):
            from workers.review.tasks import handle_review
            handle_review.apply_async(
                args=[document_id, tenant_id, decision.value],
                priority=priority,
            )

        processing_time = time.time() - start_time

        logger.info(
            f"Validation completed for document {document_id}: "
            f"decision={decision.value}, confidence={overall_confidence:.2f}"
        )

        return {
            "document_id": document_id,
            "status": "completed",
            "decision": decision.value,
            "reasoning": reasoning,
            "overall_confidence": overall_confidence,
            "schema_valid": schema_valid,
            "schema_errors": schema_errors if not schema_valid else None,
            "business_violations": business_violations if business_violations else None,
            "reconciliation_status": reconciliation.reconciliation_status if reconciliation else None,
            "supplier_match": supplier_match,
            "is_duplicate": is_duplicate,
            "processing_time": processing_time,
        }

    except Exception as exc:
        logger.exception(f"Validation failed for document {document_id}")
        update_document(
            document_id,
            status=DocumentStatus.VALIDATION_FAILED,
            error_message=str(exc),
            processing_completed_at=True,
        )

        raise self.retry(exc=exc, countdown=60, max_retries=3)
