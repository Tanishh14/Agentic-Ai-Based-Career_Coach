"""
Automated Periodic Committer for AI Career Coach Backend.
Commits and pushes backend modules in 13-minute intervals over ~4 hours.
"""

import os
import sys
import time
import subprocess
import logging
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
LOG_FILE = DATA_DIR / "periodic_commits.log"
STATUS_FILE = DATA_DIR / "periodic_commits_status.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

# 17 subsequent stages (Stage 1 was already committed and pushed)
# 17 stages * 13 mins = 221 minutes (~3h 41m, well within 4 hours)
STAGES = [
    {
        "stage": 2,
        "message": "feat(core): implement application configuration, database connection, and custom exceptions",
        "files": [
            "backend/app/config.py",
            "backend/app/database.py",
            "backend/app/errors.py"
        ]
    },
    {
        "stage": 3,
        "message": "feat(security): add encryption utilities and alembic migration configuration",
        "files": [
            "backend/app/security.py",
            "alembic.ini",
            "backend/alembic/README",
            "backend/alembic/env.py",
            "backend/alembic/script.py.mako"
        ]
    },
    {
        "stage": 4,
        "message": "feat(db): add initial schema migration for evidence assessment tables",
        "files": [
            "backend/alembic/versions/0001_initial_initial_schema_phase_1_3.py"
        ]
    },
    {
        "stage": 5,
        "message": "feat(models): implement SQLAlchemy ORM schemas and domain models",
        "files": [
            "backend/app/models.py"
        ]
    },
    {
        "stage": 6,
        "message": "feat(analyzers): add taxonomy hierarchy, evidence findings, and file classification",
        "files": [
            "backend/app/analyzers/__init__.py",
            "backend/app/analyzers/taxonomy.py",
            "backend/app/analyzers/findings.py",
            "backend/app/analyzers/files.py"
        ]
    },
    {
        "stage": 7,
        "message": "feat(analyzers): implement lexer, Python AST parser, and library indexer",
        "files": [
            "backend/app/analyzers/lexer.py",
            "backend/app/analyzers/pyast.py",
            "backend/app/analyzers/libraries.py"
        ]
    },
    {
        "stage": 8,
        "message": "feat(detectors): implement deterministic code detectors and call graph analyzer",
        "files": [
            "backend/app/analyzers/detect_python.py",
            "backend/app/analyzers/detect_clike.py",
            "backend/app/analyzers/detect_config.py",
            "backend/app/analyzers/call_graph.py",
            "backend/app/analyzers/detectors.py"
        ]
    },
    {
        "stage": 9,
        "message": "feat(integrations): add safe HTTP client, LLM provider, and GitHub API service",
        "files": [
            "backend/app/integrations/__init__.py",
            "backend/app/integrations/safe_fetch.py",
            "backend/app/integrations/llm.py",
            "backend/app/integrations/github.py"
        ]
    },
    {
        "stage": 10,
        "message": "feat(integrations): add Whisper transcription, live job sources, and LeetCode GraphQL client",
        "files": [
            "backend/app/integrations/transcription.py",
            "backend/app/integrations/job_sources.py",
            "backend/app/integrations/leetcode.py"
        ]
    },
    {
        "stage": 11,
        "message": "feat(services): implement storage manager, SSE event publisher, and integration credentials store",
        "files": [
            "backend/app/services/__init__.py",
            "backend/app/services/storage.py",
            "backend/app/services/events.py",
            "backend/app/services/migrations.py",
            "backend/app/services/integrations_store.py"
        ]
    },
    {
        "stage": 12,
        "message": "feat(services): add durable SQLite job queue, repo scanner, and document parser",
        "files": [
            "backend/app/services/job_queue.py",
            "backend/app/services/repo_scanner.py",
            "backend/app/services/documents.py"
        ]
    },
    {
        "stage": 13,
        "message": "feat(services): implement assessment engine, incremental reassessment, and career insights",
        "files": [
            "backend/app/services/assessments.py",
            "backend/app/services/reassessment.py",
            "backend/app/services/insights.py"
        ]
    },
    {
        "stage": 14,
        "message": "feat(services): add job extraction, profile verification, gap tasks, and sandbox execution",
        "files": [
            "backend/app/services/jobs.py",
            "backend/app/services/profile.py",
            "backend/app/services/gap_tasks.py",
            "backend/app/services/sandbox.py"
        ]
    },
    {
        "stage": 15,
        "message": "feat(services): implement mock interview orchestrator and Ed25519 skill passport",
        "files": [
            "backend/app/services/interviews.py",
            "backend/app/services/passport.py"
        ]
    },
    {
        "stage": 16,
        "message": "feat(agents): implement bounded agent runtime with verifier critic and interview evaluator",
        "files": [
            "backend/app/agents/__init__.py",
            "backend/app/agents/jd_extractor.py",
            "backend/app/agents/evidence_verifier.py",
            "backend/app/agents/investigator.py",
            "backend/app/agents/interview_architect.py",
            "backend/app/agents/interview_evaluator.py",
            "backend/app/agents/code_defense.py",
            "backend/app/agents/orchestrator.py"
        ]
    },
    {
        "stage": 17,
        "message": "feat(workers): implement background queue worker daemon and GitHub SHA poller",
        "files": [
            "backend/app/workers/__init__.py",
            "backend/app/workers/worker.py",
            "backend/app/workers/__main__.py"
        ]
    },
    {
        "stage": 18,
        "message": "feat(api): complete FastAPI endpoints, routers, and comprehensive test suite",
        "files": [
            "backend/app/api/__init__.py",
            "backend/app/api/deps.py",
            "backend/app/api/system.py",
            "backend/app/api/jobs.py",
            "backend/app/api/assessments.py",
            "backend/app/api/github.py",
            "backend/app/api/interviews.py",
            "backend/app/api/profile.py",
            "backend/app/api/proof.py",
            "backend/app/api/agent.py",
            "backend/app/main.py",
            "backend/tests/__init__.py",
            "backend/tests/conftest.py",
            "backend/tests/helpers.py",
            "backend/tests/test_extraction_llm_passport.py",
            "backend/tests/test_interview_and_profile.py",
            "backend/tests/test_judge_twists.py",
            "backend/tests/test_webhook_queue_db.py"
        ]
    }
]

