# OCR SaaS Backend

## Requirements

- Python 3.11+
- PostgreSQL 16
- Redis 7
- RabbitMQ 3.12
- MinIO (S3-compatible)

## Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment Variables

```env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ocr_saas
REDIS_URL=redis://localhost:6379/0
RABBITMQ_URL=amqp://user:pass@localhost:5672
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
SECRET_KEY=your-secret-key-change-in-production
VLLM_BASE_URL=http://localhost:8001
```

## Project Structure

```
backend/
├── api/                    # FastAPI application
│   ├── routes/            # API endpoints
│   ├── models/            # Pydantic models
│   ├── schemas/           # JSON schemas per document type
│   └── core/              # Config, security, dependencies
├── workers/               # Celery workers
│   ├── preprocessing/
│   ├── ocr/
│   ├── classification/
│   ├── structuring/
│   ├── validation/
│   └── reconciliation/
├── services/              # Business logic
├── models/                # ML model management
└── infrastructure/       # DB, queue, storage clients
```
