"""Configuration commune des tests.

Objectif : une suite **déterministe et hors-ligne**.

* Les variables d'environnement sont posées *avant* tout import de `app`, sur
  des ports/URLs volontairement injoignables, afin que les sondes DB/Redis
  échouent vite sans pendre.
* Sans identifiants SmartyStreets, le fournisseur est de toute façon
  injoignable : on simule une erreur de connexion déterministe au lieu d'un
  vrai appel réseau. C'est exactement le scénario « fournisseur indisponible »
  que le critère de succès n°4 doit couvrir.
* Le disjoncteur est réinitialisé avant/après chaque test.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Défaut d'environnement mesuré : sur ce Windows, une stratégie de contrôle
# d'application bloque `Crypto/Hash/_keccak.pyd` ([WinError 4551]), ce qui rend
# `eth_account`/`eth_utils` inutilisables. On installe alors un keccak Python pur
# (tests/keccak_fallback.py) AVANT tout import de `eth_account`, sinon les tests
# du protocole x402 ne mesurent plus rien. Sur Linux, le backend natif est
# utilisé et ce filet n'est pas installé (voir TEST_REPORT.md).
from tests.keccak_fallback import install_if_needed  # noqa: E402

KECCAK_FALLBACK_INSTALLED = install_if_needed()

os.environ.setdefault("API_SECRET_KEY", "test_secret_key")
os.environ.setdefault("DATABASE_URL", "postgresql://user:password@127.0.0.1:5433/address_validator_test")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6399/0")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SMARTY_AUTH_ID", "")
os.environ.setdefault("SMARTY_AUTH_TOKEN", "")
os.environ.setdefault("CDP_API_KEY_ID", "")
os.environ.setdefault("CDP_API_KEY_SECRET", "")
# Adresse de test volontairement valide (40 hex) : les chemins x402 (D34-D36)
# refusent une adresse malformee *avant* tout appel reseau, donc une adresse
# bidon interdirait de tester tout ce qui suit ce refus.
os.environ.setdefault("CDP_WALLET_ADDRESS", "0x1234567890abcdef1234567890abcdef12345678")

import pytest  # noqa: E402


class FakeRedis:
    """Client Redis en mémoire (remplace le vrai pour les tests de cache)."""

    def __init__(self):
        self.store: dict = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def ping(self):
        return True


@pytest.fixture(autouse=True)
def _offline_provider(monkeypatch):
    """Neutralise tout appel réseau sortant pendant les tests."""
    import httpx

    import app.cache as cache
    import app.validation as validation
    from app.config import get_settings
    from app.validation import verifier

    verifier.breaker.reset()
    cache.reset_backend_state()

    if not get_settings().smarty_auth_id:

        def _no_provider(*args, **kwargs):
            raise httpx.ConnectError(
                "SMARTY_AUTH_ID absent : fournisseur simulé injoignable"
            )

        monkeypatch.setattr(verifier, "_call_smarty", _no_provider)
        monkeypatch.setattr(
            validation, "geocode", lambda standardized: {"lat": None, "lng": None}
        )

    yield
    verifier.breaker.reset()
    cache.reset_backend_state()


@pytest.fixture
def fake_redis(monkeypatch):
    """Remplace le client Redis par une version en mémoire."""
    import app.cache as cache

    client = FakeRedis()
    monkeypatch.setattr(cache, "redis_client", client)
    return client


@pytest.fixture
def facilitator(monkeypatch):
    """Facilitateur x402 simulé : `/verify` et `/settle` répondent comme le vrai.

    Partagé (`conftest`) pour que la route HTTP et l'outil MCP soient testés avec
    exactement le même double. Aucun octet ne sort : c'est le protocole qui est
    mesuré, pas la disponibilité de la chaîne.
    """
    from app import x402

    calls: list[tuple[str, dict]] = []

    def fake_post(suffix: str, body: dict) -> dict:
        calls.append((suffix, body))
        payer = body["paymentPayload"]["payload"]["authorization"]["from"]
        if suffix == "/verify":
            return {"isValid": True, "payer": payer}
        return {
            "success": True,
            "transaction": "0x" + "cd" * 32,
            "network": "base",
            "payer": payer,
        }

    monkeypatch.setattr(x402, "_post_facilitator", fake_post)
    x402.reset_nonce_cache()
    yield calls
    x402.reset_nonce_cache()
