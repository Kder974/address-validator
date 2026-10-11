"""Budget du chemin dégradé (D23 + D25) — preuve déterministe, sans horloge.

Recette réelle (voir TEST_REPORT.md) : une réponse dégradée coûtait 15,2 s
(timeout fournisseur de 10 s × retries), puis encore 4,88 s après D23 — le
reliquat était payé à Redis **à chaque requête** (D25).

Ce module ne mesure pas des secondes : il compte les **tentatives**. Un retour de
la régression (attente réseau répétée, fournisseur martelé) augmente un compteur
et fait échouer le test ; une machine lente, elle, ne le fait pas échouer.
"""

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.payment import build_payment_proof

client = TestClient(app)


class _BrokenRedis:
    """Backend Redis éteint : chaque tentative échoue immédiatement."""

    def __init__(self) -> None:
        self.attempts = 0

    def get(self, key):
        self.attempts += 1
        raise ConnectionError("redis down")

    def setex(self, key, ttl, value):
        self.attempts += 1
        raise ConnectionError("redis down")

    def ping(self):
        self.attempts += 1
        raise ConnectionError("redis down")


class _MemRedis:
    """Backend Redis en mémoire, qui compte ses sollicitations."""

    def __init__(self) -> None:
        self.attempts = 0
        self.store: dict = {}

    def get(self, key):
        self.attempts += 1
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.attempts += 1
        self.store[key] = value

    def ping(self):
        self.attempts += 1
        return True


def _failing_provider(monkeypatch) -> dict:
    """Fournisseur injoignable : compte les appels réellement tentés."""
    from app.validation import verifier

    counter = {"attempts": 0}

    def _raise(*args, **kwargs):
        counter["attempts"] += 1
        raise httpx.ConnectError("fournisseur simulé injoignable")

    monkeypatch.setattr(verifier, "_call_smarty", _raise)
    return counter


def _payload(n: int) -> dict:
    return {
        "address": {
            "street": f"{100 + n} Degraded Way",
            "city": "Mountain View",
            "state": "CA",
            "zip": f"9404{n}",
            "country": "US",
        }
    }


def test_degraded_burst_stops_hitting_provider_and_redis(monkeypatch):
    """6 requêtes payantes en panne totale : 3 appels fournisseur, 1 accès Redis."""
    import app.cache as cache
    from app.validation.verifier import breaker

    cache.reset_backend_state()
    breaker.reset()

    redis = _BrokenRedis()
    monkeypatch.setattr(cache, "redis_client", redis)
    provider = _failing_provider(monkeypatch)
    headers = {"X-Payment-Proof": build_payment_proof("0xdegraded-burst")}

    burst = 6
    responses = [
        client.post("/validate", json=_payload(index), headers=headers)
        for index in range(burst)
    ]

    assert [response.status_code for response in responses] == [200] * burst
    assert all(response.json()["result"]["degraded"] is True for response in responses)

    # D23 : le disjoncteur ouvre au seuil, ensuite la réponse est locale.
    assert provider["attempts"] == breaker.failure_threshold, (
        "disjoncteur ouvert : plus aucune tentative fournisseur attendue"
    )
    # D25 : un seul timeout Redis payé pour toute la salve.
    assert redis.attempts == 1, (
        "le cooldown doit éviter de re-payer un Redis injoignable à chaque requête"
    )
    assert cache.cache_backend_available() is False
    assert cache.cache_error_count() == 1


def test_cache_recovery_after_cooldown_restores_hits(monkeypatch):
    """D25 n'est pas un coupe-circuit définitif : Redis revenu, le cache resert."""
    import app.cache as cache
    from app.validation.verifier import breaker

    cache.reset_backend_state()
    breaker.reset()

    monkeypatch.setattr(cache, "redis_client", _BrokenRedis())
    provider = _failing_provider(monkeypatch)
    headers = {"X-Payment-Proof": build_payment_proof("0xdegraded-recovery")}

    first = client.post("/validate", json=_payload(1), headers=headers)
    assert first.status_code == 200
    assert first.json()["result"]["degraded"] is True
    assert cache.cache_backend_available() is False

    # Redis revient et la fenêtre de cooldown est écoulée.
    back = _MemRedis()
    monkeypatch.setattr(cache, "redis_client", back)
    monkeypatch.setattr(cache, "_down_until", 0.0)

    second = client.post("/validate", json=_payload(1), headers=headers)
    third = client.post("/validate", json=_payload(1), headers=headers)

    assert second.status_code == 200
    assert third.status_code == 200
    assert provider["attempts"] == 2, "2e appel : recalcul, puis mise en cache"
    assert third.json()["result"]["cache_key"] == second.json()["result"]["cache_key"]
    assert back.attempts >= 2, "le backend rétabli doit être resollicité"
    assert provider["attempts"] == 2, "3e appel servi par le cache, fournisseur intact"
