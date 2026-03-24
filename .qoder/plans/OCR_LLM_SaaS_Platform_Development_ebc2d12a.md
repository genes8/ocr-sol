# OCR/LLM SaaS Platform - Development Plan

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        OCR/LLM SaaS Platform                         │
├─────────────────────────────┬───────────────────────────────────────┤
│      BACKEND (DMS Integration)│        FRONTEND (SaaS)               │
├─────────────────────────────┼───────────────────────────────────────┤
│  • FastAPI REST API         │  • React + TypeScript Tanstack        │
│  • RabbitMQ Workers         │  • Document Upload UI                 │
│  • vLLM/SGLang Model Server │  • Review UI with Bbox Overlay        │
│  • PostgreSQL + MinIO       │  • Dashboard & Analytics              │
│  • Multi-tenant Support     │  • User/Team Management              │
└─────────────────────────────┴───────────────────────────────────────┘
```

---

## Phase 1: Foundation & Backend Core

### 1.1 Project Structure Setup
```
/ocr-saas/
├── backend/
│   ├── api/                    # FastAPI application
│   │   ├── routes/            # API endpoints
│   │   ├── models/           # Pydantic models
│   │   ├── schemas/          # JSON schemas per document type
│   │   └── core/             # Config, security, dependencies
│   ├── workers/               # RabbitMQ consumers
│   │   ├── preprocessing/
│   │   ├── ocr/
│   │   ├── classification/
│   │   ├── structuring/
│   │   ├── validation/
│   │   └── reconciliation/
│   ├── services/              # Business logic
│   ├── models/                # ML model management
│   └── infrastructure/        # DB, queue, storage clients
├── frontend/
│   ├── src/
│   │   ├── components/        # UI components
│   │   ├── pages/            # Route pages
│   │   ├── hooks/           # Custom React hooks
│   │   ├── services/         # API client
│   │   └── stores/           # State management
│   └── public/
├── models/                    # On-premise ML models
├── docker/
└── k8s/                      # Kubernetes manifests
```

### 1.2 Backend Core Components

| Component | Technology | Purpose |
|-----------|------------|---------|
| API Framework | FastAPI | REST API with OpenAPI docs |
| ORM | SQLAlchemy 2.0 + asyncpg | PostgreSQL async access |
| Task Queue | Celery + RabbitMQ | Async job processing |
| Model Serving | vLLM | GPU-accelerated inference |
| File Storage | MinIO SDK | S3-compatible storage |
| Auth | OAuth2 + JWT | Multi-tenant authentication |

### 1.3 Database Schema (PostgreSQL)

**Core Tables:**
- `tenants` - Multi-tenant organization
- `users` - Per-tenant users with roles
- `documents` - Uploaded document metadata
- `jobs` - Processing job tracking
- `ocr_results` - Raw OCR + bbox storage
- `structured_results` - JSON output per document
- `reconciliation_logs` - Line items check results
- `review_sessions` - Human review tracking
- `audit_logs` - Complete audit trail
- `billing_records` - Usage metering

### 1.4 API Endpoints (DMS Integration)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/documents/upload` | POST | Upload document (PDF, images) |
| `/api/v1/documents/{id}` | GET | Get document status/metadata |
| `/api/v1/documents/{id}/result` | GET | Get structured JSON result |
| `/api/v1/documents/{id}/ocr` | GET | Get raw OCR with bbox |
| `/api/v1/documents/{id}/review` | PATCH | Submit review corrections |
| `/api/v1/jobs/{id}` | GET | Get job processing status |
| `/api/v1/webhooks` | POST | Register webhook for events |
| `/api/v1/tenants/{id}/usage` | GET | Usage statistics |
| `/api/v1/schema/document-types` | GET | Supported document types |

---

## Phase 2: ML Pipeline (On-Premise Models)

### 2.1 Model Stack

| Model | Purpose | Size | Serving |
|-------|---------|------|---------|
| GLM-OCR | Text + bbox extraction | ~0.9B | vLLM/SGLang |
| Classification Model | Document type routing | Small (<100M) | ONNX Runtime |
| Structuring LLM | JSON extraction | Small (<1B) | vLLM (quantized) |

### 2.2 Worker Architecture

```
Upload → preprocess_queue → preprocessing_worker
                                      ↓
                              ocr_queue → ocr_worker (GPU)
                                      ↓
                           classification_queue → classification_worker
                                      ↓
                            structuring_queue → structuring_worker (GPU)
                                      ↓
                          reconciliation_queue → reconciliation_worker
                                      ↓
                            validation_queue → validation_worker
                                      ↓
                           decision_engine → DMS webhook / review_queue
```

