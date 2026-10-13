"""Tests de la vérification on-chain du paiement (D27).

Aucun accès réseau : le point d'entrée RPC (`app.onchain._rpc`) est remplacé par
une fonction déterministe. Sont couverts le cas nominal et tous les refus
*fail-closed* (hash malformé, transaction inconnue ou rejetée, mauvais
destinataire, mauvais contrat, montant insuffisant, confirmations insuffisantes,
RPC injoignable).

Articulation avec D15/W1 : la preuve à secret partagé n'est servie que dans une
**recette déclarée** (`TEST_MODE=true`, régime que la suite déclare dans
`conftest.py`) ; hors recette elle est refusée — avec *ou* sans vérification
on-chain — et la décision porte la raison du refus.
"""

import httpx
import pytest

from app import onchain
from app.config import Settings, get_settings
from app.payment import (
    PROOF_HEADER,
    build_payment_proof,
    payment_result,
    verify_payment,
)

settings = get_settings()

# Le portefeuille de `conftest.py` est volontairement factice (« 0xTESTWALLET… »),
# donc les tests de ce module travaillent sur une adresse bien formée : la
# vérification refuse par principe un destinataire malformé (fail-closed).
WALLET = "0x" + "aa" * 20
OTHER_ADDRESS = "0x" + "11" * 20
TX = "0x" + "ab" * 32
BLOCK = 0x1000
UNITS = 20_000  # 0,02 USDC


@pytest.fixture(autouse=True)
def _wellformed_wallet(monkeypatch):
    monkeypatch.setattr(settings, "cdp_wallet_address", WALLET)
    return WALLET


def _transfer_log(recipient: str, units: int, contract: str | None = None) -> dict:
    """Un log ERC-20 Transfer(address,address,uint256) bien formé."""
    return {
        "address": contract or settings.x402_usdc_contract,
        "topics": [
            onchain.TRANSFER_TOPIC,
            "0x" + "00" * 12 + "22" * 20,
            "0x" + "00" * 12 + recipient[2:].lower(),
        ],
        "data": "0x" + format(units, "064x"),
    }


def _receipt(logs: list[dict], status: str = "0x1", block: int = BLOCK) -> dict:
    return {"status": status, "blockNumber": hex(block), "logs": logs}


class _FakeRpc:
    """RPC simulé : reçu fourni, puis hauteur de bloc ; trace les appels."""

    def __init__(self, receipt: dict | None, latest: int = BLOCK + 5) -> None:
        self.receipt = receipt
        self.latest = latest
        self.calls: list[str] = []

    def __call__(self, method: str, params: list) -> object:
        self.calls.append(method)
        if method == "eth_getTransactionReceipt":
            return self.receipt
        if method == "eth_blockNumber":
            return hex(self.latest)
        raise AssertionError(f"methode RPC inattendue : {method}")


class _Request:
    """Requête minimale : `verify_payment` ne lit que l'en-tête de preuve."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


@pytest.fixture
def onchain_enabled(monkeypatch):
    """Hors recette, la chaîne est la seule autorité (W1) : on le dit ici."""
    monkeypatch.setattr(settings, "x402_onchain_verify", True)
    monkeypatch.setattr(settings, "test_mode", False)
    return settings


@pytest.fixture
def production(monkeypatch):
    """Régime du VPS : `TEST_MODE` absent, la recette n'est pas déclarée (W1)."""
    monkeypatch.setattr(settings, "test_mode", False)
    return settings


def test_onchain_verification_is_the_default():
    """W1 : la couche on-chain est active par défaut.

    Le défaut `false` était un fail-open : un déploiement qui omettait la
    variable servait le service contre une preuve synthétique (AUDIT_TRAFIC_VPS
    §5). Ce test échoue si le défaut repart en arrière.
    """
    assert Settings.model_fields["x402_onchain_verify"].default is True


def test_amount_conversion_uses_six_decimals():
    assert onchain.usdc_units(0.02) == 20_000
    assert onchain.usdc_units(1) == 1_000_000


def test_malformed_hash_is_rejected_without_network(monkeypatch):
    rpc = _FakeRpc(None)
    monkeypatch.setattr(onchain, "_rpc", rpc)
    for bad in ("test_tx:1234567890:test_signature", "0xabc", "", "0x" + "zz" * 32):
        ok, reason = onchain.check_usdc_transfer(bad)
        assert ok is False
        assert "malforme" in reason
    assert rpc.calls == [], "un hash malforme ne doit declencher aucun appel reseau"


def test_unknown_transaction_is_rejected(monkeypatch):
    monkeypatch.setattr(onchain, "_rpc", _FakeRpc(None))
    ok, reason = onchain.check_usdc_transfer(TX)
    assert ok is False
    assert "inconnue" in reason


def test_missing_wallet_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "cdp_wallet_address", "")
    ok, reason = onchain.check_usdc_transfer(TX)
    assert ok is False
    assert "destinataire" in reason


def test_malformed_wallet_is_rejected(monkeypatch):
    """Un destinataire tronqué ne doit pas être rapproché des derniers 40 octets."""
    monkeypatch.setattr(settings, "cdp_wallet_address", "0xTESTWALLET0000000000000000000000000000000")
    ok, reason = onchain.check_usdc_transfer(TX)
    assert ok is False
    assert "malforme" in reason


# --- suite ---

