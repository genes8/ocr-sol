"""Integration tests — pipeline stage idempotency (Task 21).

Requires a live PostgreSQL database. Run with:
  pytest tests/integration/ -m integration

Set DATABASE_URL env var to point at a test database.
"""

import uuid
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_session():
    """Async DB session against the real test database."""
    from api.core.database import get_db_session
    session = await get_db_session()
    try:
        yield session
    finally:
        await session.rollback()  # Roll back all changes after test
        await session.close()


@pytest.fixture
async def test_tenant(db_session):
    from api.models.db import Tenant
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Test Tenant",
        api_key_hash="test-hash",
        settings={},
    )
    db_session.add(tenant)
    await db_session.flush()
    return tenant


@pytest.fixture
async def test_document(db_session, test_tenant):
    from api.models.db import Document, DocumentStatus
    doc = Document(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        filename="test.pdf",
        original_filename="test.pdf",
        content_type="application/pdf",
        file_size=1024,
        status=DocumentStatus.OCR,
    )
    db_session.add(doc)
    await db_session.flush()
    return doc


@pytest.mark.asyncio
async def test_ocr_result_upsert_no_duplicate(db_session, test_document):
    """Inserting OCR result twice produces exactly 1 row."""
    from sqlalchemy import select, func
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from api.models.db import OCRResult

    doc_id = test_document.id

    def _upsert():
        return (
            pg_insert(OCRResult)
            .values(
                id=uuid.uuid4(),
                document_id=doc_id,
                full_text="Sample text",
                text_blocks=[{"text": "Sample", "bbox": {}}],
                page_count=1,
                processing_time_ms=500,
                model_version="test-v1",
            )
            .on_conflict_do_update(
                constraint="uq_ocr_results_document",
                set_={
                    "full_text": "Sample text updated",
                    "text_blocks": [{"text": "Sample updated", "bbox": {}}],
                    "page_count": 1,
                    "processing_time_ms": 600,
                    "model_version": "test-v1",
                },
            )
            .returning(OCRResult.id)
        )

    # First insert
    result1 = await db_session.execute(_upsert())
    id1 = result1.scalar_one()

    # Second insert (simulated retry)
    result2 = await db_session.execute(_upsert())
    id2 = result2.scalar_one()

    # Verify exactly one row exists
    count_result = await db_session.execute(
        select(func.count()).select_from(OCRResult).where(
            OCRResult.document_id == doc_id
        )
    )
    count = count_result.scalar()
    assert count == 1, f"Expected 1 OCR result, got {count}"


@pytest.mark.asyncio
async def test_structured_result_upsert_no_duplicate(db_session, test_document):
    """Inserting StructuredResult twice produces exactly 1 row."""
    from sqlalchemy import select, func
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from api.models.db import StructuredResult

    doc_id = test_document.id

    def _upsert(notes: str):
        return (
            pg_insert(StructuredResult)
            .values(
                id=uuid.uuid4(),
                document_id=doc_id,
                extracted_data={"invoice_number": "001"},
                field_confidences={"invoice_number": 0.95},
                document_type="invoice",
                extraction_notes=notes,
                model_version="test-v1",
                bbox_evidence={},
            )
            .on_conflict_do_update(
                constraint="uq_structured_results_document",
                set_={
                    "extracted_data": {"invoice_number": "002"},
                    "extraction_notes": notes,
                },
            )
            .returning(StructuredResult.id)
        )

    await db_session.execute(_upsert("first"))
    await db_session.execute(_upsert("second"))

    count_result = await db_session.execute(
        select(func.count()).select_from(StructuredResult).where(
            StructuredResult.document_id == doc_id
        )
    )
    assert count_result.scalar() == 1


@pytest.mark.asyncio
async def test_reconciliation_upsert_no_duplicate(db_session, test_document, test_tenant):
    """ReconciliationLog upsert is idempotent."""
    from sqlalchemy import select, func
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from api.models.db import ReconciliationLog

    doc_id = test_document.id

    def _upsert(status: str):
        return (
            pg_insert(ReconciliationLog)
            .values(
                id=uuid.uuid4(),
                document_id=doc_id,
                tenant_id=test_tenant.id,
                reconciliation_status=status,
                discrepancy_details={},
            )
            .on_conflict_do_update(
                constraint="uq_reconciliation_logs_document",
                set_={
                    "reconciliation_status": status,
                    "discrepancy_details": {},
                },
            )
            .returning(ReconciliationLog.id)
        )

    await db_session.execute(_upsert("pass"))
    await db_session.execute(_upsert("warn"))  # Update, not insert

    count_result = await db_session.execute(
        select(func.count()).select_from(ReconciliationLog).where(
            ReconciliationLog.document_id == doc_id
        )
    )
    assert count_result.scalar() == 1
