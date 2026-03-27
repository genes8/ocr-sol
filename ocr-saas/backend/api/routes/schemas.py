"""Pydantic schemas for API request/response models."""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl


class TenantCreate(BaseModel):
    """Schema for creating a new tenant."""

    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=100)


class TenantResponse(BaseModel):
    """Schema for tenant response."""

    id: UUID
    name: str
    slug: str
    email: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    """JWT token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenRefresh(BaseModel):
    """Token refresh request."""

    refresh_token: str


class LoginRequest(BaseModel):
    """Login request."""

    email: EmailStr
    password: str


class StatusEnum(str, Enum):
    """Document status enum values."""
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


class DocumentTypeEnum(str, Enum):
    """Document type enum values."""
    INVOICE = "invoice"
    PROFORMA = "proforma"
    DELIVERY_NOTE = "delivery_note"
    CONTRACT = "contract"
    BANK_STATEMENT = "bank_statement"
    OFFICIAL_DOCUMENT = "official_document"


class DocumentResponse(BaseModel):
    """Document response schema."""

    id: UUID
    tenant_id: UUID
    filename: str
    original_filename: str
    content_type: str
    file_size: int
    page_count: int | None = None
    status: StatusEnum
    document_type: DocumentTypeEnum | None = None
    decision: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] | None = None
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class DocumentCreate(BaseModel):
    """Document creation schema."""

    filename: str
    content_type: str
    file_size: int


class DocumentListResponse(BaseModel):
    """Paginated document list response."""

    total: int
    skip: int
    limit: int
    items: list[DocumentResponse]


class DocumentStatusResponse(BaseModel):
    """Document status response schema."""

    id: UUID
    status: StatusEnum
    document_type: DocumentTypeEnum | None = None
    decision: str | None = None
    error_message: str | None = None
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None


class UpdateDocumentRequest(BaseModel):
    """Schema for updating a document."""

    decision: str | None = None
    document_type: DocumentTypeEnum | None = None
    metadata: dict[str, Any] | None = None


class WebhookCreate(BaseModel):
    """Schema for creating a webhook."""

    name: str = Field(..., min_length=1, max_length=255)
    url: HttpUrl
    events: list[str] = Field(default_factory=lambda: ["document.completed", "document.failed"])
    headers: dict[str, str] | None = None
    retry_count: int = Field(default=3, ge=0, le=10)
    retry_delay_seconds: int = Field(default=60, ge=1)


class WebhookResponse(BaseModel):
    """Webhook response schema."""

    id: UUID
    tenant_id: UUID
    name: str
    url: str
    events: list[str]
    is_active: bool
    headers: dict[str, str] | None = None
    retry_count: int
    retry_delay_seconds: int
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class WebhookListResponse(BaseModel):
    """Paginated webhook list response."""

    total: int
    items: list[WebhookResponse]


class APIKeyCreate(BaseModel):
    """Schema for creating an API key."""

    name: str = Field(..., min_length=1, max_length=255)
    expires_in_days: int | None = Field(default=None, ge=1)


class APIKeyResponse(BaseModel):
    """API key response schema."""

    id: UUID
    tenant_id: UUID
    name: str
    key_prefix: str
    expires_at: datetime | None
    is_active: bool
    last_used_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class APIKeyCreatedResponse(BaseModel):
    """API key created response (includes the full key)."""

    id: UUID
    name: str
    key: str
    key_prefix: str
    expires_at: datetime | None
    created_at: datetime


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str
    timestamp: datetime
    services: dict[str, str] = Field(default_factory=dict)


class SupplierCreate(BaseModel):
    """Schema for creating a supplier."""

    name: str = Field(..., min_length=1, max_length=500)
    pib: str | None = Field(default=None, max_length=20)
    mb: str | None = Field(default=None, max_length=20)
    iban: str | None = Field(default=None, max_length=50)
    address: str | None = None


class SupplierUpdate(BaseModel):
    """Schema for updating a supplier."""

    name: str | None = Field(default=None, min_length=1, max_length=500)
    pib: str | None = Field(default=None, max_length=20)
    mb: str | None = Field(default=None, max_length=20)
    iban: str | None = Field(default=None, max_length=50)
    address: str | None = None
    is_active: bool | None = None


class SupplierResponse(BaseModel):
    """Supplier response schema."""

    id: UUID
    tenant_id: UUID
    name: str
    pib: str | None = None
    mb: str | None = None
    iban: str | None = None
    address: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class SupplierListResponse(BaseModel):
    """Paginated supplier list response."""

    total: int
    skip: int
    limit: int
    items: list[SupplierResponse]


class TenantSettings(BaseModel):
    """Validated tenant settings — rejects unknown keys."""

    model_config = ConfigDict(extra="forbid")

    max_documents_per_month: int | None = None
    max_concurrent_processing: int | None = None
    allowed_document_types: list[str] | None = None
    confidence_thresholds: dict[str, float] | None = None
    plan: str | None = None
    schema_overrides: dict[str, Any] | None = None
    system_prompt: str | None = None


class TenantSettingsUpdate(BaseModel):
    """Schema for updating tenant settings (e.g. plan)."""

    settings: TenantSettings


class FieldCorrectionRequest(BaseModel):
    """Request to correct specific fields in extracted data."""

    fields: dict[str, Any]


class FieldCorrectionResponse(BaseModel):
    """Response after applying field corrections."""

    document_id: UUID
    updated_fields: list[str]
    structured_result_id: UUID


class AuditLogEntry(BaseModel):
    """Single audit log entry."""

    id: UUID
    tenant_id: UUID
    document_id: UUID | None = None
    actor: str
    event: str
    payload: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
