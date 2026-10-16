# AI Career Coach — REST API Specification & Endpoints

This document specifies the REST API endpoints provided by the FastAPI application layer in `backend/app/api`.

---

## 🌐 Base URL & Protocol

- **Local Development**: `http://127.0.0.1:8000`
- **Interactive Documentation**: `http://127.0.0.1:8000/docs` (Swagger UI) / `/redoc` (ReDoc)
- **Real-Time Stream**: `GET /api/events` (Server-Sent Events)

---

## 🔑 Authentication & Security

- Repository tokens (GitHub PAT) and external API keys (Groq, OpenCode, Adzuna, SerpApi) are stored encrypted in SQLite using AES-GCM-256 via `backend/app/security.py`.
- Cryptographic keys reside in `data/keys/` and are never exposed via API endpoints.

---

## 📋 Endpoints Overview

### 1. Job Ingestion & Discovery (`/api/jobs`)

| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/jobs/scrape` | Scrape job description from Greenhouse, Lever, Ashby, or public URL. |
| `POST` | `/api/jobs/text` | Parse raw text or pasted job description. |
| `GET` | `/api/jobs` | List recent job targets analyzed by the user. |
| `GET` | `/api/jobs/{id}` | Retrieve normalized requirements (8-15 quotes) and raw text. |
| `POST` | `/api/jobs/search` | Search external job boards (Adzuna, Remotive, Arbeitnow). |

### 2. Repositories & Scopes (`/api/github`)

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/api/github/repos` | List candidate repositories accessible with current PAT. |
| `POST` | `/api/github/scope` | Create an analysis scope (target repo, branch, path inclusion/exclusions). |
| `GET` | `/api/github/scope/{id}` | Inspect analysis scope manifest, pinned commit SHA, and analyzed files. |
| `POST` | `/api/github/webhook` | Webhook receiver for push events (HMAC signature validated via `X-Hub-Signature-256`). |

### 3. Competency Assessment & Diffs (`/api/assessments`)

| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/assessments/generate` | Enqueue a baseline assessment (v1) comparing job requirements against scope. |
| `GET` | `/api/assessments/{id}` | Retrieve gap matrix rows, verdict per requirement, and evidence citations. |
| `GET` | `/api/assessments/{id}/diff` | Side-by-side incremental diff between assessment versions (`v1 -> v2`). |

### 4. Mock Technical Interviews (`/api/interviews`)

| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/interviews/session` | Start a targeted mock interview session for a specific job and gap set. |
| `GET` | `/api/interviews/{id}` | Retrieve active interview state, current question, and duration timer. |
| `POST` | `/api/interviews/{id}/audio` | Upload candidate spoken audio for local Whisper transcription. |
| `POST` | `/api/interviews/{id}/evaluate` | Score interview responses against rubric items and generate feedback report. |

### 5. Candidate Code Defense (`/api/proof`)

| Method | Path | Description |
| :--- | :--- | :--- |
| `POST` | `/api/proof/defense/generate`| Generate targeted code defense questions probing candidate-authored snippets. |
| `POST` | `/api/proof/defense/{id}/answer` | Submit candidate explanation to verify genuine code authorship. |
| `POST` | `/api/proof/tasks/issue` | Create a GitHub issue on candidate's repository with automated acceptance criteria. |
| `POST` | `/api/proof/sandbox/run` | Execute candidate test suite inside an isolated Docker sandbox container. |

### 6. Cryptographic Skill Passport (`/api/passport`)

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/api/passport/active` | Get the candidate's active Ed25519-signed Skill Passport. |
| `GET` | `/api/passport/export` | Download standalone canonical JSON proof file. |
| `POST` | `/api/passport/verify` | Cryptographically verify a Skill Passport JSON artifact and its signature. |

### 7. Reactive Events & Audit Trail (`/api/events`, `/api/agent`)

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/api/events` | Server-Sent Events (SSE) stream tailing the durable `event_log` table. |
| `GET` | `/api/agent/trace` | Retrieve real-time bounded agent execution traces and tool logs. |
| `GET` | `/api/system/health` | Service health check, queue depth, and background worker heartbeat. |
