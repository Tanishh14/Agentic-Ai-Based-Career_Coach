# AI Career Coach — evidence-driven (W-PS-2)

Compares **real job requirements** with **evidence in your real GitHub repositories**, explains every gap with
file/line/commit citations, and **reassesses automatically when you push code** (webhook or SHA polling).
On top of that loop: LeetCode and resume/LinkedIn signals, JD-specific timed mock interviews with local
speech-to-text, code-defense interviews about your own code, gap-to-proof GitHub issues, an optional sandbox,
and an Ed25519-signed, revocable Skill Passport.

**No mock data.** Nothing is seeded. Jobs come from a URL you give, text/PDF you paste or upload, or live
provider APIs. Repositories come from GitHub. Missing integrations show "not configured", never fake results.

```
real JD ─▶ verbatim requirements ─▶ commit-pinned repo scan ─▶ critic ─▶ gap matrix (v1)
                                                    ▲                          │
          push ─▶ webhook (HMAC) / SHA poll ─▶ durable SQLite job ─▶ diff ─▶ affected rows only ─▶ v2 + diff
```

## Quick start (Windows, tested)

```bash
python -m venv .venv-win --system-site-packages
.venv-win\Scripts\python -m pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
python run.py
```

`run.py` applies migrations, then starts the **API** (http://127.0.0.1:8000, docs at `/docs`), the **worker**
(queue consumer + GitHub poller) and the **UI** (http://localhost:3000). `python run.py --prod` builds and serves the UI.

Requirements: Python 3.10+, Node 18+, `ffmpeg` on PATH for local transcription. Docker is optional (sandbox runs).

## Accounts and leaderboard

- Students register and log in with **a name and a password only**. Accounts and sessions are stored in the same
  SQLite database (`users`, `user_sessions`; scrypt password hashes, hashed session tokens). Every API call is scoped
  to the logged-in student; one student cannot see another's jobs, repos, interviews or evidence.
- The first account created on an install that already has data adopts that data.
- **Leaderboard** (sidebar → Step 4): students preparing for the same real posting (matched by URL, provider job id or
  identical text) are ranked by `0.60 × code-evidence coverage + 0.25 × mock-interview average + 0.15 × code-defense average`.
  Anything not attempted counts as 0. Students can join a job another student added, and can hide themselves in Settings.
  It is a coaching ranking, not a hiring prediction.

## Configure (all optional except a real job + repo)

Use **Settings → Integrations & privacy** in the UI (credentials are verified, then stored encrypted) or `.env`
(copy `.env.example`).

| Integration | Needed for | How |
|---|---|---|
| GitHub token | private repos, issues, webhooks, higher rate limits | Fine-grained PAT: Contents read + Metadata read (+ Issues r/w, Webhooks r/w). Public repos work anonymously (contents via raw.githubusercontent.com). |
| LLM (OpenAI-compatible) | richer extraction, interview questions, evaluation, code-defense, investigator agent | OpenCode Zen `https://opencode.ai/zen/v1`, Groq `https://api.groq.com/openai/v1`, OpenRouter, Gemini, or local Ollama `http://127.0.0.1:11434/v1` (tested: `qwen2.5:3b-instruct` primary — plan ~16 s, evaluation ~8 s on an RTX 3050 laptop — with `llama3:8b-instruct-q4_K_M` as fallback). |
| Speech-to-text | spoken interview answers | Default: local `openai-whisper` (`base`), audio never leaves the machine. Or any `/audio/transcriptions` API. |
| Adzuna / SerpApi | extra job search; SerpApi surfaces LinkedIn/Naukri links | keys in Settings. Remotive, Arbeitnow, Remote OK, Greenhouse, Lever need no key. |
| Webhook | instant push detection | Set `APP_PUBLIC_URL` to a public https tunnel (e.g. `ngrok http 8000`), then **Repositories → Webhook**. Without it, SHA polling (`POLL_INTERVAL_SECONDS`, default 60) detects pushes automatically. |

Without an LLM the app still works end-to-end: taxonomy extraction (verbatim quotes), template interview
questions built from your real JD/gaps/code, heuristic keyword-coverage feedback — all labelled as such.

## Demo walkthrough (your real job + your real repo)

1. **Job discovery** → paste a job URL (Greenhouse/Lever/Ashby use their APIs; LinkedIn/Naukri → paste the text, the URL is kept as provenance) or search live sources and click *Analyse & target*.
2. **Job & requirements** → each requirement links to its exact quote highlighted in the stored text.
3. **Repositories & scope** → add your repo (branch, include/exclude). The worker scans the pinned SHA; the *scope manifest* lists files analysed, ignored (with reasons) and detectors run.
4. **Evidence & skill gaps** → requirement → verdict → cited evidence (GitHub permalinks) → explanation and exact scope searched.
5. Push a real commit (e.g. add parameterised SQL + two real tests). Within the poll interval (or instantly with a webhook) the agent compares the commits, re-runs detectors on changed files only, and creates a new assessment; the gap page shows a live banner.
6. **History & commit diffs** → side-by-side before/after with the commit SHA, changed paths and moved rows. Delete the tests in another commit to see the regression.
7. Phase 2/3: Profile & LeetCode, Mock interview, Code defense, Gap-to-proof tasks (issue only after you type the repo name to confirm), Skill passport.

## How judging scenarios are handled

| Scenario | Behaviour |
|---|---|
| Empty `test_*.py`, `def test_x(): pass`, `assert True` | rejected by detector/critic → no testing verdict |
| SQL in README / comments / docstrings | never parsed as code (docs skipped, AST ignores comments/docstrings) |
| SQL string never executed | ambiguous → at most PARTIAL; the agent investigator can resolve it only with a re-checked citation |
| Dependency listed but unused | PARTIAL ("declaration only"), never DEMONSTRATED |
| CSS/docs-only commit | evidence IDs and verdict timestamps unchanged; diff shows no row changes |
| Tests deleted/trivialized | verdict regresses, diff marked *regressed*, old evidence marked historical |
| Rename / delete | old evidence superseded with reason; no broken current citations |
| Duplicate / out-of-order webhooks | unique delivery IDs; idempotency key per SHA; worker reconciles with the real branch head |
| Invalid signature | 401, logged, nothing queued |
| New commit during a scan | queued separately; next scan compares last analysed → real head |
| LLM invalid JSON / rate limit | retries with the validation error, then fallback provider, then deterministic data with a visible warning |
| Sparse JD | fewer requirements + warning; nothing invented; LLM quotes not found verbatim are rejected |
| Scope change | new scope version → new assessment version |

All of these are automated tests: `python -m pytest -q` (37 tests; GitHub is replaced by a scripted test double
**only inside `backend/tests`**). Live checks were also run against a real Greenhouse posting and the real
`fastapi/full-stack-fastapi-template` repository history (initial scan, real poll detection, incremental diff).

## Architecture

- `backend/app/analyzers` — taxonomy (versioned), file classification, Python AST detectors, comment-stripped lexical
  detectors for JS/TS/Java/Go/C#/Rust/PHP/Ruby, config/CI/Docker/k8s/Terraform/manifest detectors, reference index.
- `backend/app/agents` — bounded agent runtime with a user-safe action trace (no chain-of-thought), JD extractor,
  evidence critic, investigator (tool-using LLM loop with deterministic citation checks), interview architect/evaluator, code defense.
- `backend/app/services` — commit-pinned scanner (full + incremental), assessments/diffs, reassessment loop, durable
  SQLite queue, jobs/discovery, profile claims, interviews, gap tasks, sandbox, passport, insights.
- `backend/app/workers/worker.py` — queue consumer, lease recovery, SHA poller, heartbeat.
- `frontend/` — Next.js 14 App Router; live updates over SSE (`/api/events`, tails the `event_log` table so it works across processes).

### SQLite

- File: `data/career_coach.db` (`DATABASE_URL` to change). Schema only via Alembic (`backend/alembic`); migrations run on start under a file lock.
- Every connection: `foreign_keys=ON`, WAL (falls back to DELETE journal if unsupported), `busy_timeout=10000`.
- Timestamps: ISO-8601 UTC text. IDs: UUID hex text. JSON: validated JSON text.
- Queue claims use `BEGIN IMMEDIATE`; no network calls inside transactions.
- Backup: stop the app and copy `data/career_coach.db` (or `sqlite3 data/career_coach.db ".backup backup.db"`). Restore by copying back.
- Media, blob cache and signing keys live under `data/` (git-ignored). Audio is deleted after transcription unless you opt in.

## Limitations (honest)

- Static analysis shows code exists and how it's wired; it doesn't prove it runs. Reachability is name-based.
- Non-Python languages use lexical detectors (comment-stripped patterns + bracket matching), shallower than the Python AST.
- Soft skills and DSA aren't verifiable from code; they appear as "not demonstrated in this scope" with that explanation.
- LeetCode data uses LeetCode's unofficial public GraphQL endpoint (best effort; manual entry is labelled user-supplied).
- Accounts are local to this installation (no password reset or e-mail; an admin can delete the SQLite row).
- Sandbox needs Docker; without it the feature is disabled and explains why.
- Interview scores and the coverage indicator are coaching heuristics, not hiring predictions.
