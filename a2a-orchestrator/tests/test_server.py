"""Tests for A2A Orchestrator server."""

import pytest
from fastapi.testclient import TestClient

from a2a_orchestrator.server import app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


def test_health(client):
    """Test health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_list_agents(client):
    """Test agents listing."""
    response = client.get("/v1/agents")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert "weights" in data
    # Should have 5 specialists
    assert len(data["agents"]) == 5
    assert "devops" in data["agents"]
    assert "security" in data["agents"]


def test_investigate_validation(client):
    """Test request validation."""
    # Missing required fields
    response = client.post("/v1/investigate", json={})
    assert response.status_code == 422

    # Valid request structure
    response = client.post("/v1/investigate", json={
        "request_id": "test-123",
        "alert": {
            "name": "TestAlert",
            "severity": "warning"
        }
    })
    # Will return 200 even if specialists fail (graceful degradation)
    assert response.status_code == 200
