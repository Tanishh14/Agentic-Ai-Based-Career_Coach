"""Accounts (name + password), per-student isolation, and the per-job leaderboard."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.tests.test_judge_twists import API_ONLY, DB_CODE, MAIN_USING_DB, TESTS

JD = ("Backend Intern\n\nRequirements:\n- Experience building REST APIs with Python\n- Writing SQL queries against a relational database\n"
      "- Unit tests for your code\n- Deploying services with Docker\n")


@pytest.fixture
def client():
    from backend.app.main import app

    with TestClient(app) as c:
        yield c


def _register(client, name, pw="secret123"):
    r = client.post("/api/auth/register", json={"name": name, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_register_login_and_protection(client):
    assert client.get("/api/jobs").status_code == 401
    h = _register(client, "Asha Rao")
    assert client.get("/api/auth/me", headers=h).json()["name"] == "Asha Rao"
    assert client.post("/api/auth/register", json={"name": "asha rao", "password": "another1"}).status_code == 409
    assert client.post("/api/auth/login", json={"name": "Asha Rao", "password": "wrong-pass"}).status_code == 401
    ok = client.post("/api/auth/login", json={"name": "ASHA RAO", "password": "secret123"})
    assert ok.status_code == 200
    assert client.post("/api/auth/register", json={"name": "x", "password": "123"}).status_code == 422
    client.post("/api/auth/logout", headers=h)
    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_password_is_hashed_and_token_hashed(client):
    from backend.app.database import session_scope
    from backend.app.models import User, UserSession

    _register(client, "Ravi K")
    with session_scope() as db:
        u = db.query(User).filter(User.username == "ravi k").one()
        assert u.password_hash.startswith("scrypt$") and "secret123" not in u.password_hash
        assert all(len(s.token_hash) == 64 for s in db.query(UserSession).all())


def test_students_are_isolated_and_leaderboard_ranks_them(client, fake_repo):
    import asyncio

    from backend.app.database import session_scope
    from backend.app.models import AnalysisScope, Repository
    from backend.app.services.reassessment import handle_repo_change

    a = _register(client, "Asha Rao")
    b = _register(client, "Ravi K")
    ja = client.post("/api/jobs/ingest/text", json={"text": JD, "source_url": "https://example.org/jobs/42", "title": "Backend Intern"}, headers=a).json()["id"]
    assert client.get("/api/jobs", headers=b).json() == []  # isolation
    assert client.get(f"/api/jobs/{ja}", headers=b).status_code == 404
    shared = client.get("/api/leaderboard/jobs", headers=b).json()
    assert shared and shared[0]["students"] == 1
    jb = client.post(f"/api/leaderboard/join/{shared[0]['sample_posting_id']}", headers=b).json()["job_posting_id"]
    assert jb != ja

    def scope_for(token_headers, repo_name, gid):
        uid = client.get("/api/auth/me", headers=token_headers).json()["id"]
        with session_scope() as db:
            repo = Repository(user_id=uid, github_id=gid, owner="s", name=repo_name, full_name=f"s/{repo_name}", default_branch="main", html_url="x")
            db.add(repo)
            db.flush()
            sc = AnalysisScope(user_id=uid, repository_id=repo.id, version=1, branch="main", include_patterns=[], exclude_patterns=[], active=True)
            db.add(sc)
            db.flush()
            return sc.id

    fake_repo.commit({"main.py": MAIN_USING_DB, "db.py": DB_CODE, "tests/test_main.py": TESTS}, "strong")
    asyncio.run(handle_repo_change(scope_for(a, "strong", "1"), "initial"))
    fake_repo.commit({"main.py": API_ONLY, "db.py": None, "tests/test_main.py": None}, "weak")
    asyncio.run(handle_repo_change(scope_for(b, "weak", "2"), "initial"))

    board = client.get(f"/api/leaderboard/job/{jb}", headers=b).json()
    assert board["participants"] == 2
    first, second = board["rows"]
    assert first["name"] == "Asha Rao" and first["rank"] == 1 and second["is_you"]
    assert first["combined"] > second["combined"] and first["evidence_coverage"] > second["evidence_coverage"]
    assert "0.60" in board["formula"]
    # opting out hides you from others but not from yourself
    client.patch("/api/settings", json={"show_on_leaderboard": False}, headers=a)
    assert client.get(f"/api/leaderboard/job/{jb}", headers=b).json()["participants"] == 1
    assert client.get(f"/api/leaderboard/job/{ja}", headers=a).json()["participants"] == 2
