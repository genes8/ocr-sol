# Load Test — Task 20 Acceptance Criteria

## Definition of Done

Task 20 is closed when **all** of the following evidence exists in `tests/load/results/`:

| Artifact | File | Required |
|---|---|---|
| Locust stats CSV | `run_stats.csv` | ✅ must exist |
| Locust log | `run.log` | ✅ must exist |
| SLO check exit 0 | `check_results.py` output | ✅ must pass |

### SLOs that must pass

| SLO | Threshold | Column in CSV |
|---|---|---|
| Upload throughput | ≥ 0.25 rps (= 900 docs/hour, 10% margin on 1000/h target) | `Requests/s` for upload row |
| Upload p95 latency | < 5 000 ms | `95%` |
| Upload p99 latency | < 10 000 ms | `99%` |
| Global error rate | < 1 % | `Failure Count / Request Count` on Aggregated row |
| Minimum upload requests | ≥ 100 | `Request Count` for upload row (proves test ran at load) |

The `check_results.py` script reads `run_stats.csv` and exits 0 iff all SLOs pass.

---

## How to Run

### Prerequisites

```bash
pip install locust
export LOAD_TEST_API_KEY="<your-api-key>"
export LOAD_TEST_HOST="http://localhost:8000"   # or staging URL
```

### Minimal run (5 min, 20 users)

```bash
cd ocr-saas/backend
bash tests/load/run_load_test.sh
```

This produces:
- `tests/load/results/run_stats.csv`
- `tests/load/results/run_stats_history.csv`
- `tests/load/results/run_failures.csv`
- `tests/load/results/run.log`

### Verify SLOs independently

```bash
python3 tests/load/check_results.py tests/load/results/run
```

Exit code 0 = Task 20 PASS.

### Recommended CI command

```bash
LOAD_TEST_API_KEY=$SECRET_API_KEY \
LOAD_TEST_HOST=https://staging.ocr-saas.internal \
LOAD_TEST_USERS=40 \
LOAD_TEST_SPAWN_RATE=10 \
LOAD_TEST_DURATION=10m \
bash tests/load/run_load_test.sh
```

---

## Evidence to commit (or attach to ticket)

After a passing run, commit or archive:

```
tests/load/results/run_stats.csv          ← machine-readable SLO data
tests/load/results/run.log                ← human-readable summary with rps line
```

The log must contain a line like:
```
Task 20: PASS ✓
```

and the stats CSV must show `Requests/s ≥ 0.25` for the upload endpoint.

---

## Interpreting run_stats.csv

Key columns for the upload row (`Name=/api/v1/documents/upload`, `Type=POST`):

| Column | Meaning |
|---|---|
| `Request Count` | Total uploads during the test |
| `Failure Count` | Failed uploads (4xx/5xx) |
| `Requests/s` | Average throughput over the run |
| `Median Response Time` | p50 latency (ms) |
| `95%` | p95 latency (ms) — primary SLO |
| `99%` | p99 latency (ms) |

The `Aggregated` row gives global error rate across all endpoints.
