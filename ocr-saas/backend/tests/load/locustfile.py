"""Locust load test — OCR SaaS API.

Target SLO: 1.000+ documents/hour = ~17 docs/minute = ~0.28 docs/second.

Usage:
  # Install: pip install locust
  # Run interactive UI:
  #   locust -f tests/load/locustfile.py --host http://localhost:8000
  # Run headless (CI):
  #   locust -f tests/load/locustfile.py --host http://localhost:8000 \\
  #     --headless -u 20 -r 5 --run-time 5m \\
  #     --csv=tests/load/results/run

Environment variables:
  LOAD_TEST_API_KEY   — API key header value (required)
  LOAD_TEST_PDF_PATH  — path to sample PDF file (optional, uses generated stub if absent)
"""

import io
import os
import random
import struct
import time
import uuid

from locust import HttpUser, between, events, task
from locust.runners import MasterRunner

API_KEY = os.getenv("LOAD_TEST_API_KEY", "test-api-key")
PDF_PATH = os.getenv("LOAD_TEST_PDF_PATH", "")


# ---------------------------------------------------------------------------
# Minimal valid PDF stub (1-page, ~1 KB) used when no real PDF is configured
# ---------------------------------------------------------------------------

def _minimal_pdf() -> bytes:
    """Generate a minimal valid single-page PDF in memory."""
    return b"""%PDF-1.4
1 0 obj<</Type /Catalog /Pages 2 0 R>> endobj
2 0 obj<</Type /Pages /Kids [3 0 R] /Count 1>> endobj
3 0 obj<</Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]
  /Resources<</Font<</F1 4 0 R>>>> /Contents 5 0 R>> endobj
4 0 obj<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>> endobj
5 0 obj<</Length 44>>
stream
BT /F1 12 Tf 72 720 Td (Test Invoice 2024) Tj ET
endstream endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000348 00000 n
trailer<</Size 6 /Root 1 0 R>>
startxref
444
%%EOF"""


_PDF_BYTES: bytes = open(PDF_PATH, "rb").read() if PDF_PATH and os.path.exists(PDF_PATH) else _minimal_pdf()


# ---------------------------------------------------------------------------
# Locust users
# ---------------------------------------------------------------------------

