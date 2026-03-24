"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enums
    documentstatus = postgresql.ENUM(
        "pending", "preprocessing", "preprocess_failed", "ocr", "ocr_failed",
        "classified", "structuring", "structuring_failed", "reconciliation",
        "reconciliation_failed", "validating", "validation_failed", "completed",
        "review", "manual_review",
        name="documentstatus",
    )
    documenttype = postgresql.ENUM(
        "invoice", "proforma", "delivery_note", "contract", "bank_statement",
        "official_document",
        name="documenttype",
    )
    decision = postgresql.ENUM("auto", "review", "manual", name="decision")
    documentstatus.create(op.get_bind(), checkfirst=True)
    documenttype.create(op.get_bind(), checkfirst=True)
    decision.create(op.get_bind(), checkfirst=True)

    # tenants
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("settings", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )

    # api_keys
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False, unique=True),
        sa.Column("key_prefix", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )

    # documents
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer()),
        sa.Column("status", sa.Enum("pending","preprocessing","preprocess_failed","ocr","ocr_failed","classified","structuring","structuring_failed","reconciliation","reconciliation_failed","validating","validation_failed","completed","review","manual_review", name="documentstatus")),
        sa.Column("document_type", sa.Enum("invoice","proforma","delivery_note","contract","bank_statement","official_document", name="documenttype"), nullable=True),
        sa.Column("decision", sa.Enum("auto","review","manual", name="decision"), nullable=True),
        sa.Column("error_message", sa.Text()),
        sa.Column("doc_metadata", sa.JSON()),
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
        sa.Column("processing_completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_documents_tenant", "documents", ["tenant_id"])
    op.create_index("idx_documents_status", "documents", ["status"])
    op.create_index("idx_documents_created", "documents", ["created_at"])
    op.create_index("idx_documents_type", "documents", ["document_type"])

    # document_files
    op.create_table(
        "document_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("file_type", sa.String(50), nullable=False),
        sa.Column("page_number", sa.Integer()),
        sa.Column("minio_path", sa.String(1000), nullable=False),
        sa.Column("file_size", sa.Integer()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_document_files_doc", "document_files", ["document_id"])

    # ocr_results
    op.create_table(
        "ocr_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.Column("text_blocks", sa.JSON()),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("processing_time_ms", sa.Integer()),
        sa.Column("model_version", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("document_id", name="uq_ocr_results_document"),
    )
    op.create_index("idx_ocr_results_doc", "ocr_results", ["document_id"])

    # structured_results
    op.create_table(
        "structured_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("document_type", sa.Enum("invoice","proforma","delivery_note","contract","bank_statement","official_document", name="documenttype"), nullable=False),
        sa.Column("extracted_data", postgresql.JSONB(), nullable=False),
        sa.Column("field_confidences", sa.JSON()),
        sa.Column("raw_llm_response", sa.Text()),
        sa.Column("model_version", sa.String(100)),
        sa.Column("processing_time_ms", sa.Integer()),
        sa.Column("bbox_evidence", sa.JSON()),
        sa.Column("supplier_lookup_result", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("document_id", name="uq_structured_results_document"),
    )
    op.create_index("idx_structured_results_doc", "structured_results", ["document_id"])

    # reconciliation_logs
    op.create_table(
        "reconciliation_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("line_items_count", sa.Integer(), nullable=False),
        sa.Column("extracted_subtotal", sa.Numeric(15, 2)),
        sa.Column("calculated_subtotal", sa.Numeric(15, 2)),
        sa.Column("extracted_vat", sa.Numeric(15, 2)),
        sa.Column("calculated_vat", sa.Numeric(15, 2)),
        sa.Column("extracted_total", sa.Numeric(15, 2)),
        sa.Column("calculated_total", sa.Numeric(15, 2)),
        sa.Column("subtotal_match", sa.Boolean()),
        sa.Column("vat_match", sa.Boolean()),
        sa.Column("total_match", sa.Boolean()),
        sa.Column("reconciliation_status", sa.String(20), nullable=False),
        sa.Column("discrepancy_details", sa.JSON()),
        sa.Column("processing_time_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("document_id", name="uq_reconciliation_logs_document"),
    )
    op.create_index("idx_reconciliation_logs_doc", "reconciliation_logs", ["document_id"])

    # webhooks
    op.create_table(
        "webhooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("secret", sa.String(255), nullable=False),
        sa.Column("events", postgresql.ARRAY(sa.String())),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("headers", sa.JSON()),
        sa.Column("retry_count", sa.Integer(), default=3),
        sa.Column("retry_delay_seconds", sa.Integer(), default=60),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_webhooks_tenant", "webhooks", ["tenant_id"])

    # suppliers
    op.create_table(
        "suppliers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("pib", sa.String(20)),
        sa.Column("mb", sa.String(20)),
        sa.Column("iban", sa.String(50)),
        sa.Column("address", sa.Text()),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "pib", name="uq_supplier_tenant_pib"),
    )
    op.create_index("idx_suppliers_tenant", "suppliers", ["tenant_id"])

    # audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("event", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_audit_logs_document", "audit_logs", ["document_id"])
    op.create_index("idx_audit_logs_tenant", "audit_logs", ["tenant_id"])
    op.create_index("idx_audit_logs_created", "audit_logs", ["created_at"])

    # webhook_deliveries
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("webhook_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("webhooks.id"), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True)),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("response_body", sa.Text()),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempts", sa.Integer(), default=0),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_webhook_deliveries_webhook", "webhook_deliveries", ["webhook_id"])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("audit_logs")
    op.drop_table("suppliers")
    op.drop_table("webhooks")
    op.drop_table("reconciliation_logs")
    op.drop_table("structured_results")
    op.drop_table("ocr_results")
    op.drop_table("document_files")
    op.drop_table("documents")
    op.drop_table("api_keys")
    op.drop_table("tenants")
    sa.Enum(name="decision").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="documenttype").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="documentstatus").drop(op.get_bind(), checkfirst=True)
