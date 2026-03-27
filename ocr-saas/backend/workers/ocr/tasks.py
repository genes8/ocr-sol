"""OCR worker tasks - vLLM GLM-OCR integration."""

import base64
import hashlib
import io
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any

import requests
from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from api.core.config import settings
from api.core.database import SyncSessionLocal
from api.core.storage import get_minio_client
from api.models.db import AuditLog, Document, DocumentStatus, OCRResult
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
        session.add(
            AuditLog(
                tenant_id=uuid.UUID(tenant_id),
                document_id=uuid.UUID(document_id) if document_id else None,
                actor=actor,
                event=event,
                payload=payload,
            )
        )
        session.commit()
    finally:
        session.close()


logger = logging.getLogger(__name__)


def get_sync_session() -> Session:
    """Get sync database session for Celery workers."""
    return SyncSessionLocal()


def update_document_status(
    document_id: str,
    status: DocumentStatus,
    error_message: str | None = None,
) -> None:
    """Update document status in database."""
    session = get_sync_session()
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


def get_processed_pages(
    document_id: str, tenant_id: str
) -> list[tuple[int, Image.Image]]:
    """Get processed page images from MinIO.

    Returns:
        List of (page_number, PIL.Image) tuples
    """
    client = get_minio_client()
    bucket = settings.MINIO_BUCKET_DOCUMENTS

    # List all processed images for this document
    prefix = f"{tenant_id}/{document_id}/"

    pages = []
    try:
        objects = client.list_objects(bucket, prefix=prefix, recursive=True)
        for obj in objects:
            if "processed" in obj.object_name and obj.object_name.endswith(".jpg"):
                # Extract page number from filename
                filename = obj.object_name.split("/")[-1]
                if filename.startswith("p") and "_processed" in filename:
                    try:
                        page_num = int(filename.split("_")[0][1:])
                    except (IndexError, ValueError):
                        logger.warning("Skipping file with unexpected name: %s", filename)
                        continue

                    # Get the image
                    result = client.get_object(bucket, obj.object_name)
                    img_data = result.read()
                    result.close()
                    result.release_conn()

                    img = Image.open(io.BytesIO(img_data))
                    pages.append((page_num, img))
    except Exception as e:
        logger.error(f"Failed to list processed pages: {e}")

    return sorted(pages, key=lambda x: x[0])


def save_ocr_result(
    document_id: str,
    full_text: str,
    text_blocks: list[dict[str, Any]],
    page_count: int,
    processing_time_ms: int,
) -> str:
    """Save OCR result to database (upsert — idempotent per document).

    Returns:
        OCR result ID
    """
    session = get_sync_session()
    try:
        new_id = uuid.uuid4()
        stmt = (
            pg_insert(OCRResult)
            .values(
                id=new_id,
                document_id=uuid.UUID(document_id),
                full_text=full_text,
                text_blocks=text_blocks,
                page_count=page_count,
                processing_time_ms=processing_time_ms,
                model_version=settings.VLLM_MODEL_NAME,
            )
            .on_conflict_do_update(
                constraint="uq_ocr_results_document",
                set_={
                    "full_text": full_text,
                    "text_blocks": text_blocks,
                    "page_count": page_count,
                    "processing_time_ms": processing_time_ms,
                    "model_version": settings.VLLM_MODEL_NAME,
                },
            )
            .returning(OCRResult.id)
        )
        result = session.execute(stmt)
        session.commit()
        return str(result.scalar_one())
    finally:
        session.close()


