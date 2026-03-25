#!/usr/bin/env python3
"""Validate Locust CSV results against SLOs.

This script is the acceptance gate for Task 20 (Load test 1000+ docs/hour).
It reads the CSV files written by Locust (--csv=<prefix>) and exits non-zero
if any SLO is violated.

Usage:
    python tests/load/check_results.py tests/load/results/run

Locust writes two files:
    <prefix>_stats.csv      — per-endpoint aggregates
    <prefix>_stats_history.csv — time-series (not used here)

Exit code:
    0  — all SLOs passed
    1  — one or more SLOs violated (details printed to stdout)
"""

import csv
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# SLO definitions
# ---------------------------------------------------------------------------

SLOS = {
    # Minimum upload throughput (requests/second) averaged over the test run.
    # 1000 docs/hour = 16.67 docs/min = 0.278 docs/s  → use 0.25 as floor (10% margin)
    "upload_min_rps": 0.25,

    # Upload p95 response time (milliseconds)
    "upload_p95_ms": 5_000,

    # Upload p99 response time (milliseconds)
    "upload_p99_ms": 10_000,

    # Global error rate (fraction, 0–1)
    "max_error_rate": 0.01,

    # Minimum total upload requests during the test (proves the test actually ran at load)
    "min_upload_requests": 100,
}


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def load_stats(prefix: str) -> list[dict]:
    path = Path(f"{prefix}_stats.csv")
    if not path.exists():
        print(f"ERROR: Stats file not found: {path}")
        sys.exit(2)
    with path.open() as f:
        return list(csv.DictReader(f))


def find_row(rows: list[dict], name: str, method: str = "POST") -> dict | None:
    for row in rows:
        if row.get("Name") == name and row.get("Type", "").upper() == method.upper():
            return row
    # Fall back to Aggregated row
    for row in rows:
        if row.get("Name") == "Aggregated":
            return row
    return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_slos(prefix: str) -> list[str]:
    rows = load_stats(prefix)
    failures: list[str] = []

    upload_row = find_row(rows, "/api/v1/documents/upload", "POST")
    agg_row = find_row(rows, "Aggregated")

    if not upload_row:
        failures.append("No upload endpoint row found in stats CSV")
        return failures

    # --- Upload request count ---
    num_requests = int(upload_row.get("Request Count", 0))
    if num_requests < SLOS["min_upload_requests"]:
        failures.append(
            f"upload.num_requests={num_requests} < {SLOS['min_upload_requests']} "
            f"(test didn't reach target load)"
        )

    # --- Upload throughput (Requests/s column) ---
    rps = float(upload_row.get("Requests/s", 0))
    if rps < SLOS["upload_min_rps"]:
        failures.append(
            f"upload.rps={rps:.3f} < {SLOS['upload_min_rps']} "
            f"(need ≥0.25 rps = 900 docs/hour)"
        )

    # --- Upload p95 ---
    p95 = float(upload_row.get("95%", 0))
    if p95 > SLOS["upload_p95_ms"]:
        failures.append(
            f"upload.p95={p95:.0f}ms > {SLOS['upload_p95_ms']}ms SLO"
        )

    # --- Upload p99 ---
    p99 = float(upload_row.get("99%", 0))
    if p99 > SLOS["upload_p99_ms"]:
        failures.append(
            f"upload.p99={p99:.0f}ms > {SLOS['upload_p99_ms']}ms SLO"
        )

    # --- Global error rate ---
    if agg_row:
        total_req = int(agg_row.get("Request Count", 1))
        total_fail = int(agg_row.get("Failure Count", 0))
        error_rate = total_fail / max(total_req, 1)
        if error_rate > SLOS["max_error_rate"]:
            failures.append(
                f"global.error_rate={error_rate:.2%} > {SLOS['max_error_rate']:.0%} SLO "
                f"({total_fail}/{total_req} failed)"
            )

    return failures


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_summary(prefix: str) -> None:
    rows = load_stats(prefix)
    upload_row = find_row(rows, "/api/v1/documents/upload", "POST")
    agg_row = find_row(rows, "Aggregated")

    print("\n=== Load Test Results ===")
    if upload_row:
        print(f"Upload endpoint ({upload_row.get('Request Count')} requests):")
        print(f"  Throughput : {float(upload_row.get('Requests/s', 0)):.3f} rps"
              f"  ({float(upload_row.get('Requests/s', 0)) * 3600:.0f} docs/hour)")
        print(f"  Median     : {upload_row.get('Median Response Time')} ms")
        print(f"  p95        : {upload_row.get('95%')} ms")
        print(f"  p99        : {upload_row.get('99%')} ms")
        print(f"  Failures   : {upload_row.get('Failure Count')}")
    if agg_row:
        total = int(agg_row.get("Request Count", 0))
        fail = int(agg_row.get("Failure Count", 0))
        print(f"\nAll endpoints: {total} requests, {fail} failures "
              f"({fail/max(total,1):.2%} error rate)")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <csv-prefix>")
        print(f"  e.g. {sys.argv[0]} tests/load/results/run")
        sys.exit(1)

    prefix = sys.argv[1]
    print_summary(prefix)
    failures = check_slos(prefix)

    if failures:
        print("SLO VIOLATIONS:")
        for f in failures:
            print(f"  ✗ {f}")
        print("\nTask 20: FAIL")
        sys.exit(1)
    else:
        print("All SLOs passed.")
        print("Task 20: PASS ✓")
        sys.exit(0)


if __name__ == "__main__":
    main()
