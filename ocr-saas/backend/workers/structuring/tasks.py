"""Structuring worker tasks - LLM JSON extraction."""

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.core.config import settings
from api.core.database import SyncSessionLocal
from api.models.db import (
    AuditLog,
    Document,
    DocumentStatus,
    DocumentType,
    OCRResult,
    StructuredResult,
    Tenant,
)
from workers.celery_app import celery_app
from workers.llm_utils import strip_llm_fences


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

# Schema cache
SCHEMA_CACHE: dict[str, dict[str, Any]] = {}


# get_db_session imported from api.core.database


def update_document_status(
    document_id: str,
    status: DocumentStatus,
    error_message: str | None = None,
) -> None:
    """Update document status in database."""
    session = SyncSessionLocal()
    try:
        result = session.execute(
            select(Document).where(Document.id == uuid.UUID(document_id))
        )
        doc = result.scalar_one_or_none()
        if doc:
            doc.status = status
            doc.error_message = error_message
            session.commit()
    finally:
        session.close()


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
    }
    SCHEMA_CACHE[document_type.value] = schema
    return schema


def build_extraction_prompt(
    text: str,
    document_type: DocumentType,
    schema: dict[str, Any],
    text_blocks: list[dict[str, Any]] | None = None,
) -> str:
    """Build prompt for LLM extraction.

    If text_blocks are provided, prepend an indexed block listing so the LLM
    can reference individual blocks via index in field_evidence.
    """
    properties = json.dumps(schema.get("properties", {}), indent=2, ensure_ascii=False)

    blocks_section = ""
    if text_blocks:
        lines = ["INDEXED TEXT BLOCKS:"]
        for i, block in enumerate(text_blocks[:150]):
            bbox = block.get("bbox", {})
            page = block.get("page", 1)
            lines.append(
                f'[{i}] "{block.get("text", "")}" '
                f'(page={page}, x1={bbox.get("x1")}, y1={bbox.get("y1")}, '
                f'x2={bbox.get("x2")}, y2={bbox.get("y2")})'
            )
        blocks_section = "\n".join(lines) + "\n\n"

    prompt = f"""You are an expert document parser. Extract structured data from the following {document_type.value} document.

{blocks_section}Extract all fields according to this JSON schema:
```json
{properties}
```

For each field, also provide a confidence score between 0 and 1 based on:
- 0.9-1.0: Clear, unambiguous extraction
- 0.7-0.9: Reasonable extraction with minor ambiguity
- 0.5-0.7: Uncertain extraction
- Below 0.5: Low confidence extraction

Return a JSON object with this structure:
{{
    "extracted_data": {{ ... your extracted fields ... }},
    "field_confidences": {{ "field_name": confidence_score, ... }},
    "field_evidence": {{ "field_name": block_index_or_null, ... }},
    "extraction_notes": "Any notes about ambiguous or unclear fields"
}}

field_evidence maps each extracted field name to the index of the TEXT BLOCK (from the INDEXED TEXT BLOCKS list above) that contains the source text for that field, or null if not traceable.

OCR Text to process:
---
{text[:8000]}
---

Return only valid JSON, no markdown or explanation."""

    return prompt


def call_llm_for_extraction(
    text: str,
    document_type: DocumentType,
    text_blocks: list[dict[str, Any]] | None = None,
    schema_override: dict[str, Any] | None = None,
    system_prompt_override: str | None = None,
) -> dict[str, Any]:
    """Call Structuring LLM for structured extraction.

    Uses STRUCTURING_LLM_BASE_URL (separate server from GLM-OCR vision model).
    Supports per-tenant schema and system prompt overrides.
    """
    schema = schema_override or load_schema(document_type)
    prompt = build_extraction_prompt(text, document_type, schema, text_blocks)

    import requests

    # Feature 1: Use dedicated structuring LLM server (not GLM-OCR)
    url = f"{settings.STRUCTURING_LLM_BASE_URL}/v1/chat/completions"

    system_content = system_prompt_override or "You are a precise document extraction assistant."
    payload = {
        "model": settings.STRUCTURING_LLM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4096,
    }

    headers = {
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=settings.STRUCTURING_LLM_TIMEOUT,
        )
        response.raise_for_status()

        result = response.json()
        content = result["choices"][0]["message"]["content"]
        parsed = json.loads(strip_llm_fences(content))

        # Feature 2: Resolve field_evidence indices → actual bbox blocks
        bbox_evidence: dict[str, Any] = {}
        for field, idx in parsed.get("field_evidence", {}).items():
            if isinstance(idx, int) and text_blocks and 0 <= idx < len(text_blocks):
                bbox_evidence[field] = text_blocks[idx]
        parsed["bbox_evidence"] = bbox_evidence

        return parsed

    except requests.exceptions.RequestException as e:
        logger.error(f"Structuring LLM API request failed: {e}")
        raise


