-- OCR SaaS Database Schema
-- PostgreSQL 16

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Tenants table
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'inactive')),
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Users table (per-tenant)
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'user' CHECK (role IN ('admin', 'operator', 'user', 'viewer')),
    is_active BOOLEAN DEFAULT true,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, email)
);

-- API Keys for DMS integration
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    key_hash VARCHAR(255) NOT NULL,
    key_prefix VARCHAR(20) NOT NULL,
    permissions JSONB DEFAULT '["read", "write"]',
    rate_limit_per_minute INTEGER DEFAULT 60,
    is_active BOOLEAN DEFAULT true,
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Documents table
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    filename VARCHAR(500) NOT NULL,
    original_filename VARCHAR(500),
    mime_type VARCHAR(100),
    file_size INTEGER,
    storage_path VARCHAR(1000),
    status VARCHAR(50) DEFAULT 'pending' CHECK (status IN (
        'pending', 'uploading', 'uploaded', 'preprocessing',
        'ocr', 'classifying', 'structuring', 'reconciling',
        'validating', 'completed', 'failed', 'review_required',
        'review_in_progress', 'review_completed', 'manual_required'
    )),
    document_type VARCHAR(50),
    page_count INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Jobs table (processing job tracking)
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    job_type VARCHAR(50) NOT NULL CHECK (job_type IN (
        'preprocessing', 'ocr', 'classification', 'structuring',
        'reconciliation', 'validation', 'review'
    )),
    status VARCHAR(20) DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    worker_id VARCHAR(100),
    input_data JSONB,
    output_data JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    INDEX idx_jobs_document_id (document_id),
    INDEX idx_jobs_status (status)
);

-- OCR Results (raw OCR + bounding boxes)
CREATE TABLE ocr_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    blocks JSONB NOT NULL, -- Array of text blocks with bbox info
    raw_text TEXT,
    tables JSONB, -- Detected tables
    metadata JSONB DEFAULT '{}',
    confidence DECIMAL(5,4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(document_id, page_number)
);

-- Structured Results (JSON output per document)
CREATE TABLE structured_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_type VARCHAR(50) NOT NULL,
    schema_version VARCHAR(20),
    data JSONB NOT NULL,
    field_confidences JSONB, -- Per-field confidence scores
    field_evidence JSONB, -- Bbox references per field
    validation_status VARCHAR(20) CHECK (validation_status IN ('valid', 'warning', 'invalid')),
    validation_errors JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(document_id)
);

-- Line Items (extracted table rows)
CREATE TABLE line_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    structured_result_id UUID NOT NULL REFERENCES structured_results(id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL,
    description TEXT,
    quantity DECIMAL(15,3),
    unit VARCHAR(50),
    unit_price DECIMAL(15,2),
    line_total DECIMAL(15,2),
    vat_rate DECIMAL(5,2),
    vat_amount DECIMAL(15,2),
    data JSONB, -- Full row data
    ocr_block_refs JSONB, -- References to OCR blocks
    confidence DECIMAL(5,4),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Reconciliation Logs
CREATE TABLE reconciliation_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    structured_result_id UUID NOT NULL REFERENCES structured_results(id) ON DELETE CASCADE,
    check_type VARCHAR(100) NOT NULL, -- e.g., 'line_total_calculation', 'vat_calculation'
    status VARCHAR(20) NOT NULL CHECK (status IN ('pass', 'warn', 'fail')),
    expected_value DECIMAL(15,2),
    actual_value DECIMAL(15,2),
    difference DECIMAL(15,2),
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Review Sessions
CREATE TABLE review_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(20) DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'completed', 'cancelled')),
    corrections JSONB, -- Field corrections made during review
    notes TEXT,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Audit Logs
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID,
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id UUID,
    old_values JSONB,
    new_values JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Billing Records
CREATE TABLE billing_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    documents_processed INTEGER DEFAULT 0,
    pages_processed INTEGER DEFAULT 0,
    gpu_seconds DECIMAL(10,2) DEFAULT 0,
    storage_gb DECIMAL(10,3) DEFAULT 0,
    api_calls INTEGER DEFAULT 0,
    amount DECIMAL(10,2),
    currency VARCHAR(3) DEFAULT 'EUR',
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'invoiced', 'paid')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Webhooks
CREATE TABLE webhooks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    url VARCHAR(1000) NOT NULL,
    secret_hash VARCHAR(255),
    events JSONB DEFAULT '["document.completed", "document.failed", "document.review_required"]',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tenant custom schemas
CREATE TABLE custom_schemas (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    document_type VARCHAR(50) NOT NULL,
    schema_name VARCHAR(100) NOT NULL,
    schema_definition JSONB NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, document_type)
);

-- Indexes for performance
CREATE INDEX idx_documents_tenant_id ON documents(tenant_id);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_created_at ON documents(created_at DESC);
CREATE INDEX idx_documents_document_type ON documents(document_type);

CREATE INDEX idx_users_tenant_id ON users(tenant_id);
CREATE INDEX idx_users_email ON users(email);

CREATE INDEX idx_api_keys_tenant_id ON api_keys(tenant_id);
CREATE INDEX idx_api_keys_key_hash ON api_keys(key_hash);

CREATE INDEX idx_audit_logs_tenant_id ON audit_logs(tenant_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at DESC);

CREATE INDEX idx_jobs_tenant_id ON jobs(tenant_id);

-- Row-Level Security (RLS) policies
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- Helper function for RLS
CREATE OR REPLACE FUNCTION get_current_tenant_id() RETURNS UUID AS $$
BEGIN
    RETURN NULLIF(current_setting('app.current_tenant_id', true), '')::UUID;
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

-- RLS policies for tenants
CREATE POLICY tenant_isolation_users ON users
    USING (tenant_id = get_current_tenant_id());

CREATE POLICY tenant_isolation_documents ON documents
    USING (tenant_id = get_current_tenant_id());

CREATE POLICY tenant_isolation_api_keys ON api_keys
    USING (tenant_id = get_current_tenant_id());

CREATE POLICY tenant_isolation_audit_logs ON audit_logs
    USING (tenant_id = get_current_tenant_id());
