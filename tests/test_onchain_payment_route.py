"""Porte de paiement mesurée **au niveau de la route** (`POST /validate`) — W3.

`tests/test_onchain.py` mesure la couche (`verify_payment`,
`check_usdc_transfer`) et `tests/test_payment.py` la signature et les refus de
forme. Ce module mesure le **contrat rendu au client** : ce que la route répond,
et ce qu'elle écrit au registre, dans le **régime du VPS** (`TEST_MODE` absent,
donc recette non déclarée). C'est le régime où l'audit du trafic
(`AUDIT_TRAFIC_VPS.md` §5.1) a observé des `200` sans ligne au registre : un
test de couche ne peut pas trancher cette question, une mesure de route le peut.

Trois contrats y sont fixés :

* hors recette, un `200` exige un transfert USDC **confirmé** vers le
  portefeuille d'encaissement — une signature HMAC parfaitement valide ne suffit
  plus (W1/D27) ;
* la ligne du registre porte le hash **confirmé** (D29), et rien n'est écrit
  quand la route refuse (D34 : aucun service rendu sans paiement, aucun
  encaissement compté sans paiement) ;
* la recette déclarée (`TEST_MODE=true`) reste le seul régime où la preuve à
  secret partagé ouvre un `200` sans encaissement — et ce `200` y laisse une
  ligne au registre, sinon il serait indistinguable d'un revenu client (D40/W1).

Aucun octet ne sort : le fournisseur d'adresses, le cache, la base et le seul
point d'entrée réseau de la couche on-chain (`app.onchain._rpc`) reçoivent tous
un double déterministe. Ces tests ne mesurent donc que la porte de paiement, et
jamais la disponibilité de l'environnement — sur un VPS, `SMARTY_AUTH_ID` est
présent et le filet hors-ligne de `conftest.py` ne se déclenche pas.
"""

import pytest
from fastapi.testclient import TestClient

from app import onchain
from app.config import get_settings
from app.main import app
from app.payment import PROOF_HEADER, build_payment_proof

# Doubles et constantes partagés avec les tests de la couche (D27) : la route ne
# doit pas mesurer autre chose que ce que la couche mesure.
from tests.test_onchain import (
    OTHER_ADDRESS,
    TX,
    UNITS,
    WALLET,
    _FakeRpc,
    _receipt,
    _transfer_log,
)

settings = get_settings()
client = TestClient(app)

ADDRESS = {
    "street": "1 A St",
    "city": "X",
    "state": "CA",
    "zip": "90001",
    "country": "US",
}

#: Résultat de validation déterministe : la porte de paiement est le sujet, pas
#: le fournisseur d'adresses (mesuré ailleurs, D21/D22).
RESULTAT = {
    "validated": False,
    "standardized": dict(ADDRESS),
    "latitude": None,
    "longitude": None,
    "deliverability": "unknown",
    "confidence": 0.2,
    "degraded": True,
}


@pytest.fixture(autouse=True)
def _hors_ligne(monkeypatch):
    """Fournisseur, cache et base doublés : seul le paiement reste mesuré."""
    from app.routes import validate as route

    monkeypatch.setattr(route, "validate_full", lambda raw: dict(RESULTAT))
    monkeypatch.setattr(route, "get_cached_result", lambda key: None)
    monkeypatch.setattr(route, "store_in_cache", lambda key, value: None)


@pytest.fixture(autouse=True)
def _wallet_bien_forme(monkeypatch):
    """Le portefeuille de `conftest.py` est volontairement factice.

    La couche on-chain refuse par principe un destinataire malformé
    (fail-closed, D27) : sans cette adresse bien formée, tous les tests de ce
    module mesureraient le même refus de forme, pas la porte de paiement.
    """
    monkeypatch.setattr(settings, "cdp_wallet_address", WALLET)
    return WALLET


@pytest.fixture
def production(monkeypatch):
    """Le régime du VPS : recette non déclarée, chaîne seule autorité (W1)."""
    monkeypatch.setattr(settings, "test_mode", False)
    monkeypatch.setattr(settings, "x402_onchain_verify", True)
    return settings


def _post(tx_hash: str):
    """Requête payée par la voie héritée (`X-Payment-Proof`), adresse fixe."""
    return client.post(
        "/validate",
        json={"address": ADDRESS},
        headers={PROOF_HEADER: build_payment_proof(tx_hash)},
    )


def test_an_unpaid_request_advertises_the_valuation_before_payment(production):
    """Régression W3 : le point de vente répond `402` avec le prix et le wallet."""
    response = client.post("/validate", json={"address": ADDRESS})
    assert response.status_code == 402
    body = response.json()
    assert body["price"] == 0.02 and body["currency"] == "USDC"
    assert body["protocol"] == "x402" and body["wallet"] == WALLET


