"""Document management API routes."""

import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

logger = logging.getLogger(__name__)

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from api.core.audit import write_audit
from api.core.config import settings
from api.core.database import get_db
from api.core.security import get_current_tenant, require_role
from api.core.storage import get_minio_client, get_presigned_url
from api.models.db import (
    AuditLog,
    Decision,
    Document,
    DocumentFile,
    DocumentStatus,
    DocumentType,
    OCRResult,
    StructuredResult,
    Tenant,
)
from api.routes.schemas import (
    AuditLogEntry,
    DocumentCreate,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatusResponse,
    DocumentTypeEnum,
    FieldCorrectionRequest,
    FieldCorrectionResponse,
    StatusEnum,
    UpdateDocumentRequest,
)

router = APIRouter()


@router.get("/stats")
async def get_document_stats(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict:
    """Return document processing statistics and quota usage for the current tenant."""
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Total documents this month
    monthly_result = await db.execute(
        select(func.count()).select_from(Document).where(
            Document.tenant_id == tenant_id,
            Document.created_at >= month_start,
        )
    )
    monthly_count = monthly_result.scalar() or 0

    # Per-status breakdown
    status_rows = await db.execute(
        select(Document.status, func.count().label("cnt"))
        .where(Document.tenant_id == tenant_id)
        .group_by(Document.status)
    )
    by_status = {row.status.value: row.cnt for row in status_rows}

    # Per-decision breakdown
    decision_rows = await db.execute(
        select(Document.decision, func.count().label("cnt"))
        .where(Document.tenant_id == tenant_id, Document.decision.is_not(None))
        .group_by(Document.decision)
    )
    by_decision = {row.decision.value: row.cnt for row in decision_rows}

    # In-flight count
    _in_flight = [
        DocumentStatus.PENDING, DocumentStatus.PREPROCESSING, DocumentStatus.OCR,
        DocumentStatus.CLASSIFIED, DocumentStatus.STRUCTURING,
        DocumentStatus.RECONCILIATION, DocumentStatus.VALIDATING,
    ]
    in_flight_result = await db.execute(
        select(func.count()).select_from(Document).where(
            Document.tenant_id == tenant_id,
            Document.status.in_(_in_flight),
        )
    )
    in_flight = in_flight_result.scalar() or 0

    # Pending review
    review_result = await db.execute(
        select(func.count()).select_from(Document).where(
            Document.tenant_id == tenant_id,
            Document.status.in_([DocumentStatus.REVIEW, DocumentStatus.MANUAL_REVIEW]),
        )
    )
    pending_review = review_result.scalar() or 0

    # Tenant quota info
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    tenant_settings = (tenant.settings or {}) if tenant else {}

    return {
        "tenant_id": tenant_id,
        "period": {
            "month_start": month_start.isoformat(),
            "documents_this_month": monthly_count,
            "monthly_limit": tenant_settings.get("max_documents_per_month"),
        },
        "in_flight": in_flight,
        "concurrent_limit": tenant_settings.get(
            "max_concurrent_processing", settings.DEFAULT_MAX_CONCURRENT_DOCS
        ),
        "pending_review": pending_review,
        "by_status": by_status,
        "by_decision": by_decision,
    }


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = require_role("admin", "reviewer"),
) -> DocumentResponse:
    """Upload a document for processing."""
    if file.size and file.size > settings.MAX_FILE_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds maximum allowed ({settings.MAX_FILE_SIZE_MB}MB)",
        )

    if file.content_type not in settings.ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File type not supported: {file.content_type}",
        )

    # Fetch tenant once — reused for quota, concurrency, and priority checks
    # Lock the tenant row to prevent TOCTOU race on quota check
    tenant_for_quota = await db.execute(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    tenant_quota = tenant_for_quota.scalar_one_or_none()
    tenant_settings = (tenant_quota.settings or {}) if tenant_quota else {}

    # Gap2: Per-tenant monthly document quota check
    monthly_limit = tenant_settings.get("max_documents_per_month")
    if monthly_limit:
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        count_result = await db.execute(
            select(func.count()).select_from(Document).where(
                Document.tenant_id == tenant_id,
                Document.created_at >= month_start,
            )
        )
        if (count_result.scalar() or 0) >= monthly_limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Monthly document limit reached",
            )

    # Per-tenant concurrent processing limit (documents currently in-flight)
    max_concurrent = tenant_settings.get(
        "max_concurrent_processing", settings.DEFAULT_MAX_CONCURRENT_DOCS
    )
    _in_flight_statuses = [
        DocumentStatus.PENDING, DocumentStatus.PREPROCESSING, DocumentStatus.OCR,
        DocumentStatus.CLASSIFIED, DocumentStatus.STRUCTURING,
        DocumentStatus.RECONCILIATION, DocumentStatus.VALIDATING,
    ]
    in_flight_result = await db.execute(
        select(func.count()).select_from(Document).where(
            Document.tenant_id == tenant_id,
            Document.status.in_(_in_flight_statuses),
        )
    )
    if (in_flight_result.scalar() or 0) >= max_concurrent:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Concurrent processing limit reached — retry after current documents finish",
        )

    document_id = uuid.uuid4()
    original_filename = file.filename or "unknown"
    
    file_extension = original_filename.split(".")[-1].lower() if "." in original_filename else ""
    stored_filename = f"{document_id}.{file_extension}"

    minio_path = f"documents/{tenant_id}/{stored_filename}"

    file_content = await file.read()
    try:
        get_minio_client().put_object(
            bucket_name=settings.MINIO_BUCKET_DOCUMENTS,
            object_name=minio_path,
            data=io.BytesIO(file_content),
            length=len(file_content),
            content_type=file.content_type,
        )
    except Exception as e:
        logger.error("MinIO upload failed for document %s: %s", document_id, e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Storage upload failed")

    document = Document(
        id=document_id,
        tenant_id=tenant_id,
        filename=stored_filename,
        original_filename=original_filename,
        content_type=file.content_type,
        file_size=len(file_content),
        status=DocumentStatus.PENDING,
    )
    db.add(document)

    doc_file = DocumentFile(
        document_id=document_id,
        file_type="original",
        minio_path=minio_path,
        file_size=len(file_content),
    )
    db.add(doc_file)

    await write_audit(
        db, tenant_id, "document.uploaded",
        document_id=document_id,
        actor=f"api:{tenant_id}",
        payload={"filename": original_filename, "file_size": len(file_content)},
    )
    await db.commit()
    await db.refresh(document)

    # Feature 5: determine priority based on tenant plan
    priority = (
        settings.ENTERPRISE_TASK_PRIORITY
        if tenant_settings.get("plan") == "enterprise"
        else settings.STANDARD_TASK_PRIORITY
    )

    # Trigger document processing
    from workers.preprocessing.tasks import process_document
    process_document.apply_async(
        args=[str(document_id), str(tenant_id), priority],
        priority=priority,
    )

    return DocumentResponse(
        id=document.id,
        tenant_id=document.tenant_id,
        filename=document.filename,
        original_filename=document.original_filename,
        content_type=document.content_type,
        file_size=document.file_size,
        status=StatusEnum(document.status.value),
        created_at=document.created_at,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status_filter: StatusEnum | None = Query(None, alias="status"),
    document_type: DocumentTypeEnum | None = None,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> DocumentListResponse:
    """List documents for the current tenant."""
    query = select(Document).where(Document.tenant_id == tenant_id)

    if status_filter:
        query = query.where(Document.status == DocumentStatus(status_filter.value))
    if document_type:
        query = query.where(Document.document_type == DocumentType(document_type.value))

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(Document.created_at.desc()).offset(skip).limit(limit)
    query = query.options(selectinload(Document.structured_result))

    result = await db.execute(query)
    documents = result.scalars().all()

    items = []
    for doc in documents:
        items.append(
            DocumentResponse(
                id=doc.id,
                tenant_id=doc.tenant_id,
                filename=doc.filename,
                original_filename=doc.original_filename,
                content_type=doc.content_type,
                file_size=doc.file_size,
                page_count=doc.page_count,
                status=StatusEnum(doc.status.value),
                document_type=DocumentTypeEnum(doc.document_type.value) if doc.document_type else None,
                decision=doc.decision.value if doc.decision else None,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
            )
        )

    return DocumentListResponse(total=total, skip=skip, limit=limit, items=items)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> DocumentResponse:
    """Get a specific document by ID."""
    query = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    query = query.options(
        selectinload(Document.files),
        selectinload(Document.ocr_result),
        selectinload(Document.structured_result),
        selectinload(Document.reconciliation_log),
    )

    result = await db.execute(query)
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return DocumentResponse(
        id=document.id,
        tenant_id=document.tenant_id,
        filename=document.filename,
        original_filename=document.original_filename,
        content_type=document.content_type,
        file_size=document.file_size,
        page_count=document.page_count,
        status=StatusEnum(document.status.value),
        document_type=DocumentTypeEnum(document.document_type.value) if document.document_type else None,
        decision=document.decision.value if document.decision else None,
        error_message=document.error_message,
        metadata=document.doc_metadata,
        processing_started_at=document.processing_started_at,
        processing_completed_at=document.processing_completed_at,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> DocumentStatusResponse:
    """Get the current processing status of a document."""
    query = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )

    result = await db.execute(query)
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return DocumentStatusResponse(
        id=document.id,
        status=StatusEnum(document.status.value),
        document_type=DocumentTypeEnum(document.document_type.value) if document.document_type else None,
        decision=document.decision.value if document.decision else None,
        error_message=document.error_message,
        processing_started_at=document.processing_started_at,
        processing_completed_at=document.processing_completed_at,
    )


@router.get("/{document_id}/result")
async def get_document_result(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict:
    """Get the structured extraction result for a document."""
    query = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    query = query.options(
        selectinload(Document.ocr_result),
        selectinload(Document.structured_result),
        selectinload(Document.reconciliation_log),
    )

    result = await db.execute(query)
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if document.status != DocumentStatus.COMPLETED and document.status != DocumentStatus.REVIEW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document processing not completed. Current status: {document.status.value}",
        )

    return {
        "document_id": document.id,
        "status": document.status.value,
        "document_type": document.document_type.value if document.document_type else None,
        "decision": document.decision.value if document.decision else None,
        "ocr_result": (
            {
                "full_text": document.ocr_result.full_text,
                "text_blocks": document.ocr_result.text_blocks,
                "page_count": document.ocr_result.page_count,
            }
            if document.ocr_result
            else None
        ),
        "structured_data": (
            {
                "extracted_data": document.structured_result.extracted_data,
                "field_confidences": document.structured_result.field_confidences,
                "document_type": document.structured_result.document_type.value,
                "bbox_evidence": document.structured_result.bbox_evidence,
            }
            if document.structured_result
            else None
        ),
        "reconciliation": (
            {
                "status": document.reconciliation_log.reconciliation_status,
                "subtotal_match": document.reconciliation_log.subtotal_match,
                "vat_match": document.reconciliation_log.vat_match,
                "total_match": document.reconciliation_log.total_match,
                "discrepancy_details": document.reconciliation_log.discrepancy_details,
            }
            if document.reconciliation_log
            else None
        ),
    }


@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: uuid.UUID,
    update_data: UpdateDocumentRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = require_role("admin", "reviewer"),
) -> DocumentResponse:
    """Update a document (e.g., manual corrections during review)."""
    query = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    query = query.options(
        selectinload(Document.structured_result),
    )

    result = await db.execute(query)
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    if update_data.decision is not None:
        new_decision = Decision(update_data.decision)
        document.decision = new_decision
        # Approve (AUTO) → mark COMPLETED; Reject (MANUAL) → leave in MANUAL_REVIEW
        if new_decision == Decision.AUTO and document.status in (
            DocumentStatus.REVIEW, DocumentStatus.MANUAL_REVIEW
        ):
            document.status = DocumentStatus.COMPLETED
            document.processing_completed_at = datetime.now(timezone.utc)
        await write_audit(
            db, tenant_id, "document.decision_override",
            document_id=document_id,
            actor=f"api:{tenant_id}",
            payload={"decision": update_data.decision, "new_status": document.status.value},
        )

    if update_data.document_type is not None:
        document.document_type = DocumentType(update_data.document_type.value)

    if update_data.metadata is not None:
        document.doc_metadata = update_data.metadata

    document.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(document)

    return DocumentResponse(
        id=document.id,
        tenant_id=document.tenant_id,
        filename=document.filename,
        original_filename=document.original_filename,
        content_type=document.content_type,
        file_size=document.file_size,
        page_count=document.page_count,
        status=StatusEnum(document.status.value),
        document_type=DocumentTypeEnum(document.document_type.value) if document.document_type else None,
        decision=document.decision.value if document.decision else None,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.patch("/{document_id}/fields", response_model=FieldCorrectionResponse)
async def update_document_fields(
    document_id: uuid.UUID,
    data: FieldCorrectionRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> FieldCorrectionResponse:
    """Apply field-level corrections to extracted data."""
    import copy

    query = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    ).options(selectinload(Document.structured_result))

    result = await db.execute(query)
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    structured_result = document.structured_result
    if not structured_result:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No structured result found for this document",
        )

    extracted = copy.deepcopy(structured_result.extracted_data or {})
    confs = dict(structured_result.field_confidences or {})

    MAX_FIELD_DEPTH = 4
    for field_path, value in data.fields.items():
        parts = field_path.split(".")
        if len(parts) > MAX_FIELD_DEPTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Field path too deep: {field_path}",
            )
        for part in parts:
            if not part.isidentifier():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid field name: {part}",
                )
        target = extracted
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
        confs[field_path] = 1.0

    structured_result.extracted_data = extracted
    structured_result.field_confidences = confs
    flag_modified(structured_result, "extracted_data")
    flag_modified(structured_result, "field_confidences")

    await write_audit(
        db, tenant_id, "document.field_corrected",
        document_id=document_id,
        actor=f"api:{tenant_id}",
        payload={"fields": list(data.fields.keys())},
    )
    await db.commit()

    return FieldCorrectionResponse(
        document_id=document_id,
        updated_fields=list(data.fields.keys()),
        structured_result_id=structured_result.id,
    )


