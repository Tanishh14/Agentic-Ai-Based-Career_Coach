"""Phase 1 judge-twist scenarios (blueprint §3.5) driven through the real scanner + assessment engine.
The GitHub API is replaced by a scripted test double (tests only)."""
from __future__ import annotations

from backend.tests.helpers import current_evidence, latest, latest_diff, make_job, make_scope, scan

API_ONLY = '''
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()
PAYMENTS = {}

class Payment(BaseModel):
    amount: int
    currency: str

@app.post("/payments")
def create_payment(p: Payment):
    if p.amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be positive")
    pid = len(PAYMENTS) + 1
    PAYMENTS[pid] = p
    return {"id": pid, "status": "created"}

@app.get("/payments/{pid}")
def get_payment(pid: int):
    if pid not in PAYMENTS:
        raise HTTPException(status_code=404, detail="not found")
    return PAYMENTS[pid]
'''

DB_CODE = '''
import sqlite3

def connect(path="pay.db"):
    return sqlite3.connect(path)

def save_payment(conn, amount, currency):
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO payments (amount, currency) VALUES (?, ?)", (amount, currency))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return cur.lastrowid

def list_payments(conn):
    return conn.execute("SELECT id, amount, currency FROM payments ORDER BY id").fetchall()
'''

MAIN_USING_DB = API_ONLY + '''
from db import connect, save_payment, list_payments

@app.get("/ledger")
def ledger():
    conn = connect()
    rows = list_payments(conn)
    return [{"id": r[0], "amount": r[1]} for r in rows]

@app.post("/ledger")
def add(p: Payment):
    conn = connect()
    return {"id": save_payment(conn, p.amount, p.currency)}
'''

TESTS = '''
from main import create_payment, Payment
import pytest

def test_create_payment_returns_id():
    r = create_payment(Payment(amount=10, currency="INR"))
    assert r["status"] == "created"
    assert r["id"] >= 1

def test_rejects_non_positive_amount():
    with pytest.raises(Exception):
        create_payment(Payment(amount=0, currency="INR"))
'''


def test_initial_api_demonstrated_sql_and_tests_not(fake_repo):
    fake_repo.commit({"main.py": API_ONLY, "README.md": "We use SQL: SELECT * FROM payments"}, "initial API")
    job = make_job()
    sc = make_scope()
    scan(sc, "initial")
    a, rows = latest(job)
    assert rows["api_development"].verdict == "DEMONSTRATED"
    assert rows["sql"].verdict == "NOT_DEMONSTRATED_IN_SCOPE"  # README SQL never counts (twist 3)
    assert rows["automated_testing"].verdict == "NOT_DEMONSTRATED_IN_SCOPE"
    assert "searched" in rows["sql"].rationale and "student/payments-api" in rows["sql"].rationale
    assert rows["api_development"].search_trace["repositories"][0]["commit_sha"] == fake_repo.head


def test_commit_adding_sql_and_tests_flips_verdicts_and_keeps_unrelated_rows(fake_repo):
    fake_repo.commit({"main.py": API_ONLY}, "initial")
    job = make_job()
    sc = make_scope()
    scan(sc, "initial")
    a1, r1 = latest(job)
    fake_repo.commit({"main.py": MAIN_USING_DB, "db.py": DB_CODE, "tests/test_main.py": TESTS}, "add sql + tests")
    scan(sc, "webhook")
    a2, r2 = latest(job)
    assert a2.version == a1.version + 1
    assert r2["sql"].verdict == "DEMONSTRATED"
    assert r2["automated_testing"].verdict == "DEMONSTRATED"
    assert r2["containerization"].verdict == "NOT_DEMONSTRATED_IN_SCOPE"
    # unrelated row (containerization) carried unchanged with original timestamp
    assert r2["containerization"].carried_from_row_id == r1["containerization"].id
    assert r2["containerization"].evaluated_at == r1["containerization"].evaluated_at
    d = latest_diff()
    assert d.commit_sha == fake_repo.head
    moved = {x["skill_key"]: x for x in d.row_deltas}
    assert moved["sql"]["direction"] == "improved" and moved["automated_testing"]["direction"] == "improved"
    assert {"db.py", "tests/test_main.py"} <= {c["path"] for c in d.changed_paths}


def test_empty_test_file_and_assert_true_are_not_testing_evidence(fake_repo):
    fake_repo.commit({"main.py": API_ONLY, "tests/test_empty.py": "", "tests/test_x.py": "def test_x():\n    pass\n\ndef test_y():\n    assert True\n"}, "fake tests")
    job = make_job()
    sc = make_scope()
    scan(sc, "initial")
    _, rows = latest(job)
    assert rows["automated_testing"].verdict == "NOT_DEMONSTRATED_IN_SCOPE"
    ev = [e for e in current_evidence(sc) if e.skill_key == "automated_testing"]
    assert ev and all(e.verification_status == "rejected" for e in ev)
    assert rows["automated_testing"].search_trace["rejected"] >= 2


def test_sql_in_comments_and_docstrings_is_ignored_and_unexecuted_literal_is_partial(fake_repo):
    commented = '"""Repository.\n\nSELECT * FROM users WHERE id = 1\n"""\n# cursor.execute("SELECT * FROM users")\nQUERY = "SELECT id, name FROM users WHERE active = 1"\n\ndef noop():\n    return 1\n'
    fake_repo.commit({"main.py": API_ONLY, "repo.py": commented}, "sql text only")
    job = make_job()
    sc = make_scope()
    scan(sc, "initial")
    _, rows = latest(job)
    assert rows["sql"].verdict == "PARTIAL"  # literal with no execution path = ambiguous at most
    sql_ev = [e for e in current_evidence(sc) if e.skill_key == "sql"]
    assert all(e.verification_status == "ambiguous" for e in sql_ev)
    assert all(e.start_line >= 6 for e in sql_ev)  # never the docstring/comment lines


