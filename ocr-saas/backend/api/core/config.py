"""Application configuration using Pydantic Settings."""

from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "OCR SaaS Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://ocr_saas:ocr_saas_secret@localhost:5432/ocr_saas"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # RabbitMQ
    RABBITMQ_URL: str = "amqp://ocr_saas:ocr_saas_secret@localhost:5672"

    # MinIO / S3 Storage
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "ocr_saas"
    MINIO_SECRET_KEY: str = "ocr_saas_secret"
    MINIO_SECURE: bool = False
    MINIO_BUCKET_DOCUMENTS: str = "ocr-documents"
    MINIO_BUCKET_RESULTS: str = "ocr-results"
    MINIO_BUCKET_THUMBNAILS: str = "ocr-thumbnails"

    # Security
    SECRET_KEY: str = "change-me-in-production-use-strong-random-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # vLLM Model Server (GLM-OCR vision model)
    VLLM_BASE_URL: str = "http://localhost:8001"
    VLLM_MODEL_NAME: str = "glm-ocr"
    VLLM_TIMEOUT: int = 300

    # Structuring LLM (text-only model, separate server)
    STRUCTURING_LLM_BASE_URL: str = "http://localhost:8002"
    STRUCTURING_LLM_MODEL_NAME: str = "structuring-llm"
    STRUCTURING_LLM_TIMEOUT: int = 120

    # Priority lanes
    ENTERPRISE_TASK_PRIORITY: int = 0   # 0 = highest in Celery/RabbitMQ
    STANDARD_TASK_PRIORITY: int = 5

    # Processing
    MAX_FILE_SIZE_MB: int = 50
    MAX_PAGES_PER_DOCUMENT: int = 100
    ALLOWED_EXTENSIONS: list[str] = ["pdf", "png", "jpg", "jpeg", "tiff", "tif"]
    ALLOWED_MIME_TYPES: list[str] = [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
    ]

    # Queue Configuration
    PREPROCESS_QUEUE: str = "preprocess_queue"
    OCR_QUEUE: str = "ocr_queue"
    CLASSIFICATION_QUEUE: str = "classification_queue"
    STRUCTURING_QUEUE: str = "structuring_queue"
    RECONCILIATION_QUEUE: str = "reconciliation_queue"
    VALIDATION_QUEUE: str = "validation_queue"
    REVIEW_QUEUE: str = "review_queue"
    DEAD_LETTER_QUEUE: str = "dead_letter_queue"

    # Confidence Thresholds (default, can be per-tenant)
    DEFAULT_INVOICE_NUMBER_CONFIDENCE: float = 0.90
    DEFAULT_INVOICE_DATE_CONFIDENCE: float = 0.90
    DEFAULT_SUPPLIER_CONFIDENCE: float = 0.85
    DEFAULT_TOTAL_AMOUNT_CONFIDENCE: float = 0.95
    DEFAULT_VAT_AMOUNT_CONFIDENCE: float = 0.90
    DEFAULT_LINE_ITEM_CONFIDENCE: float = 0.80
    CRITICAL_FIELD_THRESHOLD: float = 0.50

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list[str] = ["*"]
    CORS_ALLOW_HEADERS: list[str] = ["*"]


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()
