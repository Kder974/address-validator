"""Tests des endpoints /health et /metrics."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_status_and_dependencies():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"
    assert "database" in body
    assert "redis" in body


def test_health_stays_fast_when_dependencies_are_down():
    """D22 : /health est borné (mesuré à 6,03 s avant correctif)."""
    import time

    start = time.time()
    assert client.get("/health").status_code == 200
    elapsed = time.time() - start
    assert elapsed < 3.0, f"/health a pris {elapsed:.2f}s avec DB et Redis éteints"



def test_metrics_returns_counters():
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    for field in (
        "requests_total",
        "paid_requests",
        "unpaid_requests",
        "cache_hits",
        "cache_misses",
        "cache_hit_ratio",
        "uptime_seconds",
        "price_per_validation_usdc",
        "provider_circuit_breaker",
        "cache_backend_available",
        "cache_errors",
    ):
        assert field in body
    assert body["price_per_validation_usdc"] == 0.02


def test_metrics_count_unpaid_requests():
    before = client.get("/metrics").json()["unpaid_requests"]
    client.post(
        "/validate",
        json={
            "address": {
                "street": "1 A St",
                "city": "X",
                "state": "CA",
                "zip": "90001",
                "country": "US",
            }
        },
    )
    after = client.get("/metrics").json()["unpaid_requests"]
    assert after == before + 1
