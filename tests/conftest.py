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
* Depuis D41 (W2), un paiement dont l'inscription au registre échoue n'est plus
  servi (`500`) : la suite remplace donc `record_transaction` par un double qui
  accepte et retient la ligne, afin qu'aucun test ne dépende d'un PostgreSQL
  joignable. Le test qui mesure cet échec remet lui-même un refus.
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

# Recette déclarée (W1). Depuis le durcissement W1, une preuve à secret partagé
# (`X-Payment-Proof`) n'est servie QUE dans une recette déclarée : hors recette,
# seule une confirmation on-chain ouvre un `200` (app/payment.py). La suite
# n'encaisse rien et ne parle à aucune chaîne : elle se déclare donc en recette,
# exactement comme le harnais de l'exploitant (`scripts/e2e_check.py`), et les
# lignes qu'elle écrirait seraient marquées `test_mode` (D40). Les tests qui
# mesurent le régime de production remettent `test_mode=False` eux-mêmes.
os.environ.setdefault("TEST_MODE", "true")

import pytest  # noqa: E402


class FakeRedis:
    """Client Redis en mémoire (remplace le vrai pour les tests de cache)."""

    def __init__(self):
        self.store: dict = {}
        #: TTL réellement demandés par `setex` (clé → secondes). Le double les
        #: **retient** sans les appliquer : c'est ce qui permet de mesurer qu'un
        #: résultat dégradé n'est pas gardé trente jours (D47) sans dépendre du
        #: temps qui passe.
        self.ttls: dict = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl

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


@pytest.fixture(autouse=True)
def _offline_ledger(monkeypatch):
    """Neutralise l'écriture au registre (W2/D41) : la suite reste hors-ligne.

    Depuis D41, `/validate` et `/mcp` refusent de servir quand
    `record_transaction` renvoie `False`. Sans ce double, chaque test d'un chemin
    payé dépendrait d'une base joignable — or ici elle ne l'est pas (port 5433
    volontairement fermé), ce qui rendrait la suite à la fois lente et
    dépendante de l'environnement. Le double retient les lignes demandées et
    répond `True` ; le test W2 remplace cette écriture par un refus pour mesurer
    le `500` et le compteur.
    """
    import app.routes.mcp as mcp_route
    import app.routes.validate as http_route

    ecritures: list[tuple[str, float, str]] = []

    def _accepte(tx_hash: str, amount: float, address: str) -> bool:
        ecritures.append((tx_hash, amount, address))
        return True

    monkeypatch.setattr(http_route, "record_transaction", _accepte)
    monkeypatch.setattr(mcp_route, "record_transaction", _accepte)
    yield ecritures


@pytest.fixture
def ledger(_offline_ledger):
    """Lignes que le registre aurait reçues pendant le test (même double)."""
    return _offline_ledger


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


@pytest.fixture(autouse=True)
def _suivi_sans_fil(monkeypatch):
    """D71 : aucun test ne lance le vrai fil d'écriture du suivi (il écrirait et purgerait sur DATABASE_URL)."""
    from app import audience

    monkeypatch.setattr(audience, "_demarrer", lambda: None)
    yield
    audience._FILE.clear()
