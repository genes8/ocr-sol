"""Structuring worker tasks - LLM JSON extraction."""

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.core.database import get_db_session
from api.models.db import (
    Document,
    DocumentStatus,
    DocumentType,
    OCRResult,
    StructuredResult,
)
from workers.celery_app import celery_app
from workers.llm_utils import strip_llm_fences

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
    import asyncio

    async def _update():
        session = await get_db_session()
        try:
            result = await session.execute(
                select(Document).where(Document.id == uuid.UUID(document_id))
            )
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = status
                doc.error_message = error_message
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
) -> dict[str, Any]:
    """Call Structuring LLM for structured extraction.

    Uses STRUCTURING_LLM_BASE_URL (separate server from GLM-OCR vision model).
    """
    schema = load_schema(document_type)
    prompt = build_extraction_prompt(text, document_type, schema, text_blocks)

    import requests

    # Feature 1: Use dedicated structuring LLM server (not GLM-OCR)
    url = f"{settings.STRUCTURING_LLM_BASE_URL}/v1/chat/completions"

    payload = {
        "model": settings.STRUCTURING_LLM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You are a precise document extraction assistant."},
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


def extract_line_items(text: str) -> list[dict[str, Any]]:
    """Extract line items from invoice text."""
    line_items = []
    lines = text.split("\n")

    for line in lines:
        if any(h in line.lower() for h in ["opis", "description", "količina", "quantity", "cena", "price"]):
            continue

        import re
        numbers = re.findall(r"[\d.,]+", line)
        if len(numbers) >= 2:
            item = {
                "raw_text": line.strip(),
                "values": numbers,
            }
            line_items.append(item)

    return line_items


async def save_structured_result(
    document_id: str,
    document_type: DocumentType,
    extracted_data: dict[str, Any],
    field_confidences: dict[str, float],
    raw_llm_response: str,
    processing_time_ms: int,
    bbox_evidence: dict[str, Any] | None = None,
) -> str:
    """Save structured result to database.

    Returns:
        Structured result ID
    """
    session = await get_db_session()
    try:
        result = StructuredResult(
            id=uuid.uuid4(),
            document_id=uuid.UUID(document_id),
            document_type=document_type,
            extracted_data=extracted_data,
            field_confidences=field_confidences,
            raw_llm_response=raw_llm_response,
            model_version=settings.STRUCTURING_LLM_MODEL_NAME,
            processing_time_ms=processing_time_ms,
            bbox_evidence=bbox_evidence,
        )
        session.add(result)
        await session.commit()
        return str(result.id)
    finally:
        await session.close()


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

    try:
        update_document_status(document_id, DocumentStatus.STRUCTURING)

        import asyncio

        async def _get_doc_and_ocr():
            session = await get_db_session()
            try:
                doc_result = await session.execute(
                    select(Document).where(Document.id == uuid.UUID(document_id))
                )
                doc = doc_result.scalar_one_or_none()

                if not doc or not doc.document_type:
                    raise ValueError(f"Document {document_id} not found or not classified")

                ocr_result = await session.execute(
                    select(OCRResult).where(
                        OCRResult.document_id == uuid.UUID(document_id)
                    )
                )
                ocr = ocr_result.scalar_one_or_none()

                if not ocr:
                    raise ValueError(f"No OCR result for document {document_id}")

                return doc, ocr
            finally:
                await session.close()

        doc, ocr_result = asyncio.run(_get_doc_and_ocr())

        # Feature 2: Pass text_blocks so LLM can reference them for bbox evidence
        extraction = call_llm_for_extraction(
            ocr_result.full_text,
            doc.document_type,
            text_blocks=ocr_result.text_blocks,
        )

        LINE_ITEM_TYPES = {DocumentType.INVOICE, DocumentType.PROFORMA, DocumentType.DELIVERY_NOTE}
        if doc.document_type in LINE_ITEM_TYPES:
            line_items = extract_line_items(ocr_result.full_text)
            extraction["extracted_data"]["line_items"] = line_items

        processing_time_ms = int((time.time() - start_time) * 1000)

        asyncio.run(save_structured_result(
            document_id=document_id,
            document_type=doc.document_type,
            extracted_data=extraction.get("extracted_data", {}),
            field_confidences=extraction.get("field_confidences", {}),
            raw_llm_response=json.dumps(extraction),
            processing_time_ms=processing_time_ms,
            bbox_evidence=extraction.get("bbox_evidence"),
        ))

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