def call_glm_ocr(image: Image.Image) -> dict[str, Any]:
    """Call GLM-OCR via vLLM API.

    Args:
        image: PIL Image to process

    Returns:
        OCR result with text and bounding boxes
    """
    # Convert image to base64
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_b64 = base64.b64encode(buffer.getvalue()).decode()

    # Prepare request to vLLM
    url = f"{settings.VLLM_BASE_URL}/v1/chat/completions"

    payload = {
        "model": settings.VLLM_MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                    {
                        "type": "text",
                        "text": """Extract all text from this document with their bounding box coordinates.
Return a JSON object with the following structure:
{
    "text_blocks": [
        {
            "text": "extracted text",
            "bbox": {"x1": 0, "y1": 0, "x2": 100, "y2": 50},
            "confidence": 0.95,
            "block_type": "text"
        }
    ],
    "full_text": "all extracted text combined"
}

block_type must be one of: text, table_cell, header, footer, logo, stamp, signature""",
                    },
                ],
            }
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
            timeout=settings.VLLM_TIMEOUT,
        )
        response.raise_for_status()

        result = response.json()
        content = result["choices"][0]["message"]["content"]
        return json.loads(strip_llm_fences(content))

    except requests.exceptions.RequestException as e:
        logger.error(f"vLLM API request failed: {e}")
        raise


def fallback_ocr(image: Image.Image) -> dict[str, Any]:
    """Fallback OCR using Tesseract.

    This provides actual text extraction when vLLM is not available.
    """
    import pytesseract
    import tempfile
    import os

    try:
        # Convert to RGB if needed
        if image.mode != "RGB":
            image = image.convert("RGB")

        # Save to temp file for Tesseract (more reliable)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image.save(tmp, format="PNG")
            tmp_path = tmp.name

        try:
            # Run Tesseract OCR with Serbian (Cyrillic + Latin) + English
            lang = "srp+srp_latn+eng"
            text = pytesseract.image_to_string(tmp_path, lang=lang)
            data = pytesseract.image_to_data(
                tmp_path,
                lang=lang,
                output_type=pytesseract.Output.DICT,
            )
        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except OSError as e:
                logger.debug("Failed to delete temp file %s: %s", tmp_path, e)

        # Build text blocks with bounding boxes
        text_blocks = []
        n_boxes = len(data["text"])

        for i in range(n_boxes):
            t = data["text"][i].strip()
            if t:  # Only include non-empty text
                text_blocks.append(
                    {
                        "text": t,
                        "bbox": {
                            "x1": int(data["left"][i]),
                            "y1": int(data["top"][i]),
                            "x2": int(data["left"][i] + data["width"][i]),
                            "y2": int(data["top"][i] + data["height"][i]),
                        },
                        "confidence": float(data["conf"][i]) / 100
                        if data["conf"][i] != -1
                        else 0.5,
                        "block_type": "text",
                    }
                )

        # Estimate overall confidence from Tesseract confidence
        valid_confidences = [
            b["confidence"] for b in text_blocks if b["confidence"] > 0
        ]
        avg_confidence = (
            sum(valid_confidences) / len(valid_confidences)
            if valid_confidences
            else 0.5
        )

        logger.info(
            f"Tesseract OCR extracted {len(text_blocks)} text blocks, "
            f"avg confidence: {avg_confidence:.2%}"
        )

        return {
            "text_blocks": text_blocks,
            "full_text": text.strip(),
        }

    except Exception as e:
        logger.warning("fallback_ocr (Tesseract) failed: %s", e)
        return {}


def _stable_block_id(page: int, text: str, bbox: dict) -> str:
    """Deterministic block ID stable across retries.

    Uses SHA-1 of (page, text[:80], bbox coords) so the same physical block
    always gets the same ID regardless of array position or reprocessing order.
    """
    key = f"{page}|{text[:80]}|{bbox.get('x1')}|{bbox.get('y1')}|{bbox.get('x2')}|{bbox.get('y2')}"
    return "blk-" + hashlib.sha1(key.encode()).hexdigest()[:16]


def process_single_page(
    page_num: int,
    image: Image.Image,
) -> dict[str, Any]:
    """Process a single page with OCR.

    Args:
        page_num: Page number
        image: PIL Image

    Returns:
        OCR result for this page
    """
    try:
        result = call_glm_ocr(image)
        return {
            "page": page_num,
            "success": True,
            "text_blocks": result.get("text_blocks", []),
            "full_text": result.get("full_text", ""),
        }
    except Exception as e:
        logger.warning(f"OCR failed for page {page_num}: {e}")
        try:
            result = fallback_ocr(image)
            return {
                "page": page_num,
                "success": False,
                "text_blocks": result.get("text_blocks", []),
                "full_text": result.get("full_text", ""),
                "error": str(e),
            }
        except Exception as fallback_exc:
            logger.warning(
                "All OCR methods failed for page %s: %s", page_num, fallback_exc
            )
            return {}


