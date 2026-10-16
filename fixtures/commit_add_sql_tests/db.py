import sqlite3
from typing import List, Dict, Any

DATABASE_NAME = "payments.db"

def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT UNIQUE NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def insert_payment(transaction_id: str, amount: float, status: str = "completed") -> int:
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO payments (transaction_id, amount, status) VALUES (?, ?, ?)",
        (transaction_id, amount, status)
    )
    conn.commit()
    inserted_id = cursor.lastrowid
    conn.close()
    return inserted_id

def query_payment_by_id(transaction_id: str) -> Dict[str, Any]:
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, transaction_id, amount, status FROM payments WHERE transaction_id = ?",
        (transaction_id,)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {}
    return {"id": row[0], "transaction_id": row[1], "amount": row[2], "status": row[3]}
