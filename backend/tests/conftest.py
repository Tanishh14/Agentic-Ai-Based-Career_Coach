"""Test configuration. Test doubles live ONLY in this tests package and are never importable
from the application; every test gets a fresh on-disk SQLite database migrated from empty."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="cc-tests-"))
os.environ.setdefault("DATA_DIR", str(_TMP / "data"))
os.environ["BLOB_CACHE_DIR"] = str(_TMP / "blobs")
os.environ["MEDIA_STORAGE_DIR"] = str(_TMP / "media")
os.environ["KEYS_DIR"] = str(_TMP / "keys")
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-webhook-secret"
for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_FALLBACK_BASE_URL", "LLM_FALLBACK_MODEL", "GITHUB_TOKEN"):
    os.environ[k] = ""


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    from backend.app import config, database

    from backend.app.services.migrations import ensure_migrated

    url = "sqlite:///" + str(tmp_path / "test.db").replace(os.sep, "/")
    os.environ["DATABASE_URL"] = url
    os.environ["BLOB_CACHE_DIR"] = str(tmp_path / "blobs")
    config.get_settings.cache_clear()
    database.configure_engine(url)
    ensure_migrated(url)

    yield url
    database.get_engine().dispose()


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


class FakeRepo:
    """Scripted repository history (test double for the GitHub REST client)."""

    def __init__(self, full_name: str = "student/payments-api"):
        self.full_name = full_name
        self.commits: list[tuple[str, dict[str, bytes], str]] = []
        self.head_override: str | None = None

    def commit(self, files: dict[str, str | None], message: str) -> str:
        prev = dict(self.commits[-1][1]) if self.commits else {}
        for p, c in files.items():
            if c is None:
                prev.pop(p, None)
            else:
                prev[p] = c.encode()
        sha = hashlib.sha1(f"{len(self.commits)}{message}".encode()).hexdigest()
        self.commits.append((sha, prev, message))
        return sha

    def state(self, sha: str) -> dict[str, bytes]:
        return next(files for s, files, _ in self.commits if s == sha)

    @property
    def head(self) -> str:
        return self.head_override or self.commits[-1][0]


class FakeGitHub:
    def __init__(self, repo: FakeRepo):
        self.repo = repo
        self.token = None
        self.calls: list[str] = []

    async def branch_head(self, full_name, branch):
        self.calls.append("branch_head")
        return self.repo.head, False

    async def get_commit(self, full_name, sha):
        msg = next(m for s, _, m in self.repo.commits if s == sha)
        idx = [s for s, _, _ in self.repo.commits].index(sha)
        parents = [{"sha": self.repo.commits[idx - 1][0]}] if idx else []
        return {"sha": sha, "commit": {"message": msg, "author": {"date": "2026-10-16T00:00:00Z"}}, "parents": parents,
                "html_url": f"https://github.com/{full_name}/commit/{sha}"}

    async def get_tree(self, full_name, sha):
        self.calls.append("get_tree")
        return [{"path": p, "type": "blob", "sha": blob_sha(c), "size": len(c)} for p, c in self.repo.state(sha).items()], False

    async def compare(self, full_name, base, head):
        self.calls.append("compare")
        a, b = self.repo.state(base), self.repo.state(head)
        files = []
        for p in sorted(set(a) | set(b)):
            if p not in b:
                files.append({"filename": p, "status": "removed", "sha": None})
            elif p not in a:
                files.append({"filename": p, "status": "added", "sha": blob_sha(b[p])})
            elif a[p] != b[p]:
                files.append({"filename": p, "status": "modified", "sha": blob_sha(b[p])})
        return {"status": "ahead", "files": files, "commits": []}

    async def get_blob(self, full_name, sha, path=None, ref=None):
        for _, files, _ in self.repo.commits:
            for c in files.values():
                if blob_sha(c) == sha:
                    return c
        raise KeyError(sha)

    async def close(self):
        return None


@pytest.fixture
def fake_repo(monkeypatch):
    from backend.app.services import repo_scanner

    repo = FakeRepo()
    monkeypatch.setattr(repo_scanner, "GitHubClient", lambda token: FakeGitHub(repo))
    return repo
