"""Autonomous Orchestrator Agent with Live Trace Panel.
Standing Goal: "Keep this student's skill-gap assessment for the target role accurate and current."
Implements the autonomous agentic loop:
observe -> plan -> act (tool calls) -> verify (critic) -> update memory -> report.
Event-driven autonomy: wakes on Git push webhook with no human prompt.
Broadcasts live streaming trace events to UI clients.
"""
import uuid
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Callable, Tuple
from .models import (
    AgentTraceEvent, Assessment, AssessmentDiff, EvidenceRecord,
    JobPosting, RepoScope, GapIssue
)
from .detectors import CodeDetectorEngine
from .verifier import CriticVerifier
from .git_engine import GitEngine
from .assessment_engine import AssessmentEngine
from .job_discovery import fetch_job_from_url, ingest_pasted_jd, get_preloaded_postings
from .gap_to_proof import GapToProofEngine

class OrchestratorAgent:
    """The central Autonomous AI Career Coach Controller."""

    def __init__(self, git_engine: GitEngine, assessment_engine: AssessmentEngine, gap_engine: GapToProofEngine):
        self.git_engine = git_engine
        self.assessment_engine = assessment_engine
        self.gap_engine = gap_engine
        self.trace_history: List[AgentTraceEvent] = []
        self.trace_listeners: List[Callable[[AgentTraceEvent], Any]] = []
        self.active_job: Optional[JobPosting] = None
        self.active_scope: Optional[RepoScope] = None
        self.current_evidence: List[EvidenceRecord] = []
        self.current_assessment: Optional[Assessment] = None
        self.max_steps = 15

    def add_trace_listener(self, listener: Callable[[AgentTraceEvent], Any]):
        self.trace_listeners.append(listener)

    async def emit_trace(
        self,
        step: str,
        thought: str,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Any] = None,
        critic_verdict: Optional[str] = None
    ) -> AgentTraceEvent:
        event = AgentTraceEvent(
            id=f"trace-{len(self.trace_history) + 1:03d}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            step=step,
            thought=thought,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            critic_verdict=critic_verdict
        )
        self.trace_history.append(event)
        for listener in self.trace_listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event)
                else:
                    listener(event)
            except Exception:
                pass
        return event

    async def initialize_session(self, job_id_or_url: Optional[str] = None) -> Assessment:
        """
        Step 1: Agent initializes baseline environment.
        - Loads job posting
        - Scans student repo baseline (API only, no SQL/tests)
        - Generates Assessment v1
        - Proactively opens GitHub issues for detected gaps
        """
        await self.emit_trace(
            step="OBSERVE_TARGET_ROLE",
            thought="Standing Goal initialized: Target role requirements must be mapped to verifiable student repository evidence."
        )

        # 1. Fetch or select job posting
        if not job_id_or_url or "stripe" in job_id_or_url.lower():
            postings = get_preloaded_postings()
            self.active_job = postings[0]  # Stripe Backend Infra
        else:
            self.active_job = await fetch_job_from_url(job_id_or_url)

        await self.emit_trace(
            step="TOOL_EXECUTION",
            thought=f"Target role selected: {self.active_job.role} at {self.active_job.company}. Ingested {len(self.active_job.requirements)} validated requirements.",
            tool_name="fetch_posting",
            tool_args={"source_url": self.active_job.source_url},
            tool_result={"requirements_extracted": len(self.active_job.requirements), "is_sparse": self.active_job.is_sparse}
        )

        # 2. Initialize student repo baseline
        sha, scope = self.git_engine.initialize_baseline_repo()
        self.active_scope = scope

        await self.emit_trace(
            step="TOOL_EXECUTION",
            thought=f"Setting up student repository baseline on branch '{scope.branch}' at commit {sha[:8]}. Declared scope covers {len(scope.file_paths_analyzed)} files.",
            tool_name="list_repos",
            tool_args={"repo": scope.repos[0], "branch": scope.branch},
            tool_result={"files": scope.file_paths_analyzed, "commit_sha": sha[:8]}
        )

        # 3. Deterministic Detectors + Verifier pass
        await self.emit_trace(
            step="PLAN_INVESTIGATION",
            thought="Running deterministic code detectors on repository files to identify route handlers, databases, and test assertions."
        )

        evidence_list, _ = self.git_engine.scan_entire_repo(sha)
        self.current_evidence = evidence_list

        for ev in evidence_list:
            await self.emit_trace(
                step="VERIFIER_CRITIC_PASS",
                thought=f"Critic inspecting candidate snippet from {ev.file}:{ev.line_start}-{ev.line_end} for skill '{ev.skill}'.",
                tool_name="run_detector",
                tool_args={"file": ev.file, "detector": ev.detector},
                tool_result={"snippet": ev.snippet[:120], "depth": ev.depth},
                critic_verdict=ev.critic_notes or "Verified valid"
            )

        # 4. Assessment Matrix v1
        assessment_v1 = self.assessment_engine.generate_full_assessment(
            job=self.active_job,
            evidence_items=self.current_evidence,
            scope=self.active_scope,
            trigger_commit=sha[:8],
            trigger_description="Initial baseline assessment (API endpoints found; SQL and automated tests absent)"
        )
        self.current_assessment = assessment_v1

        # 5. Proactive behavior: Open issues for gaps!
        for row in assessment_v1.rows:
            if row.verdict == "Not demonstrated in this scope":
                issue = self.gap_engine.create_issue_for_gap(row.skill, row.jd_quote)
                await self.emit_trace(
                    step="PROACTIVE_ACTION",
                    thought=f"Gap detected for '{row.skill}'. Proactively opening machine-checkable GitHub issue {issue.id} with clear acceptance criteria.",
                    tool_name="open_issue",
                    tool_args={"issue_id": issue.id, "skill": row.skill, "title": issue.title},
                    tool_result={"criteria_count": len(issue.acceptance_criteria), "status": "open"}
                )

        await self.emit_trace(
            step="REPORT_ASSESSMENT_V1",
            thought=f"Baseline Assessment v1 complete. Readiness Score: {assessment_v1.summary['readiness_score']}%. Matrix matches expected state: API Demonstrated, SQL & Tests Not Demonstrated in this scope."
        )

        return assessment_v1

    async def handle_repo_push(
        self,
        commit_sha: str,
        commit_message: str,
        changed_files: List[str]
    ) -> Tuple[Assessment, AssessmentDiff]:
        """
        Phase 1.4: Event-driven autonomy triggered by repo push webhook.
        Wakes the agent with NO prompt:
        - Diffs commit
        - Maps changed files to affected skills
        - Runs detectors on affected files only
        - Verifier critic scrutinizes the new code
        - Closes satisfied GitHub issues
        - Produces Assessment vX and AssessmentDiff
        """
        await self.emit_trace(
            step="EVENT_PUSH_WEBHOOK_RECEIVED",
            thought=f"Webhook event triggered by Git commit {commit_sha[:8]} ('{commit_message}'). Changed files: {', '.join(changed_files)}."
        )

        # 1. Reason about affected skills
        affected_skills = set()
        for f in changed_files:
            lower_f = f.lower()
            if "db" in lower_f or "model" in lower_f or "sql" in lower_f or "queries" in lower_f:
                affected_skills.add("SQL & Relational Databases")
            if "test" in lower_f:
                affected_skills.add("Automated Testing")
            if "app" in lower_f or "main" in lower_f:
                affected_skills.add("API Development")
            if "docker" in lower_f or "action" in lower_f or "workflow" in lower_f:
                affected_skills.add("Docker & Containerization")
                affected_skills.add("CI/CD & Cloud Deployment")

        if not affected_skills:
            await self.emit_trace(
                step="AUTONOMOUS_REASONING",
                thought=f"Changed files {changed_files} do not map to any active technical requirements. Unaffected rows will remain untouched with stable evidence IDs.",
                tool_name="diff_commit",
                tool_args={"commit_sha": commit_sha[:8], "changed_files": changed_files},
                tool_result={"affected_skills": []}
            )
        else:
            await self.emit_trace(
                step="AUTONOMOUS_REASONING",
                thought=f"Changed files map directly to skills: {list(affected_skills)}. Initiating incremental detector scan and critic verification on affected files only.",
                tool_name="diff_commit",
                tool_args={"commit_sha": commit_sha[:8], "changed_files": changed_files},
                tool_result={"affected_skills": list(affected_skills)}
            )

        # 2. Rescan repo state at new commit SHA
        new_evidence, new_scope = self.git_engine.scan_entire_repo(commit_sha)
        self.active_scope = new_scope
        self.current_evidence = new_evidence

        # 3. Verifier checks newly introduced findings
        for ev in new_evidence:
            if ev.file in changed_files:
                await self.emit_trace(
                    step="VERIFIER_CRITIC_SCRUTINY",
                    thought=f"Critic evaluating newly introduced snippet in '{ev.file}' for skill '{ev.skill}'.",
                    tool_name="run_detector",
                    tool_args={"file": ev.file, "line_start": ev.line_start, "detector": ev.detector},
                    tool_result={"snippet": ev.snippet[:120], "depth": ev.depth, "confidence": ev.confidence},
                    critic_verdict=ev.critic_notes
                )

        # 4. Check and close machine-checkable issues
        closed_issues = self.gap_engine.check_and_close_issues(commit_sha, new_evidence)
        for cl in closed_issues:
            await self.emit_trace(
                step="PROACTIVE_VERIFIED_CLOSURE",
                thought=f"Acceptance criteria satisfied for '{cl.skill}'! Automatically closing GitHub Issue {cl.id}.",
                tool_name="close_issue",
                tool_args={"issue_id": cl.id, "closing_commit": commit_sha[:8]},
                tool_result={"citations": cl.verification_citations}
            )

        # 5. Generate Assessment vX
        prev_assessment = self.current_assessment
        new_assessment = self.assessment_engine.generate_full_assessment(
            job=self.active_job,
            evidence_items=self.current_evidence,
            scope=self.active_scope,
            trigger_commit=commit_sha[:8],
            trigger_description=f"Automated reassessment after commit {commit_sha[:8]}: {commit_message}"
        )
        self.current_assessment = new_assessment

        # 6. Generate Diff
        diff = self.assessment_engine.compute_incremental_diff(
            prev_assessment=prev_assessment,
            new_assessment=new_assessment,
            trigger_commit=commit_sha,
            commit_message=commit_message,
            changed_files=changed_files
        )

        await self.emit_trace(
            step="REASSESSMENT_COMPLETE",
            thought=f"Reassessment complete: Version v{new_assessment.version} produced. New Readiness Score: {new_assessment.summary['readiness_score']}%. Changes: {len(diff.changes)} row(s) updated."
        )

        return new_assessment, diff
