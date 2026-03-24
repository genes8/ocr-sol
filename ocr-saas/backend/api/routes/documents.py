"""Document management API routes."""

import io
import uuid
from datetime import datetime
from typing import Annotated

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

from api.core.config import settings
from api.core.database import get_db
from api.core.security import get_current_tenant
from api.core.storage import get_minio_client, get_presigned_url
from api.models.db import (
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
    DocumentCreate,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatusResponse,
    DocumentTypeEnum,
    StatusEnum,
    UpdateDocumentRequest,
)

router = APIRouter()


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
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

    document_id = uuid.uuid4()
    original_filename = file.filename or "unknown"
    
    file_extension = original_filename.split(".")[-1].lower() if "." in original_filename else ""
    stored_filename = f"{document_id}.{file_extension}"

    minio_path = f"documents/{tenant_id}/{stored_filename}"

    file_content = await file.read()
    get_minio_client().put_object(
        bucket_name=settings.MINIO_BUCKET_DOCUMENTS,
        object_name=minio_path,
        data=io.BytesIO(file_content),
        length=len(file_content),
        content_type=file.content_type,
    )

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

    await db.commit()
    await db.refresh(document)

    # Feature 5: determine priority based on tenant plan
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    priority = (
        settings.ENTERPRISE_TASK_PRIORITY
        if (tenant and (tenant.settings or {}).get("plan") == "enterprise")
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
    tenant_id: uuid.UUID = Depends(get_current_tenant),
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
        document.decision = Decision(update_data.decision.value)

    if update_data.document_type is not None:
        document.document_type = DocumentType(update_data.document_type.value)

    if update_data.metadata is not None:
        document.doc_metadata = update_data.metadata

    document.updated_at = datetime.utcnow()

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


@router.get("/{document_id}/pages/{page}/image")
async def get_document_page_image(
    document_id: uuid.UUID,
    page: int,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
) -> dict:
    """Get a presigned URL for a document page image (for Review UI bbox overlay)."""
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

    # Verify tenant owns this document
    doc_query = select(Document).where(
        Document.id == document_id,
        Document.tenant_id == tenant_id,
    )
    doc_result = await db.execute(doc_query)
    if not doc_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    url = get_presigned_url(doc_file.minio_path, expiry=3600)
    return {"url": url, "page": page, "width": doc_file.width, "height": doc_file.height}


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_current_tenant),
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
