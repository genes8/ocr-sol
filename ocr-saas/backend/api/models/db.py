"""SQLAlchemy database models for OCR SaaS."""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.core.database import Base


class DocumentStatus(str, PyEnum):
    """Document processing status."""

    PENDING = "pending"
    PREPROCESSING = "preprocessing"
    PREPROCESS_FAILED = "preprocess_failed"
    OCR = "ocr"
    OCR_FAILED = "ocr_failed"
    CLASSIFIED = "classified"
    STRUCTURING = "structuring"
    STRUCTURING_FAILED = "structuring_failed"
    RECONCILIATION = "reconciliation"
    RECONCILIATION_FAILED = "reconciliation_failed"
    VALIDATING = "validating"
    VALIDATION_FAILED = "validation_failed"
    COMPLETED = "completed"
    REVIEW = "review"
    MANUAL_REVIEW = "manual_review"


class DocumentType(str, PyEnum):
    """Document type enum."""

    INVOICE = "invoice"
    PROFORMA = "proforma"
    DELIVERY_NOTE = "delivery_note"
    CONTRACT = "contract"
    BANK_STATEMENT = "bank_statement"
    OFFICIAL_DOCUMENT = "official_document"


class Decision(str, PyEnum):
    """Final processing decision."""

    AUTO = "auto"
    REVIEW = "review"
    MANUAL = "manual"


class Tenant(Base):
    """Tenant model for multi-tenancy."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="tenant", lazy="selectin"
    )
    api_keys: Mapped[list["APIKey"]] = relationship(
        "APIKey", back_populates="tenant", lazy="selectin"
    )
    webhooks: Mapped[list["Webhook"]] = relationship(
        "Webhook", back_populates="tenant", lazy="selectin"
    )
    suppliers: Mapped[list["Supplier"]] = relationship(
        "Supplier", back_populates="tenant", lazy="selectin"
    )


class APIKey(Base):
    """API Key model for programmatic access."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="api_keys")


class Document(Base):
    """Document model for uploaded documents."""

    __tablename__ = "documents"
    __table_args__ = (
        Index("idx_documents_tenant", "tenant_id"),
        Index("idx_documents_status", "status"),
        Index("idx_documents_created", "created_at"),
        Index("idx_documents_type", "document_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), default=DocumentStatus.PENDING
    )
    document_type: Mapped[DocumentType | None] = mapped_column(
        Enum(DocumentType), nullable=True
    )
    decision: Mapped[Decision | None] = mapped_column(Enum(Decision), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    doc_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    processing_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="documents")
    files: Mapped[list["DocumentFile"]] = relationship(
        "DocumentFile", back_populates="document", lazy="selectin"
    )
    ocr_result: Mapped["OCRResult | None"] = relationship(
        "OCRResult", back_populates="document", uselist=False
    )
    structured_result: Mapped["StructuredResult | None"] = relationship(
        "StructuredResult", back_populates="document", uselist=False
    )
    reconciliation_log: Mapped["ReconciliationLog | None"] = relationship(
        "ReconciliationLog", back_populates="document", uselist=False
    )


class DocumentFile(Base):
    """Document file storage information."""

    __tablename__ = "document_files"
    __table_args__ = (
        Index("idx_document_files_doc", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False
    )
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)  # original, processed, thumbnail
    page_number: Mapped[int | None] = mapped_column(Integer)
    minio_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    document: Mapped["Document"] = relationship("Document", back_populates="files")


class OCRResult(Base):
    """OCR extraction results."""

    __tablename__ = "ocr_results"
    __table_args__ = (
        Index("idx_ocr_results_doc", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False
    )
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    text_blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    model_version: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    document: Mapped["Document"] = relationship("Document", back_populates="ocr_result")


class StructuredResult(Base):
    """LLM structured extraction results."""

    __tablename__ = "structured_results"
    __table_args__ = (
        Index("idx_structured_results_doc", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False
    )
    document_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType), nullable=False)
    extracted_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    field_confidences: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    raw_llm_response: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(String(100))
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    # Feature 2: bbox evidence — maps field names to source OCR text blocks
    bbox_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Feature 4: supplier lookup result from validation worker
    supplier_lookup_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    document: Mapped["Document"] = relationship(
        "Document", back_populates="structured_result"
    )


class ReconciliationLog(Base):
    """Line item reconciliation results."""

    __tablename__ = "reconciliation_logs"
    __table_args__ = (
        Index("idx_reconciliation_logs_doc", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=False
    )
    line_items_count: Mapped[int] = mapped_column(Integer, nullable=False)
    extracted_subtotal: Mapped[float | None] = mapped_column(Numeric(15, 2))
    calculated_subtotal: Mapped[float | None] = mapped_column(Numeric(15, 2))
    extracted_vat: Mapped[float | None] = mapped_column(Numeric(15, 2))
    calculated_vat: Mapped[float | None] = mapped_column(Numeric(15, 2))
    extracted_total: Mapped[float | None] = mapped_column(Numeric(15, 2))
    calculated_total: Mapped[float | None] = mapped_column(Numeric(15, 2))
    subtotal_match: Mapped[bool | None] = mapped_column(Boolean)
    vat_match: Mapped[bool | None] = mapped_column(Boolean)
    total_match: Mapped[bool | None] = mapped_column(Boolean)
    reconciliation_status: Mapped[str] = mapped_column(String(20), nullable=False)  # pass, warn, fail
    discrepancy_details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    document: Mapped["Document"] = relationship(
        "Document", back_populates="reconciliation_log"
    )


class Webhook(Base):
    """Webhook configuration for notifications."""

    __tablename__ = "webhooks"
    __table_args__ = (
        Index("idx_webhooks_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    events: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    headers: Mapped[dict[str, str] | None] = mapped_column(JSON)
    retry_count: Mapped[int] = mapped_column(Integer, default=3)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, default=60)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="webhooks")


class Supplier(Base):
    """Supplier (dobavljač) model for per-tenant supplier registry."""

    __tablename__ = "suppliers"
    __table_args__ = (
        Index("idx_suppliers_tenant", "tenant_id"),
        UniqueConstraint("tenant_id", "pib", name="uq_supplier_tenant_pib"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    pib: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mb: Mapped[str | None] = mapped_column(String(20), nullable=True)
    iban: Mapped[str | None] = mapped_column(String(50), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="suppliers")


class WebhookDelivery(Base):
    """Webhook delivery log."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("idx_webhook_deliveries_webhook", "webhook_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("webhooks.id"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
