"""SQLAlchemy 2.x models. SQLite-only, portable types (TEXT ids, JSONText, ISO UTC text)."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, JSONText, new_id, utcnow


def pk() -> Mapped[str]:
    return mapped_column(String(32), primary_key=True, default=new_id)


def created() -> Mapped[str]:
    return mapped_column(String(40), default=utcnow, nullable=False)


# ---------------------------------------------------------------- core


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = pk()
    display_name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[Optional[str]] = mapped_column(String(60), unique=True)  # lower-cased login name
    password_hash: Mapped[Optional[str]] = mapped_column(String(300))
    settings: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    created_at: Mapped[str] = created()
    last_login_at: Mapped[Optional[str]] = mapped_column(String(40))


class UserSession(Base):
    """Login sessions. Only a SHA-256 of the bearer token is stored."""

    __tablename__ = "user_sessions"
    __table_args__ = (Index("ix_session_user", "user_id"),)
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[str] = created()
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    last_seen_at: Mapped[Optional[str]] = mapped_column(String(40))


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_integration_user_provider"),)
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    encrypted_credential: Mapped[Optional[str]] = mapped_column(Text)
    config: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    scopes: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    status: Mapped[str] = mapped_column(String(30), default="disconnected")
    last_sync_at: Mapped[Optional[str]] = mapped_column(String(40))
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = created()
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow, onupdate=utcnow)


class JobSearchRun(Base):
    __tablename__ = "job_search_runs"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filters: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    providers: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    created_at: Mapped[str] = created()


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint("user_id", "canonical_url", name="uq_posting_user_url"),
        UniqueConstraint("user_id", "provider", "external_id", name="uq_posting_provider_ext"),
        Index("ix_posting_user_status", "user_id", "status"),
    )
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    search_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("job_search_runs.id", ondelete="SET NULL"))
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    canonical_url: Mapped[Optional[str]] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(60), default="unknown")
    provider: Mapped[str] = mapped_column(String(60), default="user")
    ingestion_method: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(200))
    company: Mapped[Optional[str]] = mapped_column(String(300))
    title: Mapped[Optional[str]] = mapped_column(String(300))
    location: Mapped[Optional[str]] = mapped_column(String(300))
    employment_type: Mapped[Optional[str]] = mapped_column(String(100))
    posted_at: Mapped[Optional[str]] = mapped_column(String(60))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    description_is_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieved_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    extraction_warning: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[Any] = mapped_column("metadata", JSONText, nullable=True, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="listing")  # listing | analyzed
    is_tracked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = created()


class Requirement(Base):
    __tablename__ = "requirements"
    __table_args__ = (Index("ix_req_posting", "posting_id"),)
    id: Mapped[str] = pk()
    posting_id: Mapped[str] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False)
    skill_key: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    original_term: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(40), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    detector_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    out_of_scope: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = created()


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("user_id", "github_id", name="uq_repo_user_github"),)
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    github_id: Mapped[str] = mapped_column(String(40), nullable=False)
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name: Mapped[str] = mapped_column(String(400), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    html_url: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[Optional[str]] = mapped_column(Text)
    languages: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    connection_status: Mapped[str] = mapped_column(String(30), default="connected")
    webhook_id: Mapped[Optional[str]] = mapped_column(String(40))
    webhook_status: Mapped[Optional[str]] = mapped_column(String(60))
    created_at: Mapped[str] = created()


class AnalysisScope(Base):
    __tablename__ = "analysis_scopes"
    __table_args__ = (UniqueConstraint("repository_id", "version", name="uq_scope_repo_version"),)
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    branch: Mapped[str] = mapped_column(String(200), nullable=False)
    include_patterns: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    exclude_patterns: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_analyzed_sha: Mapped[Optional[str]] = mapped_column(String(64))
    last_snapshot_id: Mapped[Optional[str]] = mapped_column(String(32))
    last_polled_at: Mapped[Optional[str]] = mapped_column(String(40))
    last_poll_etag: Mapped[Optional[str]] = mapped_column(String(200))
    last_seen_head_sha: Mapped[Optional[str]] = mapped_column(String(64))
    last_poll_error: Mapped[Optional[str]] = mapped_column(Text)
    last_event_at: Mapped[Optional[str]] = mapped_column(String(40))
    last_event_kind: Mapped[Optional[str]] = mapped_column(String(40))
    scan_status: Mapped[str] = mapped_column(String(30), default="pending")
    scan_error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = created()


class ScopeSnapshot(Base):
    __tablename__ = "scope_snapshots"
    __table_args__ = (UniqueConstraint("scope_id", "commit_sha", name="uq_snapshot_scope_sha"),)
    id: Mapped[str] = pk()
    scope_id: Mapped[str] = mapped_column(ForeignKey("analysis_scopes.id", ondelete="CASCADE"), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    base_snapshot_id: Mapped[Optional[str]] = mapped_column(String(32))
    commit_message: Mapped[Optional[str]] = mapped_column(Text)
    commit_date: Mapped[Optional[str]] = mapped_column(String(40))
    tree_file_count: Mapped[int] = mapped_column(Integer, default=0)
    scanned_file_count: Mapped[int] = mapped_column(Integer, default=0)
    languages: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    detectors: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    ignored: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    ignored_count: Mapped[int] = mapped_column(Integer, default=0)
    trigger: Mapped[str] = mapped_column(String(30), default="initial")
    mode: Mapped[str] = mapped_column(String(20), default="full")  # full | incremental
    changed_paths: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    created_at: Mapped[str] = created()


class SnapshotFile(Base):
    __tablename__ = "snapshot_files"
    __table_args__ = (UniqueConstraint("snapshot_id", "path", name="uq_snapshot_file"),)
    id: Mapped[str] = pk()
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("scope_snapshots.id", ondelete="CASCADE"), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    blob_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[Optional[str]] = mapped_column(String(40))
    size: Mapped[int] = mapped_column(Integer, default=0)
    file_kind: Mapped[str] = mapped_column(String(30), default="implementation")


class CommitRecord(Base):
    __tablename__ = "commits"
    __table_args__ = (UniqueConstraint("repository_id", "sha", name="uq_commit_repo_sha"),)
    id: Mapped[str] = pk()
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    sha: Mapped[str] = mapped_column(String(64), nullable=False)
    parents: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    message: Mapped[Optional[str]] = mapped_column(Text)
    author_date: Mapped[Optional[str]] = mapped_column(String(40))
    html_url: Mapped[Optional[str]] = mapped_column(Text)
    changed_paths: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    created_at: Mapped[str] = created()


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"
    __table_args__ = (
        Index("ix_evidence_scope_status", "scope_id", "status"),
        Index("ix_evidence_skill", "skill_key"),
    )
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scope_id: Mapped[str] = mapped_column(ForeignKey("analysis_scopes.id", ondelete="CASCADE"), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("scope_snapshots.id", ondelete="CASCADE"), nullable=False)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    skill_key: Mapped[str] = mapped_column(String(80), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(400), nullable=False)
    branch: Mapped[str] = mapped_column(String(200), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    blob_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    snippet: Mapped[str] = mapped_column(Text, nullable=False)
    snippet_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    detector: Mapped[str] = mapped_column(String(80), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(20), nullable=False)
    node_type: Mapped[Optional[str]] = mapped_column(String(80))
    signal_level: Mapped[str] = mapped_column(String(40), nullable=False)
    depth: Mapped[str] = mapped_column(String(20), nullable=False)
    file_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    reachability: Mapped[str] = mapped_column(String(30), default="unknown")
    verification_status: Mapped[str] = mapped_column(String(20), nullable=False)
    verification_notes: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    llm_corroboration: Mapped[Any] = mapped_column(JSONText, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    permalink: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="current")  # current | historical
    superseded_at_sha: Mapped[Optional[str]] = mapped_column(String(64))
    superseded_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = created()


class Assessment(Base):
    __tablename__ = "assessments"
    __table_args__ = (UniqueConstraint("job_posting_id", "version", name="uq_assessment_job_version"),)
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    job_posting_id: Mapped[str] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False)
    trigger_ref: Mapped[Optional[str]] = mapped_column(String(200))
    trigger_commit_sha: Mapped[Optional[str]] = mapped_column(String(64))
    scopes: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    summary: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    agent_run_id: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[str] = created()


class AssessmentRow(Base):
    __tablename__ = "assessment_rows"
    __table_args__ = (Index("ix_row_assessment", "assessment_id"),)
    id: Mapped[str] = pk()
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False)
    skill_key: Mapped[str] = mapped_column(String(80), nullable=False)
    verdict: Mapped[str] = mapped_column(String(40), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_note: Mapped[str] = mapped_column(Text, default="")
    signal_level: Mapped[Optional[str]] = mapped_column(String(40))
    search_trace: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    signals: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    recommendation: Mapped[Optional[str]] = mapped_column(Text)
    evaluated_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    carried_from_row_id: Mapped[Optional[str]] = mapped_column(String(32))
    changed: Mapped[bool] = mapped_column(Boolean, default=True)


class AssessmentRowEvidence(Base):
    __tablename__ = "assessment_row_evidence"
    row_id: Mapped[str] = mapped_column(ForeignKey("assessment_rows.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence_records.id", ondelete="CASCADE"), primary_key=True)


class AssessmentDiff(Base):
    __tablename__ = "assessment_diffs"
    __table_args__ = (UniqueConstraint("to_assessment_id", name="uq_diff_to"),)
    id: Mapped[str] = pk()
    from_assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    to_assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(30), nullable=False)
    trigger_ref: Mapped[Optional[str]] = mapped_column(String(200))
    commit_sha: Mapped[Optional[str]] = mapped_column(String(64))
    commit_url: Mapped[Optional[str]] = mapped_column(Text)
    changed_paths: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    affected_skills: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    row_deltas: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    created_at: Mapped[str] = created()


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = pk()
    delivery_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    event: Mapped[str] = mapped_column(String(60), default="")
    signature_status: Mapped[str] = mapped_column(String(20), nullable=False)
    repository_full_name: Mapped[Optional[str]] = mapped_column(String(400))
    ref: Mapped[Optional[str]] = mapped_column(String(300))
    after_sha: Mapped[Optional[str]] = mapped_column(String(64))
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    processed_at: Mapped[Optional[str]] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="received")
    error: Mapped[Optional[str]] = mapped_column(Text)
    job_id: Mapped[Optional[str]] = mapped_column(String(32))


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (Index("ix_job_status_next", "status", "next_run_at"),)
    id: Mapped[str] = pk()
    user_id: Mapped[Optional[str]] = mapped_column(String(32))
    job_type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    next_run_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    lease_expires_at: Mapped[Optional[str]] = mapped_column(String(40))
    claimed_by: Mapped[Optional[str]] = mapped_column(String(100))
    result: Mapped[Any] = mapped_column(JSONText, nullable=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = created()
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow, onupdate=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = pk()
    user_id: Mapped[Optional[str]] = mapped_column(String(32))
    trigger: Mapped[str] = mapped_column(String(60), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    ended_at: Mapped[Optional[str]] = mapped_column(String(40))
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    model_provider: Mapped[Optional[str]] = mapped_column(String(100))
    model_id: Mapped[Optional[str]] = mapped_column(String(200))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    error_summary: Mapped[Optional[str]] = mapped_column(Text)


class AgentAction(Base):
    __tablename__ = "agent_actions"
    __table_args__ = (Index("ix_action_run", "run_id"),)
    id: Mapped[str] = pk()
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    tool: Mapped[Optional[str]] = mapped_column(String(80))
    input_summary: Mapped[str] = mapped_column(Text, default="")
    entity_ids: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="ok")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = created()


class EventLog(Base):
    """Append-only event stream consumed by the SSE endpoint (works across processes)."""

    __tablename__ = "event_log"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    created_at: Mapped[str] = created()


class SystemState(Base):
    """Small key/value table (worker heartbeat, poller status)."""

    __tablename__ = "system_state"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow, onupdate=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = pk()
    user_id: Mapped[Optional[str]] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    detail: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    created_at: Mapped[str] = created()


# ---------------------------------------------------------------- phase 2


class ProfileDocument(Base):
    __tablename__ = "profile_documents"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # resume | linkedin_export | pasted_profile
    filename: Mapped[Optional[str]] = mapped_column(String(300))
    mime: Mapped[Optional[str]] = mapped_column(String(120))
    storage_key: Mapped[Optional[str]] = mapped_column(String(200))
    size: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    page_offsets: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    parse_method: Mapped[str] = mapped_column(String(60), default="")
    parse_warning: Mapped[Optional[str]] = mapped_column(Text)
    structured: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    consent_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = created()


class ProfileClaim(Base):
    __tablename__ = "profile_claims"
    id: Mapped[str] = pk()
    document_id: Mapped[str] = mapped_column(ForeignKey("profile_documents.id", ondelete="CASCADE"), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(30), nullable=False)  # CLAIMED_RESUME | CLAIMED_PROFILE
    claim_type: Mapped[str] = mapped_column(String(30), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    skill_key: Mapped[Optional[str]] = mapped_column(String(80))
    page: Mapped[Optional[int]] = mapped_column(Integer)
    span_start: Mapped[Optional[int]] = mapped_column(Integer)
    span_end: Mapped[Optional[int]] = mapped_column(Integer)
    section: Mapped[Optional[str]] = mapped_column(String(120))
    extraction_method: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[str] = created()


class LeetCodeSnapshot(Base):
    __tablename__ = "leetcode_snapshots"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    fields: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    field_status: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text)
    retrieved_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class MediaFile(Base):
    __tablename__ = "media_files"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    mime: Mapped[str] = mapped_column(String(120), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    retention_status: Mapped[str] = mapped_column(String(30), default="retained")
    created_at: Mapped[str] = created()
    deleted_at: Mapped[Optional[str]] = mapped_column(String(40))


class InterviewSession(Base):
    __tablename__ = "interview_sessions"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # mock | code_defense
    job_posting_id: Mapped[Optional[str]] = mapped_column(ForeignKey("job_postings.id", ondelete="SET NULL"))
    assessment_id: Mapped[Optional[str]] = mapped_column(ForeignKey("assessments.id", ondelete="SET NULL"))
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    plan_generator: Mapped[str] = mapped_column(String(200), default="")
    plan_frozen_at: Mapped[Optional[str]] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="planned")
    consent: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    current_index: Mapped[int] = mapped_column(Integer, default=0)
    settings: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    started_at: Mapped[Optional[str]] = mapped_column(String(40))
    ended_at: Mapped[Optional[str]] = mapped_column(String(40))
    report: Mapped[Any] = mapped_column(JSONText, nullable=True)
    created_at: Mapped[str] = created()


class InterviewQuestion(Base):
    __tablename__ = "interview_questions"
    __table_args__ = (Index("ix_question_session", "session_id", "order_index"),)
    id: Mapped[str] = pk()
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False)
    order_index: Mapped[float] = mapped_column(Float, nullable=False)
    qtype: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    target_skill_key: Mapped[Optional[str]] = mapped_column(String(80))
    requirement_id: Mapped[Optional[str]] = mapped_column(String(32))
    evidence_id: Mapped[Optional[str]] = mapped_column(String(32))
    assessment_row_id: Mapped[Optional[str]] = mapped_column(String(32))
    provenance: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")
    prep_seconds: Mapped[int] = mapped_column(Integer, default=30)
    answer_seconds: Mapped[int] = mapped_column(Integer, default=120)
    rubric: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    max_followups: Mapped[int] = mapped_column(Integer, default=1)
    parent_question_id: Mapped[Optional[str]] = mapped_column(String(32))
    followup_depth: Mapped[int] = mapped_column(Integer, default=0)
    code_context: Mapped[Any] = mapped_column(JSONText, nullable=True)
    challenge: Mapped[Any] = mapped_column(JSONText, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[str] = created()


class InterviewAnswer(Base):
    __tablename__ = "interview_answers"
    id: Mapped[str] = pk()
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[str] = mapped_column(ForeignKey("interview_questions.id", ondelete="CASCADE"), nullable=False, unique=True)
    answer_source: Mapped[str] = mapped_column(String(20), nullable=False)  # audio | typed | none
    transcript_raw: Mapped[Optional[str]] = mapped_column(Text)
    transcript_final: Mapped[Optional[str]] = mapped_column(Text)
    transcript_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    transcription_provider: Mapped[Optional[str]] = mapped_column(String(100))
    transcription_status: Mapped[str] = mapped_column(String(20), default="not_applicable")
    transcription_error: Mapped[Optional[str]] = mapped_column(Text)
    transcribed_at: Mapped[Optional[str]] = mapped_column(String(40))
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)
    media_id: Mapped[Optional[str]] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(20), default="submitted")
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = created()
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow, onupdate=utcnow)


class InterviewEvaluation(Base):
    __tablename__ = "interview_evaluations"
    id: Mapped[str] = pk()
    answer_id: Mapped[str] = mapped_column(ForeignKey("interview_answers.id", ondelete="CASCADE"), nullable=False, unique=True)
    scores: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    overall: Mapped[float] = mapped_column(Float, default=0)
    strengths: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    missing_points: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    correction: Mapped[str] = mapped_column(Text, default="")
    model_answer: Mapped[str] = mapped_column(Text, default="")
    delivery_metrics: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    followup_suggestion: Mapped[Optional[str]] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    evaluator_version: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[str] = created()


class UnderstandingEvidence(Base):
    """Interview / code-defense signals. Never mixed into repository verdicts."""

    __tablename__ = "understanding_evidence"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[str] = mapped_column(ForeignKey("interview_sessions.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[str] = mapped_column(ForeignKey("interview_questions.id", ondelete="CASCADE"), nullable=False)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("interview_evaluations.id", ondelete="CASCADE"), nullable=False)
    skill_key: Mapped[Optional[str]] = mapped_column(String(80))
    evidence_type: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    rubric_score: Mapped[float] = mapped_column(Float, nullable=False)
    code_evidence_id: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[str] = created()


# ---------------------------------------------------------------- phase 3


class GapTask(Base):
    __tablename__ = "gap_tasks"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assessment_row_id: Mapped[Optional[str]] = mapped_column(ForeignKey("assessment_rows.id", ondelete="SET NULL"))
    job_posting_id: Mapped[str] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False)
    requirement_id: Mapped[Optional[str]] = mapped_column(String(32))
    skill_key: Mapped[str] = mapped_column(String(80), nullable=False)
    repository_id: Mapped[Optional[str]] = mapped_column(ForeignKey("repositories.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str] = mapped_column(Text, default="")
    suggested_files: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    acceptance_criteria: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    risks: Mapped[Any] = mapped_column(JSONText, nullable=True, default=list)
    effort_estimate: Mapped[Optional[str]] = mapped_column(String(40))
    generator: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="proposed")
    confirmed_at: Mapped[Optional[str]] = mapped_column(String(40))
    github_issue_number: Mapped[Optional[int]] = mapped_column(Integer)
    github_issue_url: Mapped[Optional[str]] = mapped_column(Text)
    last_verification: Mapped[Any] = mapped_column(JSONText, nullable=True)
    verified_commit_sha: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[str] = created()
    updated_at: Mapped[str] = mapped_column(String(40), default=utcnow, onupdate=utcnow)


class SandboxRun(Base):
    __tablename__ = "sandbox_runs"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    image: Mapped[str] = mapped_column(String(200), nullable=False)
    consent_at: Mapped[Optional[str]] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    exit_code: Mapped[Optional[int]] = mapped_column(Integer)
    tests_run: Mapped[Optional[int]] = mapped_column(Integer)
    passed: Mapped[Optional[int]] = mapped_column(Integer)
    failed: Mapped[Optional[int]] = mapped_column(Integer)
    skipped: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    output_excerpt: Mapped[Optional[str]] = mapped_column(Text)
    runtime_version: Mapped[Optional[str]] = mapped_column(String(200))
    constraints: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = created()
    finished_at: Mapped[Optional[str]] = mapped_column(String(40))


class SkillPassport(Base):
    __tablename__ = "skill_passports"
    id: Mapped[str] = pk()
    artifact_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), default="private")
    payload: Mapped[Any] = mapped_column(JSONText, nullable=False)
    canonical_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    key_id: Mapped[str] = mapped_column(String(40), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    revoked_at: Mapped[Optional[str]] = mapped_column(String(40))
    revoke_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = created()


class PassportEvidence(Base):
    __tablename__ = "passport_evidence"
    passport_id: Mapped[str] = mapped_column(ForeignKey("skill_passports.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence_records.id", ondelete="CASCADE"), primary_key=True)


class MarketObservation(Base):
    __tablename__ = "market_observations"
    __table_args__ = (UniqueConstraint("posting_id", "skill_key", name="uq_market_posting_skill"),)
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    search_run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("job_search_runs.id", ondelete="CASCADE"))
    posting_id: Mapped[str] = mapped_column(ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False)
    skill_key: Mapped[str] = mapped_column(String(80), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    observed_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class EvidenceFreshnessEvent(Base):
    __tablename__ = "evidence_freshness_events"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # stale | contradiction | permission | job_changed
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_ref: Mapped[Optional[str]] = mapped_column(String(200))
    detail: Mapped[Any] = mapped_column(JSONText, nullable=True, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[str] = created()
