# Event-Driven Reassessment & Incremental Diff Engine

This document details the autonomous reassessment loop, GitHub webhook handling, SHA polling fallback, durable SQLite task queue, and incremental diff engine.

---

## ⚡ Architecture Overview

When candidates make changes in their repositories, the AI Career Coach automatically detects the push, analyzes the affected files, updates the gap matrix, and computes an incremental diff without re-scanning the entire repository.

```text
git push ──▶ Webhook (HMAC) / SHA Poll ──▶ Durable SQLite Queue (Lease Lock)
                                                      │
                                                      ▼
SSE Broadcast ◀── Assessment Diff (vN+1) ◀── Detector + Critic (Changed Files)
```

---

## 🔒 Webhook & SHA Polling

### 1. GitHub Webhook Receiver (`/api/github/webhook`)
- Secret-authenticated endpoint configured with HMAC SHA-256 (`X-Hub-Signature-256`).
- Validates delivery uniqueness using `X-GitHub-Delivery` headers to discard duplicates.
- Immediately enqueues a scan task and returns HTTP 202 Accepted.

### 2. SHA Polling Fallback (`POLL_INTERVAL_SECONDS`, default: 60s)
- When public webhook tunnels are unavailable, the background worker polls the branch `HEAD` SHA.
- If `HEAD` differs from the last analyzed SHA, an incremental scan job is enqueued with an idempotency key.

---

## 📦 Durable SQLite Job Queue (`backend/app/services/job_queue.py`)

- **Concurrency Control**: Job claims use `BEGIN IMMEDIATE` transactions to prevent duplicate processing across multiple worker processes.
- **Lease Locks**: Tasks are leased for 120 seconds. If a worker crashes, the lease expires and the task is reclaimed automatically.
- **Dead-Letter Handling**: Tasks failing more than 3 times are marked failed with stack traces preserved in the database.

---

## 📊 Incremental Assessment Diffing (`assessments.py`, `reassessment.py`)

1. **Path Mapping**: Maps changed files to affected skill categories (e.g., `tests/` maps to `Automated Testing`, `models/` maps to `SQL & Relational Databases`).
2. **Selective Execution**: Runs detectors and critics strictly against modified files.
3. **Diff Generation**:
   - Compares rows between `Assessment vN` and `Assessment vN+1`.
   - Tags rows as `improved`, `regressed`, or `unchanged`.
   - Preserves citation IDs for unchanged rows to ensure UI stability.
   - Automatically closes satisfied GitHub Issues generated during previous gap cycles.
