"""Celery application configuration and shared utilities."""

import os
from celery import Celery
from celery.signals import worker_ready, worker_shutdown
from kombu import Exchange, Queue

from api.core.config import settings

# Get worker type from environment
WORKER_TYPE = os.environ.get("WORKER_TYPE", "general")

# Create Celery app
celery_app = Celery(
    "ocr_saas",
    broker=settings.RABBITMQ_URL,
    backend=settings.REDIS_URL,
    include=[
        "workers.preprocessing.tasks",
        "workers.ocr.tasks",
        "workers.classification.tasks",
        "workers.structuring.tasks",
        "workers.reconciliation.tasks",
        "workers.validation.tasks",
    ],
)

# Feature 5: Priority queue arguments — x-max-priority=10 enables per-message priority
# in RabbitMQ. Priority 0 = highest, 9 = lowest (Celery convention with RabbitMQ).
# NOTE: Existing queues must be deleted and re-declared when deploying, because
# RabbitMQ does not allow changing queue arguments after creation.
PRIORITY_ARGS = {"x-max-priority": 10}


def _make_queue(name: str, with_priority: bool = True) -> Queue:
    exchange = Exchange(name, type="direct")
    args = PRIORITY_ARGS if with_priority else {}
    return Queue(name, exchange, routing_key=name, queue_arguments=args)


# Celery configuration
celery_app.conf.update(
    # Task serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task routing
    task_routes={
        "workers.preprocessing.tasks.*": {"queue": settings.PREPROCESS_QUEUE},
        "workers.ocr.tasks.*": {"queue": settings.OCR_QUEUE},
        "workers.classification.tasks.*": {"queue": settings.CLASSIFICATION_QUEUE},
        "workers.structuring.tasks.*": {"queue": settings.STRUCTURING_QUEUE},
        "workers.reconciliation.tasks.*": {"queue": settings.RECONCILIATION_QUEUE},
        "workers.validation.tasks.*": {"queue": settings.VALIDATION_QUEUE},
    },

    # Task execution
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=600,   # 10 minutes
    task_soft_time_limit=540,  # 9 minutes

    # Result backend
    result_expires=86400,  # 24 hours
    result_backend_transport_options={
        "master_name": "mymaster",
    },

    # Retry configuration
    task_default_retry_delay=60,
    task_max_retries=3,

    # Feature 5: Priority-enabled queues (kombu Queue objects)
    task_queues=[
        _make_queue(settings.PREPROCESS_QUEUE),
        _make_queue(settings.OCR_QUEUE),
        _make_queue(settings.CLASSIFICATION_QUEUE),
        _make_queue(settings.STRUCTURING_QUEUE),
        _make_queue(settings.RECONCILIATION_QUEUE),
        _make_queue(settings.VALIDATION_QUEUE),
        _make_queue(settings.DEAD_LETTER_QUEUE, with_priority=False),
    ],

    # Monitoring
    worker_send_task_events=True,
    task_send_sent_event=True,
)


@worker_ready.connect
def on_worker_ready(**kwargs):
    """Log worker readiness."""
    print(f"Worker ready: {WORKER_TYPE}")


@worker_shutdown.connect
def on_worker_shutdown(**kwargs):
    """Log worker shutdown."""
    print(f"Worker shutting down: {WORKER_TYPE}")
