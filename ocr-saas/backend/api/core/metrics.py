"""Prometheus metrics for OCR SaaS API and workers."""

from prometheus_client import Counter, Gauge, Histogram, Info

# --- Document pipeline counters ---
documents_uploaded_total = Counter(
    "ocr_documents_uploaded_total",
    "Total documents uploaded",
    ["tenant_id"],
)

documents_processed_total = Counter(
    "ocr_documents_processed_total",
    "Total documents reaching final state",
    ["decision"],  # auto, review, manual
)

pipeline_stage_completed_total = Counter(
    "ocr_pipeline_stage_completed_total",
    "Pipeline stage completion count",
    ["stage"],  # preprocess, ocr, classification, structuring, reconciliation, validation
)

pipeline_stage_failed_total = Counter(
    "ocr_pipeline_stage_failed_total",
    "Pipeline stage failure count",
    ["stage"],
)

# --- Latency histograms ---
http_request_duration_seconds = Histogram(
    "ocr_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path", "status_code"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# --- Gauges (current state) ---
documents_in_flight = Gauge(
    "ocr_documents_in_flight",
    "Documents currently being processed",
)

review_queue_depth = Gauge(
    "ocr_review_queue_depth",
    "Documents waiting in review/manual_review status",
)

# --- App info ---
app_info = Info("ocr_app", "OCR SaaS application info")