def test_reverted_transaction_is_rejected(monkeypatch):
    monkeypatch.setattr(onchain, "_rpc", _FakeRpc(_receipt([], status="0x0")))
    ok, reason = onchain.check_usdc_transfer(TX)
    assert ok is False
    assert "non confirmee" in reason


def test_transfer_to_another_recipient_is_rejected(monkeypatch):
    monkeypatch.setattr(
        onchain, "_rpc", _FakeRpc(_receipt([_transfer_log(OTHER_ADDRESS, UNITS)]))
    )
    ok, reason = onchain.check_usdc_transfer(TX)
    assert ok is False
    assert "aucun transfert USDC" in reason


def test_transfer_from_another_contract_is_rejected(monkeypatch):
    monkeypatch.setattr(
        onchain,
        "_rpc",
        _FakeRpc(_receipt([_transfer_log(WALLET, UNITS, contract=OTHER_ADDRESS)])),
    )
    ok, _ = onchain.check_usdc_transfer(TX)
    assert ok is False


def test_underpaid_transfer_is_rejected(monkeypatch):
    monkeypatch.setattr(
        onchain, "_rpc", _FakeRpc(_receipt([_transfer_log(WALLET, UNITS - 1)]))
    )
    ok, reason = onchain.check_usdc_transfer(TX)
    assert ok is False
    assert "montant insuffisant" in reason


def test_insufficient_confirmations_are_rejected(monkeypatch):
    monkeypatch.setattr(settings, "x402_min_confirmations", 3)
    monkeypatch.setattr(
        onchain, "_rpc", _FakeRpc(_receipt([_transfer_log(WALLET, UNITS)]), latest=BLOCK)
    )
    ok, reason = onchain.check_usdc_transfer(TX)
    assert ok is False
    assert "confirmation" in reason


def test_unreachable_rpc_fails_closed(monkeypatch):
    def boom(method: str, params: list) -> object:
        raise httpx.ConnectError("RPC injoignable")

    monkeypatch.setattr(onchain, "_rpc", boom)
    ok, reason = onchain.check_usdc_transfer(TX)
    assert ok is False
    assert "impossible" in reason


def test_confirmed_transfer_is_accepted(monkeypatch):
    rpc = _FakeRpc(_receipt([_transfer_log(WALLET, UNITS)]), latest=BLOCK + 11)
    monkeypatch.setattr(onchain, "_rpc", rpc)
    ok, reason = onchain.check_usdc_transfer(TX)
    assert ok is True
    assert "0.020000 USDC" in reason
    assert "12 confirmation(s)" in reason


def test_verify_payment_serves_a_shared_secret_proof_in_a_declared_recette():
    """W1/D15 : en recette déclarée, la preuve synthétique reste servie.

    C'est le seul chemin qui ouvre un `200` sans encaissement vérifié, il est
    journalisé (WARNING) et la ligne écrite est marquée `test_mode` (D40).
    """
    assert settings.test_mode is True  # conftest.py déclare la suite en recette
    proof = build_payment_proof("0x" + "cd" * 32)
    assert verify_payment(_Request({PROOF_HEADER: proof})) is True


def test_verify_payment_refuses_a_shared_secret_proof_outside_a_recette(
    production, monkeypatch
):
    """W1 : hors recette, connaître le secret ne suffit plus.

    La signature est parfaitement valide ; c'est l'encaissement qui manque, et
    c'est la chaîne qui le dit (ici un reçu absent, donc aucun octet sorti).
    """
    rpc = _FakeRpc(None)
    monkeypatch.setattr(onchain, "_rpc", rpc)
    proof = build_payment_proof(TX)
    assert verify_payment(_Request({PROOF_HEADER: proof})) is False
    assert rpc.calls == ["eth_getTransactionReceipt"]


def test_verify_payment_refuses_a_proof_when_onchain_is_cut_outside_a_recette(
    production, monkeypatch
):
    """W1 : couper la vérification hors recette ne rouvre pas D15, elle ferme.

    `X402_ONCHAIN_VERIFY=false` sur une production n'est donc pas un fail-open :
    toute preuve à secret partagé est refusée, sans même interroger la chaîne.
    """
    monkeypatch.setattr(settings, "x402_onchain_verify", False)
    rpc = _FakeRpc(None)
    monkeypatch.setattr(onchain, "_rpc", rpc)
    decision = payment_result(_Request({PROOF_HEADER: build_payment_proof("0xMADEUP")}))
    assert decision.paid is False
    assert "hors recette" in decision.reason
    assert rpc.calls == [], "une couche coupée ne doit pas interroger la chaîne"


def test_verify_payment_rejects_fake_proof_when_onchain_enabled(
    onchain_enabled, monkeypatch
):
    """Preuve correctement signée mais transaction inexistante : refus."""
    monkeypatch.setattr(onchain, "_rpc", _FakeRpc(None))
    proof = build_payment_proof(TX)
    assert verify_payment(_Request({PROOF_HEADER: proof})) is False


def test_verify_payment_accepts_proof_with_confirmed_transfer(
    onchain_enabled, monkeypatch
):
    monkeypatch.setattr(
        onchain, "_rpc", _FakeRpc(_receipt([_transfer_log(WALLET, UNITS)]))
    )
    proof = build_payment_proof(TX)
    assert verify_payment(_Request({PROOF_HEADER: proof})) is True
