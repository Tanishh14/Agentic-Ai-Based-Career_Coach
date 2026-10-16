# FastAPI Production Gateway
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from typing import List

app = FastAPI(title="Student Core Payment Gateway", version="1.0.0")

class PaymentRequest(BaseModel):
    user_id: str
    amount: float
    currency: str = "USD"

class PaymentResponse(BaseModel):
    transaction_id: str
    status: str
    amount: float

# In-memory transient store (Notice: NO SQL Database configured initially)
transactions = {}

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "payment-api"}

@app.get("/api/v1/payments/{payment_id}", response_model=PaymentResponse)
def get_payment(payment_id: str):
    if payment_id not in transactions:
        raise HTTPException(status_code=404, detail="Payment transaction not found")
    return transactions[payment_id]

@app.post("/api/v1/payments", response_model=PaymentResponse)
def create_payment(req: PaymentRequest):
    tx_id = f"tx_{len(transactions) + 1001}"
    record = PaymentResponse(
        transaction_id=tx_id,
        status="completed",
        amount=req.amount
    )
    transactions[tx_id] = record
    return record