def test_a_confirmed_transfer_is_what_opens_the_route(production, ledger, monkeypatch):
    """W1/D27/D29 : le `200` vient de la chaîne, et la ligne porte le hash confirmé.

    L'assertion qui compte n'est pas seulement le `200` : c'est que le registre
    reçoive **le hash du transfert confirmé**, au prix annoncé. Un `200` dont la
    ligne ne correspond pas au paiement réel serait un revenu faux (D40), donc
    le contrat est mesuré des deux côtés à la fois.
    """
    monkeypatch.setattr(
        onchain, "_rpc", _FakeRpc(_receipt([_transfer_log(WALLET, UNITS)]))
    )
    response = _post(TX)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transaction_id"] == TX
    assert body["price_paid"] == 0.02
    assert body["result"]["degraded"] is True  # fournisseur coupé, résultat local
    assert ledger == [(TX, 0.02, body["result"]["cache_key"])]


def test_a_signed_proof_the_chain_does_not_know_is_refused(
    production, ledger, monkeypatch
):
    """D15 ne rouvre pas la porte : le secret partagé ne remplace pas la chaîne."""
    rpc = _FakeRpc(None)
    monkeypatch.setattr(onchain, "_rpc", rpc)
    response = _post(TX)

    assert response.status_code == 402
    body = response.json()
    assert body["error"] == "transaction inconnue de la chaine"
    assert body["price"] == 0.02 and body["currency"] == "USDC"
    assert ledger == [], "un refus ne doit rien inscrire au registre (D34)"
    assert rpc.calls == ["eth_getTransactionReceipt"], "la chaîne n'a pas été interrogée"


def test_a_transfer_below_the_announced_price_is_refused(production, ledger, monkeypatch):
    """Le montant est le prix annoncé, pas « un transfert quelconque »."""
    monkeypatch.setattr(
        onchain, "_rpc", _FakeRpc(_receipt([_transfer_log(WALLET, UNITS - 1)]))
    )
    response = _post(TX)

    assert response.status_code == 402
    assert "montant insuffisant" in response.json()["error"]
    assert ledger == []


def test_a_transfer_to_another_wallet_is_refused(production, ledger, monkeypatch):
    """Un transfert USDC vers une autre adresse ne paie pas ce service."""
    monkeypatch.setattr(
        onchain,
        "_rpc",
        _FakeRpc(_receipt([_transfer_log(OTHER_ADDRESS, UNITS * 100)])),
    )
    response = _post(TX)

    assert response.status_code == 402
    assert "aucun transfert USDC" in response.json()["error"]
    assert ledger == []


def test_a_rejected_transaction_pays_nothing(production, ledger, monkeypatch):
    """`status != 0x1` : la transaction a échoué, elle n'a rien encaissé."""
    monkeypatch.setattr(
        onchain,
        "_rpc",
        _FakeRpc(_receipt([_transfer_log(WALLET, UNITS)], status="0x0")),
    )
    response = _post(TX)

    assert response.status_code == 402
    assert "non confirmee" in response.json()["error"]
    assert ledger == []


def test_an_unreachable_chain_fails_closed_at_the_route(production, ledger, monkeypatch):
    """Chaîne injoignable : pas de `200` optimiste, pas d'écriture, prix respecté."""

    def boom(method: str, params: list) -> object:
        raise RuntimeError("RPC en panne")

    monkeypatch.setattr(onchain, "_rpc", boom)
    response = _post(TX)

    assert response.status_code == 402
    assert "verification on-chain impossible" in response.json()["error"]
    assert ledger == []


def test_the_recette_is_the_only_regime_where_the_shared_secret_serves(
    production, ledger, monkeypatch
):
    """W1 : le même en-tête, deux régimes, deux réponses — et une seule trace.

    Hors recette, la preuve est refusée et le registre reste vide ; en recette
    déclarée, elle est servie (c'est le harnais de l'exploitant qui contrôle le
    service sans dépenser 0,02 USDC réels) mais la chaîne n'est même pas
    interrogée. C'est ce second chemin que §5.1 de l'audit observait : un `200`
    qui **doit** laisser une ligne, sans quoi il serait indistinguable d'un
    revenu client réel (D40).
    """
    rpc = _FakeRpc(None)
    monkeypatch.setattr(onchain, "_rpc", rpc)
    refusee = _post(TX)
    assert refusee.status_code == 402
    assert ledger == []

    monkeypatch.setattr(settings, "test_mode", True)
    servie = _post(TX)
    assert servie.status_code == 200
    assert servie.json()["transaction_id"] == TX
    assert ledger == [(TX, 0.02, servie.json()["result"]["cache_key"])]
    assert rpc.calls == ["eth_getTransactionReceipt"], (
        "la recette déclarée ne doit pas interroger la chaîne : une seule requête, "
        "celle de la tentative refusée hors recette"
    )
