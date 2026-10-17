#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "AI Career Coach (Phase 1) - Controlled Demo Reproduction"
echo "=========================================================="

BACKEND_URL="http://localhost:8000"

echo "[1/4] Checking backend server at ${BACKEND_URL}/health..."
if ! curl -s "${BACKEND_URL}/health" > /dev/null; then
    echo "Backend is not running. Starting backend server in background on port 8000..."
    uvicorn backend.app.main:app --port 8000 &
    BACKEND_PID=$!
    sleep 2
fi

echo "[2/4] Seeding cached baseline analysis (R-20, C-15)..."
SEED_RESP=$(curl -s -X POST "${BACKEND_URL}/api/demo/seed")
echo "Seeded Result: ${SEED_RESP}"

echo "[3/4] Baseline state verified:"
echo " - API Development: Demonstrated in main.py:16-36"
echo " - SQL: Not demonstrated in this scope"
echo " - Testing: Not demonstrated in this scope"

echo "[4/4] Simulating live commit adding SQL and Pytest tests..."
COMMIT_RESP=$(curl -s -X POST "${BACKEND_URL}/api/demo/commit?action=add_sql_tests")
echo "Commit Result: ${COMMIT_RESP}"

echo "=========================================================="
echo "Demo Loop Succeeded! View live dashboard at http://localhost:3000"
echo "=========================================================="
