import pytest
from fastapi.testclient import TestClient
from fixtures.controlled_repo.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

def test_create_and_list_payments():
    payload = {"amount": 100.50, "currency": "USD", "recipient_id": "rec_123"}
    create_resp = client.post("/api/v1/payments", json=payload)
    assert create_resp.status_code == 200
    data = create_resp.json()
    assert data["amount"] == 100.50
    assert data["status"] == "completed"

    list_resp = client.get("/api/v1/payments")
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) >= 1