def normalize_extracted_data(
    data: dict[str, Any],
    document_type: DocumentType,
) -> dict[str, Any]:
    """Normalize LLM-extracted data to canonical field names for the pipeline.

    Key normalizations:
    - delivery_note: schema uses `items` array → pipeline needs `line_items`
    - all line-item types: ensure canonical keys (description, quantity, unit_price,
      line_total, vat_rate) are present, merging from common aliases.
    """
    import copy
    data = copy.deepcopy(data)

    # delivery_note: map `items` → `line_items`
    if document_type == DocumentType.DELIVERY_NOTE:
        if "items" in data and "line_items" not in data:
            raw_items = data.pop("items", [])
            line_items = []
            for item in raw_items:
                line_items.append({
                    "description": item.get("description", ""),
                    "quantity": item.get("quantity") or item.get("delivered_quantity"),
                    "unit": item.get("unit", "kom"),
                    "sku": item.get("sku"),
                    "batch_number": item.get("batch_number"),
                    # delivery notes usually lack pricing — preserve if present
                    "unit_price": item.get("unit_price"),
                    "line_total": item.get("line_total"),
                    "vat_rate": item.get("vat_rate"),
                })
            data["line_items"] = line_items

    # Normalize existing line_items to canonical keys
    raw_items = data.get("line_items", [])
    if raw_items:
        normalized = []
        for item in raw_items:
            # Skip already-garbage items (raw_text-only from old extract_line_items)
            if set(item.keys()) == {"raw_text", "values"}:
                continue
            norm: dict[str, Any] = {
                "description": (
                    item.get("description")
                    or item.get("name")
                    or item.get("item_name")
                    or item.get("raw_text", "")
                ),
                "quantity": item.get("quantity") or item.get("qty"),
                "unit_price": item.get("unit_price") or item.get("price") or item.get("unit_cost"),
                "line_total": (
                    item.get("line_total")
                    or item.get("total")
                    or item.get("amount")
                    or item.get("subtotal")
                ),
                "vat_rate": item.get("vat_rate"),
            }
            # Preserve any extra fields (unit, sku, batch_number, …)
            for k, v in item.items():
                if k not in norm:
                    norm[k] = v
            normalized.append(norm)
        data["line_items"] = normalized

    return data


def save_structured_result(
    document_id: str,
    document_type: DocumentType,
    extracted_data: dict[str, Any],
    field_confidences: dict[str, float],
    raw_llm_response: str,
    processing_time_ms: int,
    bbox_evidence: dict[str, Any] | None = None,
) -> str:
    """Save structured result to database (upsert — idempotent per document).

    Returns:
        Structured result ID
    """
    session = SyncSessionLocal()
    try:
        new_id = uuid.uuid4()
        stmt = (
            pg_insert(StructuredResult)
            .values(
                id=new_id,
                document_id=uuid.UUID(document_id),
                document_type=document_type,
                extracted_data=extracted_data,
                field_confidences=field_confidences,
                raw_llm_response=raw_llm_response,
                model_version=settings.STRUCTURING_LLM_MODEL_NAME,
                processing_time_ms=processing_time_ms,
                bbox_evidence=bbox_evidence,
            )
            .on_conflict_do_update(
                constraint="uq_structured_results_document",
                set_={
                    "document_type": document_type,
                    "extracted_data": extracted_data,
                    "field_confidences": field_confidences,
                    "raw_llm_response": raw_llm_response,
                    "model_version": settings.STRUCTURING_LLM_MODEL_NAME,
                    "processing_time_ms": processing_time_ms,
                    "bbox_evidence": bbox_evidence,
                },
            )
            .returning(StructuredResult.id)
        )
        result = session.execute(stmt)
        session.commit()
        return str(result.scalar_one())
    finally:
        session.close()


