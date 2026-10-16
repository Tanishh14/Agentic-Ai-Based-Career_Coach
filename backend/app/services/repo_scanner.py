"""Commit-pinned repository scanning (full and incremental).

Full scan:   tree at pinned SHA → filter by scope/ignore rules → fetch blobs → detectors
             → reference index → critic → immutable evidence records.
Incremental: compare(last_analyzed_sha...head) → re-run detectors only on added/modified/
             renamed files (unchanged files reuse cached findings keyed by blob SHA),
             re-verify reachability across the scope, supersede evidence whose file changed
             or was deleted, keep every other evidence ID untouched.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..agents.evidence_verifier import llm_corroborate, verify_finding
from ..agents.orchestrator import AgentRun
from ..analyzers.call_graph import ReferenceIndex
from ..analyzers.detectors import DETECTOR_FAMILIES, FileScan, scan_file
from ..analyzers.files import file_kind, ignore_reason, language_of, matches_scope
from ..analyzers.findings import DETECTOR_VERSION, Finding
from ..config import get_settings
from ..database import session_scope, utcnow
from ..errors import AppError
from ..integrations.github import GitHubClient, commit_url, permalink
from ..models import (AnalysisScope, CommitRecord, EvidenceFreshnessEvent, EvidenceRecord, Repository, ScopeSnapshot,
                      SnapshotFile)
from ..security import sha256_hex
from .events import emit
from .integrations_store import github_token, user_settings

PRIORITY_KINDS = {"implementation": 0, "test": 1, "config": 2, "manifest": 2}


@dataclass
class FileEntry:
    path: str
    blob_sha: str
    size: int
    language: str | None = None
    kind: str = "implementation"


@dataclass
class ScanResult:
    scope_id: str
    repository_id: str
    repo_full_name: str
    snapshot_id: str | None
    commit_sha: str | None
    previous_sha: str | None
    mode: str
    noop: bool = False
    changed_paths: list[dict[str, Any]] = field(default_factory=list)
    affected_skills: set[str] = field(default_factory=set)
    evidence_added: int = 0
    evidence_superseded: int = 0
    evidence_kept: int = 0
    commit_message: str | None = None
    commit_html_url: str | None = None


def _findings_cache_path(blob_sha: str, path: str) -> Path:
    key = hashlib.sha256(f"{blob_sha}|{path}|{DETECTOR_VERSION}".encode()).hexdigest()
    return Path(get_settings().blob_cache_dir) / "findings" / key[:2] / f"{key}.json"


def _load_cached_scan(blob_sha: str, path: str) -> FileScan | None:
    p = _findings_cache_path(blob_sha, path)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fs = FileScan(path=path, language=d["language"], kind=d["kind"], parse_error=d.get("parse_error"))
    fs.findings = [Finding(**f) for f in d["findings"]]
    fs.defined_symbols = set(d["defined"])
    fs.referenced_names = d["refs"]
    return fs


def _store_cached_scan(blob_sha: str, fs: FileScan) -> None:
    p = _findings_cache_path(blob_sha, fs.path)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {"language": fs.language, "kind": fs.kind, "parse_error": fs.parse_error, "defined": sorted(fs.defined_symbols),
         "refs": fs.referenced_names, "findings": [f.__dict__ for f in fs.findings]}
    p.write_text(json.dumps(d), encoding="utf-8")


def _select(entries: list[dict], include: list[str], exclude: list[str]) -> tuple[list[FileEntry], list[dict]]:
    s = get_settings()
    selected: list[FileEntry] = []
    ignored: list[dict] = []
    for e in entries:
        if e.get("type") != "blob":
            continue
        path = e["path"]
        size = int(e.get("size") or 0)
        if not matches_scope(path, include, exclude):
            ignored.append({"path": path, "reason": "excluded by scope include/exclude patterns"})
            continue
        reason = ignore_reason(path, size, s.max_file_bytes)
        if reason:
            ignored.append({"path": path, "reason": reason})
            continue
        selected.append(FileEntry(path, e["sha"], size, language_of(path), file_kind(path)))
    if len(selected) > s.max_files_per_scan:
        selected.sort(key=lambda f: (PRIORITY_KINDS.get(f.kind, 3), f.path.count("/"), f.path))
        for f in selected[s.max_files_per_scan:]:
            ignored.append({"path": f.path, "reason": f"file cap reached (MAX_FILES_PER_SCAN={s.max_files_per_scan})"})
        selected = selected[: s.max_files_per_scan]
    return selected, ignored


async def _fetch_texts(gh: GitHubClient, full_name: str, files: list[FileEntry], ref: str) -> tuple[dict[str, str], list[dict]]:
    texts: dict[str, str] = {}
    ignored: list[dict] = []

    async def one(f: FileEntry) -> None:
        try:
            raw = await gh.get_blob(full_name, f.blob_sha, path=f.path, ref=ref)
        except AppError as e:
            ignored.append({"path": f.path, "reason": f"could not read blob: {e.message}"})
            return
        if b"\x00" in raw[:8000]:
            ignored.append({"path": f.path, "reason": "binary content"})
            return
        texts[f.path] = raw.decode("utf-8", errors="replace")

    await asyncio.gather(*(one(f) for f in files))
    return texts, ignored


def _evidence_key(skill: str, detector: str, path: str, start: int, end: int, blob: str) -> str:
    return f"{skill}|{detector}|{path}|{start}|{end}|{blob}"


async def scan_scope(scope_id: str, trigger: str, run: AgentRun, *, trigger_ref: str | None = None, force_full: bool = False) -> ScanResult:
    with session_scope() as db:
        scope = db.get(AnalysisScope, scope_id)
        if scope is None:
            raise AppError("scope_not_found", "Analysis scope not found.", status=404)
        repo = db.get(Repository, scope.repository_id)
        user_id = scope.user_id
        token = github_token(db, user_id)
        settings_ = user_settings(db, user_id)
        include, exclude = list(scope.include_patterns or []), list(scope.exclude_patterns or [])
        branch, last_sha, last_snapshot_id = scope.branch, scope.last_analyzed_sha, scope.last_snapshot_id
        full_name, repo_id = repo.full_name, repo.id
        scope.scan_status = "scanning"
        scope.scan_error = None
    emit("scan_progress", {"scope_id": scope_id, "repo": full_name, "stage": "started", "trigger": trigger}, user_id)

    gh = GitHubClient(token)
    try:
        head, _ = await run.tool("get_repository_tree", f"resolve head of {full_name}@{branch}", gh.branch_head, full_name, branch,
                                 summarize=lambda r: f"head {r[0][:12]}")
        result = ScanResult(scope_id, repo_id, full_name, None, head, last_sha, "full")
        if head == last_sha and not force_full:
            result.noop = True
            result.snapshot_id = last_snapshot_id
            run.record("observe", result_summary=f"{full_name}@{branch} already analysed at {head[:12]}; nothing to do")
            with session_scope() as db:
                sc = db.get(AnalysisScope, scope_id)
                sc.scan_status = "ready"
            return result
        with session_scope() as db:
            existing = db.execute(select(ScopeSnapshot).where(ScopeSnapshot.scope_id == scope_id, ScopeSnapshot.commit_sha == head)).scalar_one_or_none()
            if existing is not None and not force_full:
                sc = db.get(AnalysisScope, scope_id)
                sc.last_analyzed_sha, sc.last_snapshot_id, sc.scan_status = head, existing.id, "ready"
                result.noop, result.snapshot_id = True, existing.id
                return result

        commit = await run.tool("get_commit_diff", f"commit metadata {head[:12]}", gh.get_commit, full_name, head,
                                summarize=lambda c: (c.get("commit", {}).get("message", "") or "").split("\n")[0][:120])
        result.commit_message = (commit.get("commit", {}).get("message") or "")[:2000]
        result.commit_html_url = commit.get("html_url") or commit_url(full_name, head)

        prev_files: dict[str, SnapshotFile] = {}
        if last_snapshot_id and not force_full:
            with session_scope() as db:
                for sf in db.execute(select(SnapshotFile).where(SnapshotFile.snapshot_id == last_snapshot_id)).scalars():
                    db.expunge(sf)
                    prev_files[sf.path] = sf

        ignored: list[dict] = []
        changed: list[dict[str, Any]] = []
        commits_meta: list[dict] = []
        tree_count = 0
        incremental = bool(prev_files) and bool(last_sha)
        if incremental:
            try:
                cmp = await run.tool("get_commit_diff", f"compare {last_sha[:12]}...{head[:12]}", gh.compare, full_name, last_sha, head,
                                     summarize=lambda c: f"{c.get('status')}: {len(c.get('files') or [])} files, {c.get('total_commits')} commits")
            except AppError as e:
                run.record("plan", result_summary=f"compare unavailable ({e.code}); falling back to a full scan of {head[:12]}")
                incremental = False
            else:
                if cmp.get("status") in ("diverged", "behind") or len(cmp.get("files") or []) >= 300:
                    run.record("plan", result_summary=f"history {cmp.get('status')} / large diff; performing full scan for consistency")
                    incremental = False
                else:
                    commits_meta = cmp.get("commits") or []
                    for f in cmp.get("files") or []:
                        changed.append({"path": f["filename"], "status": f["status"], "previous_path": f.get("previous_filename"),
                                        "blob_sha": f.get("sha"), "additions": f.get("additions"), "deletions": f.get("deletions")})
        result.mode = "incremental" if incremental else "full"

        # ---------------------------------------------------------- build new file set
        if incremental:
            files: dict[str, FileEntry] = {p: FileEntry(p, sf.blob_sha, sf.size, sf.language, sf.file_kind) for p, sf in prev_files.items()}
            s = get_settings()
            for c in changed:
                if c["status"] == "removed":
                    files.pop(c["path"], None)
                    continue
                if c["status"] == "renamed" and c.get("previous_path"):
                    files.pop(c["previous_path"], None)
                p = c["path"]
                reason = None if matches_scope(p, include, exclude) else "excluded by scope include/exclude patterns"
                reason = reason or ignore_reason(p, 0, s.max_file_bytes)
                if reason or not c.get("blob_sha"):
                    files.pop(p, None)
                    ignored.append({"path": p, "reason": reason or "no blob"})
                    continue
                files[p] = FileEntry(p, c["blob_sha"], 0, language_of(p), file_kind(p))
            selected = list(files.values())
            changed_paths_set = {c["path"] for c in changed if c["status"] != "removed"}
            run.record("plan", input_summary=f"{len(changed)} changed path(s)",
                       result_summary=f"incremental: re-run detectors on {len([p for p in changed_paths_set if p in files])} changed in-scope file(s); "
                                      f"{len(selected) - len([p for p in changed_paths_set if p in files])} unchanged file(s) reuse cached findings")
        else:
            entries, truncated = await run.tool("get_repository_tree", f"tree {full_name}@{head[:12]}", gh.get_tree, full_name, head,
                                                summarize=lambda r: f"{len(r[0])} entries{' (truncated)' if r[1] else ''}")
            tree_count = sum(1 for e in entries if e.get("type") == "blob")
            if truncated:
                ignored.append({"path": "*", "reason": "GitHub truncated the recursive tree (very large repository); narrow the scope with include patterns"})
            selected, ign = _select(entries, include, exclude)
            ignored += ign
            changed_paths_set = {f.path for f in selected}
            run.record("plan", result_summary=f"full scan: {len(selected)} of {tree_count} files in scope after filters; {len(ign)} ignored with reasons")

        # ---------------------------------------------------------- fetch + detect
        need_detection = [f for f in selected if f.path in changed_paths_set or _load_cached_scan(f.blob_sha, f.path) is None]
        texts, fetch_ignored = await run.tool("read_file_at_commit", f"read {len(selected)} blob(s) at {head[:12]} (cache-first)",
                                              _fetch_texts, gh, full_name, selected, head,
                                              summarize=lambda r: f"{len(r[0])} files read, {len(r[1])} unreadable")
        ignored += fetch_ignored
        scans: dict[str, FileScan] = {}
        rerun = 0
        need = {f.path for f in need_detection}
        for f in selected:
            if f.path not in texts:
                continue
            cached = None if f.path in need else _load_cached_scan(f.blob_sha, f.path)
            if cached is None:
                fs = scan_file(f.path, texts[f.path])
                _store_cached_scan(f.blob_sha, fs)
                rerun += 1
            else:
                fs = cached
            scans[f.path] = fs
            if fs.parse_error:
                ignored.append({"path": f.path, "reason": f"parse error: {fs.parse_error}"})
        run.record("tool_call", tool="run_skill_detector", input_summary=f"{rerun} file(s) analysed by detectors",
                   result_summary=f"{sum(len(s.findings) for s in scans.values())} candidate finding(s) across {len(scans)} file(s); detector v{DETECTOR_VERSION}")

        index = ReferenceIndex()
        for fs in scans.values():
            index.add_file(fs.path, fs.referenced_names, fs.defined_symbols)

        blob_of = {f.path: f.blob_sha for f in selected}
        candidates: list[dict[str, Any]] = []
        for path, fs in scans.items():
            text = texts[path]
            for fnd in fs.findings:
                v = verify_finding(fnd, path, text, fs.kind, index)
                candidates.append({"f": fnd, "v": v, "path": path, "blob": blob_of[path], "kind": fs.kind})
        acc = sum(1 for c in candidates if c["v"].status == "accepted")
        rej = sum(1 for c in candidates if c["v"].status == "rejected")
        amb = sum(1 for c in candidates if c["v"].status == "ambiguous")
        run.record("tool_call", tool="verify_evidence", input_summary=f"critic over {len(candidates)} candidate(s)",
                   result_summary=f"{acc} accepted, {amb} ambiguous, {rej} rejected (trivial tests, comment-only, unreferenced, generated, etc.)")

        # Optional bounded LLM corroboration (can only lower confidence)
        if settings_.get("allow_code_snippets_to_llm"):
            from ..integrations.llm import llm_available

            if llm_available(user_id):
                batch = [c for c in candidates if c["v"].status == "accepted" and c["path"] in changed_paths_set
                         and c["f"].signal_level in ("meaningful_implementation", "tests_found")][:8]
                if batch:
                    try:
                        run.charge_llm()
                        verdicts = await run.tool("verify_evidence", f"LLM corroboration of {len(batch)} snippet(s)", llm_corroborate, user_id,
                                                  [{"id": str(i), "skill_key": c["f"].skill_key, "path": c["path"], "start_line": c["f"].start_line,
                                                    "end_line": c["f"].end_line, "snippet": c["v"].snippet, "reason": c["f"].reason} for i, c in enumerate(batch)],
                                                  summarize=lambda r: f"{sum(1 for x in r.values() if not x.supports_claim)} flagged")
                        for i, c in enumerate(batch):
                            it = verdicts.get(str(i))
                            if it is None:
                                continue
                            c["llm"] = {"supports_claim": it.supports_claim, "concerns": it.concerns}
                            if not it.supports_claim:
                                c["v"].status = "ambiguous"
                                c["v"].confidence = round(max(0.1, c["v"].confidence - 0.25), 2)
                                c["v"].notes.append("LLM reviewer raised concerns: " + "; ".join(it.concerns[:3]))
                            elif it.concerns:
                                c["v"].confidence = round(max(0.1, c["v"].confidence - 0.05), 2)
                    except Exception as e:  # noqa: BLE001 - deterministic evidence is retained
                        run.record("verify", result_summary=f"LLM corroboration skipped: {type(e).__name__}: {str(e)[:200]}", status="degraded")

        # ---------------------------------------------------------- persist snapshot + evidence
        languages: dict[str, int] = {}
        for f in selected:
            if f.language:
                languages[f.language] = languages.get(f.language, 0) + 1
        with session_scope() as db:
            snap = ScopeSnapshot(
                scope_id=scope_id, commit_sha=head, base_snapshot_id=last_snapshot_id if incremental else None,
                commit_message=result.commit_message, commit_date=(commit.get("commit", {}).get("author") or {}).get("date"),
                tree_file_count=tree_count or len(selected) + len([i for i in ignored if i["path"] != "*"]),
                scanned_file_count=len(scans), languages=languages,
                detectors=[f"v{DETECTOR_VERSION}"] + DETECTOR_FAMILIES, ignored=ignored[:400], ignored_count=len(ignored),
                trigger=trigger, mode=result.mode, changed_paths=changed[:500],
            )
            db.add(snap)
            db.flush()
            result.snapshot_id = snap.id
            for f in selected:
                if f.path in scans:
                    db.add(SnapshotFile(snapshot_id=snap.id, path=f.path, blob_sha=f.blob_sha, language=f.language,
                                        size=f.size or len(texts.get(f.path, "")), file_kind=scans[f.path].kind))

            current = db.execute(select(EvidenceRecord).where(EvidenceRecord.scope_id == scope_id, EvidenceRecord.status == "current")).scalars().all()
            existing_by_key: dict[str, EvidenceRecord] = {
                _evidence_key(e.skill_key, e.detector, e.path, e.start_line, e.end_line, e.blob_sha): e for e in current}
            new_keys: set[str] = set()
            for c in candidates:
                fnd: Finding = c["f"]
                v = c["v"]
                key = _evidence_key(fnd.skill_key, fnd.detector, c["path"], fnd.start_line, fnd.end_line, c["blob"])
                if key in new_keys:
                    continue
                new_keys.add(key)
                old = existing_by_key.get(key)
                if old is not None and old.verification_status == v.status and old.depth == v.depth and old.reachability == v.reachability \
                        and abs(old.confidence - v.confidence) < 0.001:
                    result.evidence_kept += 1
                    continue
                if old is not None:
                    old.status = "historical"
                    old.superseded_at_sha = head
                    old.superseded_reason = f"re-verified at {head[:12]}: {old.verification_status}/{old.reachability} → {v.status}/{v.reachability}"
                    result.evidence_superseded += 1
                    result.affected_skills.add(old.skill_key)
                ev = EvidenceRecord(
                    user_id=user_id, scope_id=scope_id, snapshot_id=snap.id, repository_id=repo_id, skill_key=fnd.skill_key,
                    source_type=fnd.source_type, repo_full_name=full_name, branch=branch, commit_sha=head, path=c["path"], blob_sha=c["blob"],
                    start_line=fnd.start_line, end_line=fnd.end_line, snippet=v.snippet[:6000], snippet_digest=sha256_hex(v.snippet),
                    detector=fnd.detector, detector_version=DETECTOR_VERSION, node_type=fnd.node_type, signal_level=fnd.signal_level,
                    depth=v.depth, file_kind=c["kind"], reachability=v.reachability, verification_status=v.status,
                    verification_notes=v.notes, llm_corroboration=c.get("llm"), confidence=v.confidence, reason=fnd.reason,
                    permalink=permalink(full_name, head, c["path"], fnd.start_line, fnd.end_line),
                )
                db.add(ev)
                result.evidence_added += 1
                result.affected_skills.add(fnd.skill_key)
            removed_paths = {c["path"]: c for c in changed if c["status"] == "removed"}
            renamed_old = {c["previous_path"]: c for c in changed if c["status"] == "renamed" and c.get("previous_path")}
            stale_ids: list[str] = []
            for key, old in existing_by_key.items():
                if key in new_keys or old.status != "current":
                    continue
                old.status = "historical"
                old.superseded_at_sha = head
                if old.path in removed_paths:
                    old.superseded_reason = f"file deleted in {head[:12]}"
                elif old.path in renamed_old:
                    old.superseded_reason = f"file renamed to {renamed_old[old.path]['path']} in {head[:12]}"
                elif old.path in changed_paths_set:
                    old.superseded_reason = f"file modified in {head[:12]}; evidence no longer present at these lines"
                else:
                    old.superseded_reason = f"no longer detected at {head[:12]} (scope or detector change)"
                result.evidence_superseded += 1
                result.affected_skills.add(old.skill_key)
                stale_ids.append(old.id)
            for sid in stale_ids[:50]:
                db.add(EvidenceFreshnessEvent(user_id=user_id, kind="stale", source_type="evidence", source_id=sid,
                                              reason="Evidence superseded by a newer commit", trigger_ref=head, status="resolved",
                                              detail={"commit": head}))
            for cm in commits_meta[-50:]:
                sha = cm.get("sha")
                if sha and not db.execute(select(CommitRecord.id).where(CommitRecord.repository_id == repo_id, CommitRecord.sha == sha)).first():
                    db.add(CommitRecord(repository_id=repo_id, sha=sha, parents=[p.get("sha") for p in cm.get("parents") or []],
                                        message=(cm.get("commit", {}).get("message") or "")[:2000],
                                        author_date=(cm.get("commit", {}).get("author") or {}).get("date"), html_url=cm.get("html_url")))
            if not db.execute(select(CommitRecord.id).where(CommitRecord.repository_id == repo_id, CommitRecord.sha == head)).first():
                db.add(CommitRecord(repository_id=repo_id, sha=head, parents=[p.get("sha") for p in commit.get("parents") or []],
                                    message=result.commit_message, author_date=(commit.get("commit", {}).get("author") or {}).get("date"),
                                    html_url=result.commit_html_url, changed_paths=[c["path"] for c in changed][:300]))
            sc = db.get(AnalysisScope, scope_id)
            sc.last_analyzed_sha = head
            sc.last_snapshot_id = snap.id
            sc.last_seen_head_sha = head
            sc.scan_status = "ready"
            sc.scan_error = None
        result.changed_paths = changed
        run.record("tool_call", tool="record_evidence", input_summary=f"snapshot {head[:12]} ({result.mode})",
                   result_summary=f"{result.evidence_added} new evidence record(s), {result.evidence_superseded} superseded, {result.evidence_kept} unchanged IDs kept; "
                                  f"affected skills: {', '.join(sorted(result.affected_skills)) or 'none'}",
                   entity_ids=[result.snapshot_id or ""])
        emit("scan_progress", {"scope_id": scope_id, "repo": full_name, "stage": "completed", "commit_sha": head, "mode": result.mode,
                               "affected_skills": sorted(result.affected_skills)}, user_id)
        return result
    except Exception as e:
        with session_scope() as db:
            sc = db.get(AnalysisScope, scope_id)
            if sc:
                sc.scan_status = "failed"
                sc.scan_error = getattr(e, "message", None) or f"{type(e).__name__}: {e}"
                if isinstance(e, AppError) and e.code in ("github_unauthorized", "github_forbidden", "github_not_found"):
                    db.add(EvidenceFreshnessEvent(user_id=user_id, kind="permission", source_type="scope", source_id=scope_id,
                                                  reason=e.message, trigger_ref=trigger_ref, detail={"code": e.code}))
        emit("scan_progress", {"scope_id": scope_id, "repo": full_name, "stage": "failed", "error": getattr(e, "message", str(e))[:300]}, user_id)
        raise
    finally:
        await gh.close()


def search_scope_code(scope_id: str, pattern: str, limit: int = 30) -> list[dict[str, Any]]:
    """search_repository_code tool: regex search over the cached blobs of the latest snapshot."""
    import re

    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise AppError("bad_pattern", f"Invalid regex: {e}", status=422)
    out: list[dict[str, Any]] = []
    with session_scope() as db:
        sc = db.get(AnalysisScope, scope_id)
        if not sc or not sc.last_snapshot_id:
            return out
        files = db.execute(select(SnapshotFile).where(SnapshotFile.snapshot_id == sc.last_snapshot_id)).scalars().all()
        files = [(f.path, f.blob_sha) for f in files]
    base = Path(get_settings().blob_cache_dir)
    for path, blob in files:
        p = base / blob[:2] / blob
        if not p.exists():
            continue
        for i, line in enumerate(p.read_bytes().decode("utf-8", "replace").split("\n"), 1):
            if rx.search(line):
                out.append({"path": path, "line": i, "text": line.strip()[:200]})
                if len(out) >= limit:
                    return out
    return out


def read_cached_file(scope_id: str, path: str) -> str | None:
    with session_scope() as db:
        sc = db.get(AnalysisScope, scope_id)
        if not sc or not sc.last_snapshot_id:
            return None
        sf = db.execute(select(SnapshotFile).where(SnapshotFile.snapshot_id == sc.last_snapshot_id, SnapshotFile.path == path)).scalar_one_or_none()
        if not sf:
            return None
        blob = sf.blob_sha
    p = Path(get_settings().blob_cache_dir) / blob[:2] / blob
    return p.read_bytes().decode("utf-8", "replace") if p.exists() else None


def mark_scope_event(scope_id: str, kind: str) -> None:
    with session_scope() as db:
        sc = db.get(AnalysisScope, scope_id)
        if sc:
            sc.last_event_at = utcnow()
            sc.last_event_kind = kind
