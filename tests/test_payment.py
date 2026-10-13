"""Tests de la couche paiement / x402 (spec Phase 7, corrigés).

Correction D7 : le test fourni par la spec envoyait une preuve
`tx_hash:1234567890:signature` — un timestamp de février 2009, donc toujours
périmé au regard de la fenêtre de 300 s. Le test aurait échoué avec le code
fourni. On construit ici des preuves valides via `build_payment_proof()`.

Régime des tests (W1) : la suite se déclare en **recette** (`TEST_MODE=true`,
`conftest.py`), car une preuve à secret partagé n'est plus servie hors recette.
Le dernier test mesure justement le régime de production — la preuve y est
correctement signée et pourtant refusée, faute d'encaissement vérifié.
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


def test_valid_payment(ledger):
    """Recette déclarée (W1) : la preuve synthétique est servie, et tracée."""
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
    # D29/D41 : le service rendu a bien ete inscrit au registre (meme double que
    # la base : la suite ne parle a aucun PostgreSQL).
    assert len(ledger) == 1
    assert ledger[0][0] == "0xabc123"
    assert ledger[0][1] == 0.02


def test_ledger_write_failure_refuses_to_serve_the_validation(monkeypatch):
    """W2/D41 : un service rendu mais non inscrit au registre serait un revenu perdu.

    Le client a paye (preuve valide) et la validation a ete calculee : ce qui
    manque, c'est la ligne au registre. Servir un `200` ferait disparaitre un
    encaissement reel du revenu publie (D40) sans que personne ne le sache. Le
    contrat est donc explicite : `500`, aucun `result`, et un compteur qui monte.
    """
    from app.routes import validate as route
    from app.routes.metrics import metrics

    monkeypatch.setattr(route, "record_transaction", lambda tx, amount, key: False)
    avant = metrics.snapshot()["ledger_write_failures_total"]
    response = client.post(
        "/validate",
        json={"address": ADDRESS},
        headers={"X-Payment-Proof": build_payment_proof("0xledger-ko")},
    )
    apres = metrics.snapshot()["ledger_write_failures_total"]

    assert response.status_code == 500
    body = response.json()
    assert "result" not in body
    assert body["error"] == "paiement non enregistre : validation non servie"
    assert "registre" in body["detail"]
    # Le hash du reglement reste dans la reponse : c'est ce qui rend
    # l'encaissement rapprochable par l'exploitant.
    assert body["transaction_id"] == "0xledger-ko"
    assert apres == avant + 1


def test_hmac_proof_is_refused_outside_a_declared_recette(monkeypatch):
    """W1 : hors recette, un `200` exige un encaissement confirmé on-chain.

    La preuve est parfaitement signée : ce qui manque, c'est le transfert USDC
    vers le portefeuille d'encaissement (la chaîne est ici simulée muette, donc
    aucun octet ne sort). Le client doit lire un `402` expliqué, pas un `200`.
    """
    from app import onchain
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "test_mode", False)
    monkeypatch.setattr(onchain, "_rpc", lambda method, params: None)
    response = client.post(
        "/validate",
        json={"address": ADDRESS},
        headers={"X-Payment-Proof": build_payment_proof("0x" + "ab" * 32)},
    )
    assert response.status_code == 402
    body = response.json()
    assert body["protocol"] == "x402" and body["price"] == 0.02
    assert "on-chain" not in body["error"].lower() or "inconnue" in body["error"]
    assert body["error"] == "transaction inconnue de la chaine"


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