def test_unrelated_css_docs_commit_keeps_evidence_ids_and_verdict_timestamps(fake_repo):
    fake_repo.commit({"main.py": MAIN_USING_DB, "db.py": DB_CODE, "tests/test_main.py": TESTS, "static/site.css": "body{margin:0}"}, "base")
    job = make_job()
    sc = make_scope()
    scan(sc, "initial")
    ev1 = {e.id for e in current_evidence(sc)}
    _, r1 = latest(job)
    fake_repo.commit({"static/site.css": "body{margin:0;padding:0}", "docs/notes.md": "SELECT * FROM x"}, "styling + docs")
    scan(sc, "poll")
    ev2 = {e.id for e in current_evidence(sc)}
    a2, r2 = latest(job)
    assert ev1 == ev2
    for k in ("api_development", "sql", "automated_testing"):
        assert r2[k].evaluated_at == r1[k].evaluated_at and r2[k].verdict == r1[k].verdict
    assert latest_diff().row_deltas == []


def test_deleting_tests_regresses_and_marks_evidence_historical(fake_repo):
    fake_repo.commit({"main.py": MAIN_USING_DB, "db.py": DB_CODE, "tests/test_main.py": TESTS}, "with tests")
    job = make_job()
    sc = make_scope()
    scan(sc, "initial")
    _, r1 = latest(job)
    assert r1["automated_testing"].verdict == "DEMONSTRATED"
    fake_repo.commit({"tests/test_main.py": None}, "remove tests")
    scan(sc, "poll")
    _, r2 = latest(job)
    assert r2["automated_testing"].verdict == "NOT_DEMONSTRATED_IN_SCOPE"
    d = latest_diff()
    assert any(x["skill_key"] == "automated_testing" and x["direction"] == "regressed" for x in d.row_deltas)
    assert not [e for e in current_evidence(sc) if e.path == "tests/test_main.py"]


def test_trivializing_tests_regresses(fake_repo):
    fake_repo.commit({"main.py": API_ONLY, "tests/test_main.py": TESTS}, "tests")
    job = make_job()
    sc = make_scope()
    scan(sc, "initial")
    fake_repo.commit({"tests/test_main.py": "def test_ok():\n    assert True\n"}, "trivialize")
    scan(sc, "poll")
    _, r = latest(job)
    assert r["automated_testing"].verdict == "NOT_DEMONSTRATED_IN_SCOPE"


def test_rename_leaves_no_broken_current_citation(fake_repo):
    fake_repo.commit({"main.py": MAIN_USING_DB, "db.py": DB_CODE}, "base")
    job = make_job()
    sc = make_scope()
    scan(sc, "initial")
    fake_repo.commit({"db.py": None, "storage/db.py": DB_CODE}, "move db module")
    scan(sc, "poll")
    cur = current_evidence(sc)
    assert not [e for e in cur if e.path == "db.py"]
    assert [e for e in cur if e.path == "storage/db.py" and e.skill_key == "sql"]
    for e in cur:
        assert fake_repo.head in e.permalink or e.commit_sha in e.permalink


def test_multi_skill_commit_only_reassesses_affected(fake_repo):
    fake_repo.commit({"main.py": API_ONLY}, "base")
    job = make_job()
    sc = make_scope()
    scan(sc, "initial")
    fake_repo.commit({"db.py": DB_CODE, "Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt\nUSER app\nCMD [\"uvicorn\", \"main:app\"]\n"}, "db + docker")
    scan(sc, "poll")
    a, r = latest(job)
    assert r["containerization"].verdict == "DEMONSTRATED"
    assert r["api_development"].carried_from_row_id is not None  # untouched
    assert "kubernetes" in r and r["kubernetes"].carried_from_row_id is not None


def test_noop_when_head_already_analysed_and_out_of_order_reconciles(fake_repo):
    fake_repo.commit({"main.py": API_ONLY}, "a")
    job = make_job()
    sc = make_scope()
    scan(sc, "initial")
    b = fake_repo.commit({"db.py": DB_CODE}, "b")
    c = fake_repo.commit({"tests/test_main.py": TESTS}, "c")
    # a delayed delivery for b arrives after head already moved to c: worker reconciles to the real head
    res = scan(sc, "webhook")
    assert res["commit_sha"] == c
    again = scan(sc, "webhook")  # duplicate/out-of-order delivery for b -> nothing to do
    assert again["status"] == "noop"
    _, r = latest(job)
    assert r["automated_testing"].verdict == "DEMONSTRATED" and r["sql"].verdict in ("DEMONSTRATED", "PARTIAL")


def test_scope_change_creates_new_version(fake_repo):
    fake_repo.commit({"main.py": API_ONLY, "svc/db.py": DB_CODE}, "a")
    job = make_job()
    sc1 = make_scope(include=["svc/"])
    scan(sc1, "initial")
    a1, r1 = latest(job)
    assert r1["api_development"].verdict == "NOT_DEMONSTRATED_IN_SCOPE"  # main.py outside include
    from backend.app.database import session_scope
    from backend.app.models import AnalysisScope

    with session_scope() as db:
        db.get(AnalysisScope, sc1).active = False
    sc2 = make_scope()
    scan(sc2, "scope_change")
    a2, r2 = latest(job)
    assert a2.version > a1.version and r2["api_development"].verdict == "DEMONSTRATED"