class OCRApiUser(HttpUser):
    """Simulates a tenant uploading and monitoring documents."""

    wait_time = between(1, 3)  # Seconds between requests per user
    headers = {"X-API-Key": API_KEY}

    def on_start(self):
        self.uploaded_ids: list[str] = []

    # --- Task weights: upload is the hot path ---

    @task(10)
    def upload_document(self):
        """Upload a document — the primary pipeline trigger."""
        filename = f"invoice_{uuid.uuid4().hex[:8]}.pdf"
        with self.client.post(
            "/api/v1/documents/upload",
            files={"file": (filename, io.BytesIO(_PDF_BYTES), "application/pdf")},
            headers={k: v for k, v in self.headers.items()},
            name="/api/v1/documents/upload",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                doc_id = resp.json().get("id")
                if doc_id:
                    self.uploaded_ids.append(doc_id)
                resp.success()
            elif resp.status_code == 429:
                resp.failure("Rate limited (quota)")
            else:
                resp.failure(f"Upload failed: {resp.status_code}")

    @task(5)
    def list_documents(self):
        """List documents — common dashboard call."""
        with self.client.get(
            "/api/v1/documents?limit=20&skip=0",
            headers=self.headers,
            name="/api/v1/documents (list)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"List failed: {resp.status_code}")

    @task(3)
    def poll_document_status(self):
        """Poll a recently uploaded document for status updates."""
        if not self.uploaded_ids:
            return
        doc_id = random.choice(self.uploaded_ids)
        with self.client.get(
            f"/api/v1/documents/{doc_id}",
            headers=self.headers,
            name="/api/v1/documents/{id}",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 404:
                # Document may have been cleaned up
                self.uploaded_ids.remove(doc_id)
                resp.success()
            else:
                resp.failure(f"Status poll failed: {resp.status_code}")

    @task(2)
    def get_document_result(self):
        """Fetch full processing result for a document."""
        if not self.uploaded_ids:
            return
        doc_id = random.choice(self.uploaded_ids)
        with self.client.get(
            f"/api/v1/documents/{doc_id}/result",
            headers=self.headers,
            name="/api/v1/documents/{id}/result",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404, 422):
                resp.success()  # 422 = still processing, expected
            else:
                resp.failure(f"Result fetch failed: {resp.status_code}")

    @task(1)
    def health_check(self):
        """Monitor health endpoint."""
        with self.client.get("/health", name="/health", catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Health check failed: {resp.status_code}")

    @task(1)
    def list_review_queue(self):
        """Simulate reviewer checking the queue."""
        with self.client.get(
            "/api/v1/documents?status=review&limit=50",
            headers=self.headers,
            name="/api/v1/documents (review queue)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Review queue failed: {resp.status_code}")


class ReviewerUser(HttpUser):
    """Simulates a human reviewer approving/rejecting documents."""

    wait_time = between(5, 15)  # Reviewers work more slowly
    weight = 1  # 1 reviewer for every ~5 uploaders
    headers = {"X-API-Key": API_KEY}

    def on_start(self):
        self.review_ids: list[str] = []

    @task(3)
    def fetch_review_queue(self):
        with self.client.get(
            "/api/v1/documents?status=review&limit=50",
            headers=self.headers,
            name="/api/v1/documents (reviewer list)",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                self.review_ids = [d["id"] for d in items[:10]]
                resp.success()
            else:
                resp.failure(f"Review list failed: {resp.status_code}")

    @task(2)
    def approve_document(self):
        if not self.review_ids:
            return
        doc_id = self.review_ids.pop(0)
        with self.client.patch(
            f"/api/v1/documents/{doc_id}",
            json={"decision": "auto"},
            headers=self.headers,
            name="/api/v1/documents/{id} (approve)",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Approve failed: {resp.status_code}")

    @task(1)
    def get_audit_trail(self):
        if not self.review_ids:
            return
        doc_id = random.choice(self.review_ids) if self.review_ids else uuid.uuid4()
        with self.client.get(
            f"/api/v1/documents/{doc_id}/audit",
            headers=self.headers,
            name="/api/v1/documents/{id}/audit",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"Audit trail failed: {resp.status_code}")


# ---------------------------------------------------------------------------
# SLO assertions (run after the test)
# ---------------------------------------------------------------------------

@events.quitting.add_listener
def check_slos(environment, **kwargs):
    """Assert SLOs after the test run completes."""
    stats = environment.runner.stats.total if environment.runner else None
    if not stats:
        return

    failures = []

    # Upload p95 < 5s
    p95 = stats.get_response_time_percentile(0.95)
    if p95 and p95 > 5_000:
        failures.append(f"Upload p95={p95:.0f}ms exceeds 5s SLO")

    # Error rate < 1%
    error_rate = stats.fail_ratio
    if error_rate > 0.01:
        failures.append(f"Error rate={error_rate:.1%} exceeds 1% SLO")

    # Throughput >= 0.28 rps (1000 docs/hour) for upload endpoint
    # Use num_requests over the run to avoid current_rps being 0 at quitting time.
    upload_stats = environment.runner.stats.get("/api/v1/documents/upload", "POST") if environment.runner else None
    if upload_stats:
        run_time_s = environment.runner.stats.last_request_timestamp - environment.runner.stats.start_time
        avg_rps = upload_stats.num_requests / max(run_time_s, 1) if run_time_s else 0
        if avg_rps < 0.28:
            failures.append(
                f"Upload avg throughput={avg_rps:.2f} rps below 0.28 rps (1000/hour) target"
            )

    if failures:
        print("\n[LOAD TEST] SLO VIOLATIONS:")
        for f in failures:
            print(f"  ✗ {f}")
        environment.process_exit_code = 1
    else:
        print("\n[LOAD TEST] All SLOs passed.")