@celery_app.task(bind=True, name="workers.structuring.tasks.extract_structure")
def extract_structure(self, document_id: str, tenant_id: str, priority: int = 5) -> dict[str, Any]:
    """Extract structured data from document using LLM.

    Args:
        document_id: Document UUID
        tenant_id: Tenant UUID
        priority: Task priority (0=highest, 9=lowest)

    Returns:
        Extraction result
    """
    start_time = time.time()
    logger.info(f"Starting structuring for document {document_id}")

    # Feature flag gate — canary/rollback support
    if not settings.ENABLE_LLM_STRUCTURING:
        logger.warning(f"LLM structuring disabled (feature flag). Routing document {document_id} to MANUAL_REVIEW.")
        update_document_status(document_id, DocumentStatus.MANUAL_REVIEW, error_message="llm_structuring_disabled")
        return {"document_id": document_id, "status": "manual_review", "reason": "llm_structuring_disabled"}

    try:
        update_document_status(document_id, DocumentStatus.STRUCTURING)

        _session = SyncSessionLocal()
        try:
            _doc_result = _session.execute(
                select(Document).where(Document.id == uuid.UUID(document_id))
            )
            doc = _doc_result.scalar_one_or_none()

            if not doc or not doc.document_type:
                raise ValueError(f"Document {document_id} not found or not classified")

            _ocr_result = _session.execute(
                select(OCRResult).where(
                    OCRResult.document_id == uuid.UUID(document_id)
                )
            )
            ocr_result = _ocr_result.scalar_one_or_none()

            if not ocr_result:
                raise ValueError(f"No OCR result for document {document_id}")

            _tenant_result = _session.execute(
                select(Tenant).where(Tenant.id == doc.tenant_id)
            )
            tenant = _tenant_result.scalar_one_or_none()
            tenant_settings = (tenant.settings or {}) if tenant else {}
        finally:
            _session.close()

        # Per-tenant schema/prompt routing
        # tenant.settings.schema_overrides: {"invoice": {...schema...}, ...}
        # tenant.settings.system_prompt: "Custom system prompt text"
        schema_overrides = tenant_settings.get("schema_overrides", {})
        schema_override = schema_overrides.get(doc.document_type.value) if schema_overrides else None
        system_prompt_override = tenant_settings.get("system_prompt")

        # Feature 2: Pass text_blocks so LLM can reference them for bbox evidence
        extraction = call_llm_for_extraction(
            ocr_result.full_text,
            doc.document_type,
            text_blocks=ocr_result.text_blocks,
            schema_override=schema_override,
            system_prompt_override=system_prompt_override,
        )

        LINE_ITEM_TYPES = {DocumentType.INVOICE, DocumentType.PROFORMA, DocumentType.DELIVERY_NOTE}

        # Normalize extracted data to canonical field names before persisting
        normalized_data = normalize_extracted_data(
            extraction.get("extracted_data", {}),
            doc.document_type,
        )

        processing_time_ms = int((time.time() - start_time) * 1000)

        save_structured_result(
            document_id=document_id,
            document_type=doc.document_type,
            extracted_data=normalized_data,
            field_confidences=extraction.get("field_confidences", {}),
            raw_llm_response=json.dumps(extraction),
            processing_time_ms=processing_time_ms,
            bbox_evidence=extraction.get("bbox_evidence"),
        )

        if doc.document_type in LINE_ITEM_TYPES:
            update_document_status(document_id, DocumentStatus.RECONCILIATION)
            from workers.reconciliation.tasks import reconcile_document
            reconcile_document.apply_async(
                args=[document_id, tenant_id, priority],
                priority=priority,
            )
        else:
            update_document_status(document_id, DocumentStatus.VALIDATING)
            from workers.validation.tasks import validate_document
            validate_document.apply_async(
                args=[document_id, tenant_id, priority],
                priority=priority,
            )

        processing_time = time.time() - start_time
        write_audit_event(
            tenant_id, "pipeline.structuring.completed", document_id,
            actor="worker:structuring",
            payload={
                "document_type": doc.document_type.value,
                "fields_extracted": len(extraction.get("extracted_data", {})),
                "schema_override_applied": schema_override is not None,
                "processing_time_s": round(processing_time, 2),
            },
        )

        logger.info(
            f"Structuring completed for document {document_id} "
            f"in {processing_time:.2f}s"
        )

        return {
            "document_id": document_id,
            "status": "completed",
            "document_type": doc.document_type.value,
            "fields_extracted": len(extraction.get("extracted_data", {})),
            "field_confidences": extraction.get("field_confidences", {}),
            "notes": extraction.get("extraction_notes", ""),
            "processing_time": processing_time,
        }

    except Exception as exc:
        logger.exception(f"Structuring failed for document {document_id}")
        update_document_status(
            document_id,
            DocumentStatus.STRUCTURING_FAILED,
            error_message=str(exc),
        )

        raise self.retry(exc=exc, countdown=60, max_retries=3)
