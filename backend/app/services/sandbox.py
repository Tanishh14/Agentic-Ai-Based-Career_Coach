"""Opt-in ephemeral sandbox for running a repository's tests at a pinned commit.

Isolation: disposable Docker container, --network none (unless the user explicitly allows
network), read-only source mount copied into a tmpfs work dir, non-root user, all
capabilities dropped, no-new-privileges, memory/CPU/PID/time limits, no host credentials,
no Docker socket. If Docker is not available the feature is disabled and says why — untrusted
code is never executed in the backend process.
"""
from __future__ import annotations

import asyncio
import io
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..database import session_scope, utcnow
from ..errors import AppError
from ..integrations.github import GitHubClient
from ..models import AnalysisScope, Repository, SandboxRun, SnapshotFile
from . import job_queue
from .events import audit, emit
from .integrations_store import github_token, user_settings


def docker_status() -> dict[str, Any]:
    s = get_settings()
    if not s.sandbox_enabled:
        return {"available": False, "reason": "Disabled by SANDBOX_ENABLED=false."}
    exe = shutil.which("docker")
    if not exe:
        return {"available": False, "reason": "Docker is not installed on this machine, so no isolated sandbox can be created. "
                                              "Install Docker Desktop to enable proof-of-execution runs."}
    try:
        out = subprocess.run([exe, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"available": False, "reason": f"Docker daemon not reachable ({type(e).__name__})."}
    if out.returncode != 0:
        return {"available": False, "reason": "Docker daemon is not running."}
    return {"available": True, "server_version": out.stdout.strip()}


def propose_command(user_id: str, repository_id: str) -> dict[str, Any]:
    s = get_settings()
    with session_scope() as db:
        sc = db.execute(select(AnalysisScope).where(AnalysisScope.repository_id == repository_id, AnalysisScope.active.is_(True),
                                                    AnalysisScope.user_id == user_id)).scalar_one_or_none()
        if not sc or not sc.last_snapshot_id:
            raise AppError("not_analyzed", "Analyse the repository first.", status=409)
        files = {p for (p,) in db.execute(select(SnapshotFile.path).where(SnapshotFile.snapshot_id == sc.last_snapshot_id))}
        sha = sc.last_analyzed_sha
    if any(p.endswith(".py") for p in files) and any(re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", p) for p in files):
        req = "pip install -q -r requirements.txt 2>/dev/null; " if "requirements.txt" in files else ""
        return {"image": s.sandbox_image_python, "command": f"{req}pip install -q pytest >/dev/null 2>&1; python -m pytest -q -rs", "commit_sha": sha,
                "needs_network_for_install": True}
    if "package.json" in files:
        return {"image": s.sandbox_image_node, "command": "npm ci --ignore-scripts --no-audit --no-fund >/dev/null 2>&1; npm test --silent", "commit_sha": sha,
                "needs_network_for_install": True}
    if "go.mod" in files:
        return {"image": "golang:1.22", "command": "go test ./...", "commit_sha": sha, "needs_network_for_install": True}
    raise AppError("no_test_command", "No supported test setup detected in the analysed scope.", status=422)


def request_run(user_id: str, repository_id: str, command: str, image: str, commit_sha: str, consent: bool) -> str:
    if not consent:
        raise AppError("consent_required", "You must approve running this repository's code in the sandbox.", status=428)
    st = docker_status()
    with session_scope() as db:
        repo = db.get(Repository, repository_id)
        if repo is None or repo.user_id != user_id:
            raise AppError("not_found", "Repository not found.", status=404)
        allow_net = bool(user_settings(db, user_id).get("sandbox_allow_network"))
        r = SandboxRun(user_id=user_id, repository_id=repository_id, commit_sha=commit_sha, command=command[:2000], image=image, consent_at=utcnow(),
                       status="queued" if st["available"] else "disabled", error=None if st["available"] else st["reason"],
                       constraints={"network": "bridge (user allowed)" if allow_net else "none", "memory": "1g", "cpus": "1", "pids": 256,
                                    "read_only_source": True, "user": "65534:65534", "cap_drop": "ALL", "timeout_s": get_settings().sandbox_timeout_seconds})
        db.add(r)
        db.flush()
        rid = r.id
        audit("sandbox_consent", {"run_id": rid, "repo": repo.full_name, "commit": commit_sha, "command": command}, user_id, db=db)
    if st["available"]:
        job_queue.enqueue("sandbox_run", {"run_id": rid}, f"sandbox:{rid}", user_id=user_id, max_attempts=1)
    return rid


def _safe_extract(data: bytes, dest: Path) -> Path:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf.getmembers():
            p = (dest / m.name).resolve()
            if dest.resolve() not in p.parents and p != dest.resolve():
                raise AppError("unsafe_archive", "Archive contains unsafe paths.", status=422)
            if m.issym() or m.islnk() or m.isdev():
                continue
            tf.extract(m, dest)
    roots = [p for p in dest.iterdir() if p.is_dir()]
    return roots[0] if len(roots) == 1 else dest


def _parse_counts(out: str) -> dict[str, int | None]:
    res: dict[str, int | None] = {"passed": None, "failed": None, "skipped": None}
    m = re.findall(r"(\d+) (passed|failed|skipped|errors?)", out)
    for n, k in m:
        k = "failed" if k.startswith("error") else k
        res[k] = (res.get(k) or 0) + int(n)
    jm = re.search(r"Tests:\s+(?:(\d+) failed, )?(?:(\d+) skipped, )?(\d+) passed", out)
    if jm:
        res = {"failed": int(jm.group(1) or 0), "skipped": int(jm.group(2) or 0), "passed": int(jm.group(3))}
    return res


async def execute_run(run_id: str) -> dict[str, Any]:
    s = get_settings()
    with session_scope() as db:
        r = db.get(SandboxRun, run_id)
        repo = db.get(Repository, r.repository_id)
        token = github_token(db, r.user_id)
        user_id, full, sha, cmd, image, net = r.user_id, repo.full_name, r.commit_sha, r.command, r.image, r.constraints.get("network", "none")
        r.status = "running"
    emit("sandbox_status", {"run_id": run_id, "status": "running"}, user_id)
    gh = GitHubClient(token)
    try:
        data = await gh.download_tarball(full, sha)
    finally:
        await gh.close()
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="cc-sandbox-") as tmp:
        src = _safe_extract(data, Path(tmp))
        args = ["docker", "run", "--rm", "--network", "none" if net == "none" else "bridge", "--read-only",
                "--tmpfs", "/tmp:rw,exec,size=256m", "--tmpfs", "/work:rw,exec,size=768m", "--memory", "1g", "--cpus", "1", "--pids-limit", "256",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65534:65534", "-e", "HOME=/tmp",
                "-v", f"{src}:/src:ro", "-w", "/work", image, "sh", "-c", f"cp -r /src/. /work && {cmd}"]
        try:
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=s.sandbox_timeout_seconds)
            code = proc.returncode
            err = None
        except asyncio.TimeoutError:
            proc.kill()
            out_b, code, err = b"", None, f"timed out after {s.sandbox_timeout_seconds}s"
    out = out_b.decode("utf-8", "replace")
    counts = _parse_counts(out)
    with session_scope() as db:
        r = db.get(SandboxRun, run_id)
        r.status = "completed" if code is not None else "failed"
        r.exit_code = code
        r.passed, r.failed, r.skipped = counts["passed"], counts["failed"], counts["skipped"]
        r.tests_run = sum(v for v in counts.values() if v) or None
        r.duration_ms = int((time.monotonic() - t0) * 1000)
        r.output_excerpt = out[-6000:]
        r.runtime_version = image
        r.error = err
        r.finished_at = utcnow()
    emit("sandbox_status", {"run_id": run_id, "status": "completed" if code is not None else "failed", "exit_code": code}, user_id)
    return {"exit_code": code, **counts}