@celery_app.task(bind=True, name="workers.ocr.tasks.process_ocr")
def process_ocr(
    self, document_id: str, tenant_id: str, priority: int = 5
) -> dict[str, Any]:
    """Process document pages with OCR.

    Args:
        document_id: Document UUID
        tenant_id: Tenant UUID

    Returns:
        OCR processing result
    """
    start_time = time.time()
    logger.info(f"Starting OCR for document {document_id}")

    # Feature flag gate — canary/rollback support
    if not settings.ENABLE_OCR_PIPELINE:
        logger.warning(
            f"OCR pipeline disabled (feature flag). Routing document {document_id} to MANUAL_REVIEW."
        )
        update_document_status(
            document_id,
            DocumentStatus.MANUAL_REVIEW,
            error_message="ocr_pipeline_disabled",
        )
        return {
            "document_id": document_id,
            "status": "manual_review",
            "reason": "ocr_pipeline_disabled",
        }

    try:
        # Update status
        update_document_status(document_id, DocumentStatus.OCR)

        # Get processed pages
        pages = get_processed_pages(document_id, tenant_id)

        if not pages:
            raise ValueError(f"No processed pages found for document {document_id}")

        # Process each page
        page_results = []
        for page_num, image in pages:
            result = process_single_page(page_num, image)
            page_results.append(result)
            logger.info(f"OCR completed for page {page_num}")

        # Combine results
        full_text = "\n\n".join(r["full_text"] for r in page_results)

        # Merge text blocks with page info + stable block IDs
        all_text_blocks = []
        for result in page_results:
            for block in result["text_blocks"]:
                block["page"] = result["page"]
                block["block_id"] = _stable_block_id(
                    result["page"],
                    block.get("text", ""),
                    block.get("bbox", {}),
                )
                all_text_blocks.append(block)

        processing_time_ms = int((time.time() - start_time) * 1000)

        # Save OCR result
        save_ocr_result(
            document_id=document_id,
            full_text=full_text,
            text_blocks=all_text_blocks,
            page_count=len(pages),
            processing_time_ms=processing_time_ms,
        )

        # Trigger classification (propagate priority for Feature 5)
        from workers.classification.tasks import classify_document_task

        classify_document_task.apply_async(
            args=[document_id, tenant_id, priority],
            priority=priority,
        )

        processing_time = time.time() - start_time
        write_audit_event(
            tenant_id,
            "pipeline.ocr.completed",
            document_id,
            actor="worker:ocr",
            payload={
                "pages": len(pages),
                "text_blocks": len(all_text_blocks),
                "processing_time_s": round(processing_time, 2),
            },
        )

        logger.info(
            f"OCR completed for document {document_id} in {processing_time:.2f}s"
        )

        return {
            "document_id": document_id,
            "status": "completed",
            "pages_processed": len(pages),
            "text_blocks_count": len(all_text_blocks),
            "processing_time": processing_time,
            "full_text_length": len(full_text),
        }

    except Exception as exc:
        logger.exception(f"OCR failed for document {document_id}")
        if isinstance(exc, requests.exceptions.Timeout):
            msg = "OCR service timeout"
        elif isinstance(exc, requests.exceptions.ConnectionError):
            msg = "OCR service unavailable"
        elif isinstance(exc, (json.JSONDecodeError, KeyError)):
            msg = "OCR response parse error"
        else:
            msg = "OCR processing failed"
        update_document_status(
            document_id,
            DocumentStatus.OCR_FAILED,
            error_message=msg,
        )

        raise self.retry(exc=exc, countdown=60, max_retries=3)
