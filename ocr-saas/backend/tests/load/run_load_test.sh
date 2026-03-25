#!/usr/bin/env bash
# Run the load test headless and verify SLOs.
#
# Exit code:
#   0 — test ran and all SLOs passed  (Task 20: DONE)
#   1 — SLO violation                 (Task 20: FAIL)
#   2 — setup error
#
# Required env vars:
#   LOAD_TEST_API_KEY   — valid API key for the target stack
#   LOAD_TEST_HOST      — base URL, e.g. http://localhost:8000
#
# Optional:
#   LOAD_TEST_USERS     — concurrent users      (default: 20)
#   LOAD_TEST_SPAWN_RATE— users spawned/second  (default: 5)
#   LOAD_TEST_DURATION  — e.g. "5m", "10m"     (default: 5m)
#   LOAD_TEST_PDF_PATH  — path to real PDF file (uses stub if absent)
#
# Produces:
#   tests/load/results/run_stats.csv
#   tests/load/results/run_stats_history.csv
#   tests/load/results/run_failures.csv
#   tests/load/results/run_report.html
#   tests/load/results/run.log

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/results"
PREFIX="$RESULTS_DIR/run"
LOG="$PREFIX.log"

HOST="${LOAD_TEST_HOST:-http://localhost:8000}"
USERS="${LOAD_TEST_USERS:-20}"
SPAWN_RATE="${LOAD_TEST_SPAWN_RATE:-5}"
DURATION="${LOAD_TEST_DURATION:-5m}"

echo "=== OCR SaaS Load Test ==="
echo "Host       : $HOST"
echo "Users      : $USERS"
echo "Spawn rate : $SPAWN_RATE/s"
echo "Duration   : $DURATION"
echo "Results    : $RESULTS_DIR"
echo ""

mkdir -p "$RESULTS_DIR"

# Check locust is installed
if ! command -v locust &>/dev/null; then
  echo "ERROR: locust not installed. Run: pip install locust"
  exit 2
fi

# Check API key is set
if [[ -z "${LOAD_TEST_API_KEY:-}" ]]; then
  echo "ERROR: LOAD_TEST_API_KEY is not set"
  exit 2
fi

# Run locust headless
locust \
  -f "$SCRIPT_DIR/locustfile.py" \
  --host "$HOST" \
  --headless \
  --users "$USERS" \
  --spawn-rate "$SPAWN_RATE" \
  --run-time "$DURATION" \
  --csv="$PREFIX" \
  --html="$PREFIX_report.html" \
  --loglevel INFO \
  2>&1 | tee "$LOG"

echo ""
echo "=== Checking SLOs ==="
python3 "$SCRIPT_DIR/check_results.py" "$PREFIX"
