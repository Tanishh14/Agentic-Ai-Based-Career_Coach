# AI Career Coach — System Architecture & Specification

An Autonomous AI Career Coach with live GitHub repository change detection, deterministic code detectors, critic verifier pass, Code-Defense interviews, and Verified Skill Passport.

---

## 🏗️ Visual System Architecture

![AI Career Coach System Architecture](docs/architecture.png)

```mermaid
graph TD
    subgraph Data Sources ["Live Job Ingestion"]
        A[Job Postings: Greenhouse / Lever / Web] -->|Anti-Hallucination Validator| B[8-15 Normalized Requirements]
    end

    subgraph Evidence Pipeline ["Deterministic Evidence Engine"]
        C[Candidate GitHub Repo] -->|Deterministic Detectors| D[AST & Regex Hits]
        D -->|Critic Verifier Pass| E[Qualified Code Evidence]
    end

    subgraph Assessment ["Core Assessment Matrix"]
        B & E -->|Assessment Engine| F[4-Verdict Gap Matrix]
    end

    subgraph Agent Runtime ["Autonomous Orchestrator Agent"]
        G[Live Git Push / Webhook] -->|Event Trigger| H[Autonomous Orchestrator Agent]
        H -->|Incremental Reassessment| F
        H -->|Diff Generator| I[Versioned Diff v1->v2]
        H -->|Gap-to-Proof Loop| J[GitHub Issues with Acceptance Criteria]
    end

    subgraph Verification ["Interview & Cryptographic Proof"]
        F -->|Phase 3.1| K[Code-Defense Interview Engine]
        K -->|Probes Candidate Code| L[Understood Status Awarded]
        F & L -->|Phase 3.3| M[Verified Skill Passport]
    end
```

---

## 🧩 Architectural Subsystems

### 1. Ingestion & Anti-Hallucination Validation (`backend/job_discovery.py`)
- Real job posting ingestion from Greenhouse, Lever, Adzuna, and public board APIs.
- Strict anti-hallucination substring validator: every requirement quote must match source text verbatim or be purged immediately.

### 2. Deterministic AST & Regex Detectors (`backend/detectors.py`)
- Native Python AST analysis targeting route handlers (FastAPI, Flask), relational database schemas/queries (SQLAlchemy, SQL queries), and unit test assertions (pytest, unittest).
- Comment and docstring stripper ensuring superficial mentions in comments or markdown files are never credited.

### 3. Critic & Verifier Pass (`backend/verifier.py`)
- Adversarial evaluation of detected snippets to filter out trivial stubs, `pass` blocks, and mock code.
- Assigns depth labels: `trivial`, `surface`, `working`, and `production`.

### 4. Autonomous Controller Agent (`backend/orchestrator.py`)
- Standing Goal: *"Keep this candidate's skill-gap assessment for the target role accurate and current."*
- Bounded execution loop: `observe -> plan -> act (tools) -> verify -> update memory -> report`.
- Streams agent thoughts, detector executions, and critic verdicts in real-time.

### 5. Live Git Engine & Reassessment Loop (`backend/git_engine.py`)
- Webhook receiver (`/api/git/webhook`) and background poller tracking real commit SHAs.
- Incremental reassessment triggers only for modified files, computing versioned diffs (`v1 -> v2`).

### 6. Code-Defense Interview Engine (`backend/interview_engine.py`)
- Synthesizes technical interview questions derived directly from candidate-authored code.
- Assesses comprehension, speech pace (WPM), filler frequency, and awards the **Understood** flag.

### 7. Verified Skill Passport (`backend/passport.py`)
- Cryptographically verifiable passport linking candidate accomplishments to verified commit SHAs, line numbers, and interview validation timestamps.
