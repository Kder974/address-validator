"""Tests de la couche paiement / x402 (spec Phase 7, corrigés).

Correction D7 : le test fourni par la spec envoyait une preuve
`tx_hash:1234567890:signature` — un timestamp de février 2009, donc toujours
périmé au regard de la fenêtre de 300 s. Le test aurait échoué avec le code
fourni. On construit ici des preuves valides via `build_payment_proof()`.
"""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.payment import build_payment_proof

client = TestClient(app)

ADDRESS = {
    "street": "123 Main St",
    "city": "New York",
    "state": "NY",
    "zip": "10001",
    "country": "US",
}


def test_payment_required():
    response = client.post("/validate", json={"address": ADDRESS})
    assert response.status_code == 402
    body = response.json()
    assert body["price"] == 0.02
    assert body["currency"] == "USDC"
    assert body["protocol"] == "x402"


def test_payment_required_includes_wallet():
    response = client.post("/validate", json={"address": ADDRESS})
    assert response.status_code == 402
    assert "wallet" in response.json()


def test_valid_payment():
    response = client.post(
        "/validate",
        json={
            "address": {
                "street": "1600 Amphitheatre Parkway",
                "city": "Mountain View",
                "state": "CA",
                "zip": "94043",
                "country": "US",
            }
        },
        headers={"X-Payment-Proof": build_payment_proof("0xabc123")},
    )
    assert response.status_code == 200
    assert "result" in response.json()
    assert response.json()["price_paid"] == 0.02


def test_stale_proof_is_rejected():
    proof = build_payment_proof("0xabc123", timestamp=1234567890)  # 2009
    response = client.post(
        "/validate", json={"address": ADDRESS}, headers={"X-Payment-Proof": proof}
    )
    assert response.status_code == 402


def test_future_proof_is_rejected():
    proof = build_payment_proof("0xabc123", timestamp=time.time() + 10_000)
    response = client.post(
        "/validate", json={"address": ADDRESS}, headers={"X-Payment-Proof": proof}
    )
    assert response.status_code == 402


def test_tampered_signature_is_rejected():
    timestamp = str(time.time())
    response = client.post(
        "/validate",
        json={"address": ADDRESS},
        headers={"X-Payment-Proof": f"0xabc123:{timestamp}:deadbeef"},
    )
    assert response.status_code == 402


def test_malformed_proof_is_rejected():
    response = client.post(
        "/validate",
        json={"address": ADDRESS},
        headers={"X-Payment-Proof": "not-a-valid-proof"},
    )
    assert response.status_code == 402


def test_transaction_id_is_echoed():
    response = client.post(
        "/validate",
        json={"address": ADDRESS},
        headers={
            "X-Payment-Proof": build_payment_proof("0xabc123"),
            "X-Transaction-Id": "0xtx-42",
        },
    )
    assert response.status_code == 200
    assert response.json()["transaction_id"] == "0xtx-42"