### 2.3 Document Type Schemas

Create JSON schemas for:
- `invoice_schema.json` - Fakture
- `proforma_schema.json` - Profakture
- `delivery_note_schema.json` - Otpremnice
- `contract_schema.json` - Ugovori
- `bank_stmt_schema.json` - Bank statements
- `official_doc_schema.json` - Rešenja/dopisi

---

## Phase 3: Frontend SaaS

### 3.1 Core Pages

| Page | Route | Purpose |
|------|-------|---------|
| Dashboard | `/` | Overview, recent documents, stats |
| Upload | `/upload` | Drag-drop document upload |
| Documents | `/documents` | List, search, filter |
| Document View | `/documents/:id` | Result viewer with bbox overlay |
| Review | `/review` | Batch review queue |
| Settings | `/settings` | Tenant config, API keys, schemas |
| Team | `/team` | User management, roles |

### 3.2 Key Features

- **Bbox Overlay**: Click any JSON field → highlight source on original scan
- **Inline Editing**: Correct OCR errors directly in the UI
- **Confidence Indicators**: Color-coded confidence scores (green/yellow/red)
- **Bulk Actions**: Approve/reject multiple documents
- **API Key Management**: Generate keys for DMS integration
- **Usage Dashboard**: Documents processed, GPU time, storage

---

## Phase 4: Multi-Tenant & Enterprise

### 4.1 Tenant Isolation
- Row-level security in PostgreSQL
- Per-tenant rate limits (requests/minute, documents/hour)
- Per-tenant queue quotas
- Custom JSON schemas per tenant

### 4.2 Billing Integration
- Document count metering
- GPU compute time tracking
- Storage usage per tenant
- Webhook for billing system integration

### 4.3 Enterprise Features
- SSO/SAML support
- Custom document schemas
- On-premise deployment package
- Dedicated GPU resources

---

## Phase 5: DevOps & Deployment

### 5.1 Docker Compose (Development)
```yaml
services:
  api:
    build: ./backend
  worker:
    build: ./backend
    command: celery -A workers worker
  minio:
    image: minio/minio
  postgres:
    image: postgres:16
  rabbitmq:
    image: rabbitmq:3.12
  redis:
    image: redis:7
  vllm:
    build: ./models/vllm
```

### 5.2 Kubernetes (Production)
- CPU Node Pool: API, workers, infrastructure
- GPU Node Pool: vLLM inference servers
- HPA for workers based on queue depth
- Persistent volumes for MinIO, PostgreSQL

---

## Implementation Order

| Phase | Task | Duration | Deliverable |
|-------|------|----------|-------------|
| 1 | Project setup, Docker, DB schema | 1 week | Running dev environment |
| 2 | FastAPI core + upload endpoint | 1 week | Working document upload |
| 3 | vLLM integration + OCR pipeline | 2 weeks | End-to-end OCR |
| 4 | Classification + Structuring LLM | 2 weeks | Structured JSON output |
| 5 | DMS API documentation + SDK | 1 week | Integration-ready API |
| 6 | React frontend core | 2 weeks | Dashboard + upload UI |
| 7 | Review UI with bbox overlay | 2 weeks | Full review workflow |
| 8 | Multi-tenant + auth | 1 week | Tenant isolation |
| 9 | Reconciliation + validation | 1 week | Business logic |
| 10 | Kubernetes deployment | 1 week | Production-ready |

---

## Tech Stack Summary

| Layer | Technology |
|-------|------------|
| Backend API | Python 3.11 + FastAPI |
| Task Queue | Celery + RabbitMQ |
| Database | PostgreSQL 16 (async) |
| Cache | Redis 7 |
| Storage | MinIO (S3-compatible) |
| ML Serving | vLLM |
| Frontend | React 18 + TypeScript Tanstack + Vite |
| UI Components | ui + Radix |
| State | Zustand |
| Styling | Tailwind CSS |
| Container | Docker + Kubernetes |
| Monitoring | Prometheus + Grafana |

---

## Key Decisions

1. **FastAPI + Celery** over pure async workers for better job tracking
2. **vLLM for all GPU models** - unified inference layer
3. **PostgreSQL with RLS** for multi-tenant isolation
4. **Separate queues per processing stage** for fine-grained scaling
5. **Bbox stored as JSON in PostgreSQL** - fast access, easy API response
6. **React Tanstack** - production-grade components, fast development