@router.get("/{document_id}/audit", response_model=list[AuditLogEntry])
async def get_document_audit(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> list[AuditLogEntry]:
    """Get the audit trail for a document."""
    doc_query = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    doc_result = await db.execute(doc_query)
    if not doc_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    audit_query = (
        select(AuditLog)
        .where(AuditLog.document_id == document_id)
        .order_by(AuditLog.created_at)
    )
    result = await db.execute(audit_query)
    entries = result.scalars().all()

    return [AuditLogEntry.model_validate(e) for e in entries]


@router.get("/{document_id}/pages/{page}/image")
async def get_document_page_image(
    document_id: uuid.UUID,
    page: int,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict:
    """Get a presigned URL for a document page image (for Review UI bbox overlay)."""
    # Verify tenant ownership first to prevent IDOR
    doc_query = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    doc_result = await db.execute(doc_query)
    if not doc_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    query = select(DocumentFile).where(
        DocumentFile.document_id == document_id,
        DocumentFile.page_number == page,
        DocumentFile.file_type == "processed",
    )
    result = await db.execute(query)
    doc_file = result.scalar_one_or_none()

    if not doc_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Page {page} not found for document {document_id}",
        )

    url = get_presigned_url(doc_file.minio_path, expiry=settings.PRESIGNED_URL_EXPIRY_SECONDS)
    return {"url": url, "page": page, "width": doc_file.width, "height": doc_file.height}


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = require_role("admin"),
) -> None:
    """Delete a document and its associated files."""
    query = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    query = query.options(selectinload(Document.files))

    result = await db.execute(query)
    document = result.scalar_one_or_none()

    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    for file in document.files:
        try:
            get_minio_client().remove_object(
                bucket_name=settings.MINIO_BUCKET_DOCUMENTS,
                object_name=file.minio_path,
            )
        except Exception:
            pass

    await db.delete(document)
    await db.commit()
