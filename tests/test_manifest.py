"""Tests de l'endpoint de découverte /manifest (format x402 Bazaar)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_manifest_is_exposed():
    response = client.get("/manifest")
    assert response.status_code == 200


def test_manifest_declares_x402_pricing():
    body = client.get("/manifest").json()
    assert body["name"] == "AddressValidator"
    assert body["protocol"] == "x402"
    assert body["currency"] == "USDC"
    assert body["price"] == 0.02


def test_manifest_describes_validate_endpoint():
    body = client.get("/manifest").json()
    assert "validate" in body["endpoints"]
    endpoint = body["endpoints"]["validate"]
    assert endpoint["method"] == "POST"
    assert endpoint["path"] == "/validate"
    assert "input_schema" in endpoint
    assert "output_schema" in endpoint


def test_manifest_has_discovery_metadata():
    body = client.get("/manifest").json()
    assert body["category"] == "data"
    assert "address" in body["tags"]
    assert body["license"] == "MIT"