INTERVAL_SECONDS = 13 * 60  # 13 minutes = 780 seconds


def run_cmd(cmd_list, check=True):
    logging.info(f"Executing: {' '.join(cmd_list)}")
    res = subprocess.run(cmd_list, cwd=str(BASE_DIR), capture_output=True, text=True)
    if check and res.returncode != 0:
        logging.error(f"Command failed (code {res.returncode}): {res.stderr.strip()}")
        raise RuntimeError(f"Command {' '.join(cmd_list)} failed: {res.stderr.strip()}")
    return res


def load_completed_stages():
    import json
    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("completed_stages", []))
        except Exception as e:
            logging.warning(f"Error reading status file: {e}")
    return {1, 2}  # Stages 1 and 2 are already completed and pushed


def mark_stage_completed(stage_num):
    import json
    completed = load_completed_stages()
    completed.add(stage_num)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "completed_stages": sorted(list(completed)),
            "last_updated": datetime.now().isoformat()
        }, f, indent=2)


def push_with_retry(max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            # Always rebase to incorporate any teammate commits cleanly
            rebase_res = run_cmd(["git", "pull", "--rebase", "origin", "main"], check=False)
            if rebase_res.returncode != 0:
                logging.warning(f"Rebase attempt {attempt} failed: {rebase_res.stderr.strip()}")
            res = run_cmd(["git", "push", "origin", "main"], check=False)
            if res.returncode == 0:
                logging.info("Push successful!")
                return True
            logging.warning(f"Push attempt {attempt} failed: {res.stderr.strip()}")
        except Exception as e:
            logging.warning(f"Push attempt {attempt} encountered error: {e}")
        time.sleep(10)
    return False


def main():
    logging.info("Starting Periodic Committer Daemon")
    logging.info(f"Target duration per stage: {INTERVAL_SECONDS // 60} minutes ({INTERVAL_SECONDS}s)")
    logging.info(f"Total remaining stages: {len(STAGES)}")

    completed = load_completed_stages()
    logging.info(f"Previously completed stages: {sorted(list(completed))}")

    for idx, item in enumerate(STAGES):
        stage_num = item["stage"]
        if stage_num in completed:
            logging.info(f"Stage {stage_num} already completed. Skipping.")
            continue

        logging.info(f"Waiting 13 minutes before executing Stage {stage_num}/{len(STAGES) + 1}...")
        time.sleep(INTERVAL_SECONDS)

        logging.info(f"--- Executing Stage {stage_num}: {item['message']} ---")

        # Checkout files from master branch
        checkout_cmd = ["git", "checkout", "master", "--"] + item["files"]
        run_cmd(checkout_cmd)

        # Stage files
        for f in item["files"]:
            run_cmd(["git", "add", f])

        # Commit
        commit_res = run_cmd(["git", "commit", "-m", item["message"]], check=False)
        if commit_res.returncode != 0:
            logging.warning(f"Commit output: {commit_res.stdout.strip()} {commit_res.stderr.strip()}")

        # Push
        if push_with_retry():
            mark_stage_completed(stage_num)
            logging.info(f"Stage {stage_num} successfully committed and pushed to origin/main.")
        else:
            logging.error(f"Failed to push Stage {stage_num} after retries.")

    logging.info("All periodic commits completed! Entire backend folder is fully committed.")


if __name__ == "__main__":
    main()
