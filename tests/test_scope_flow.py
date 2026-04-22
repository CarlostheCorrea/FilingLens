import pytest
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_propose_requires_query(client):
    res = client.post("/api/scope/propose", json={})
    assert res.status_code == 422


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_propose_scope(client):
    res = client.post("/api/scope/propose", json={"query": "semiconductor supply chain risk"})
    assert res.status_code == 200
    data = res.json()
    assert "proposal_id" in data
    assert "companies" in data
    assert "form_types" in data


def test_approve_scope_missing_proposal(client):
    res = client.post("/api/scope/approve", json={
        "proposal_id": "nonexistent_id",
        "approved_companies": [],
        "form_types": ["10-K"],
        "date_range": ["2024-01-01", "2025-12-31"],
    })
    # Should succeed (approval doesn't require proposal to exist)
    assert res.status_code == 200
