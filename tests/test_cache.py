"""Tests de la couche cache (critère de succès n°3).

On remplace le client Redis par une version en mémoire (`fake_redis`) et on
compte les appels réellement transmis au fournisseur : deux requêtes identiques
ne doivent déclencher qu'**un seul** appel fournisseur.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.payment import build_payment_proof

client = TestClient(app)

PAYLOAD = {
    "address": {
        "street": "1600 Amphitheatre Parkway",
        "city": "Mountain View",
        "state": "CA",
        "zip": "94043",
        "country": "US",
    }
}


def _count_provider_calls(monkeypatch) -> dict:
    from app.validation import verifier

    counter = {"calls": 0}

    def _stub(*args, **kwargs):
        counter["calls"] += 1
        return [{"analysis": {"dpv_match_code": "Y"}, "metadata": {}}]

    monkeypatch.setattr(verifier, "_call_smarty", _stub)
    return counter


def test_second_identical_request_hits_cache(fake_redis, monkeypatch):
    counter = _count_provider_calls(monkeypatch)
    headers = {"X-Payment-Proof": build_payment_proof("0xcache-1")}

    first = client.post("/validate", json=PAYLOAD, headers=headers)
    second = client.post("/validate", json=PAYLOAD, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert counter["calls"] == 1, "la 2e requête identique ne doit pas rappeler le fournisseur"
    assert (
        first.json()["result"]["cache_key"] == second.json()["result"]["cache_key"]
    )
    assert first.json()["result"]["validated"] is True


def test_distinct_requests_call_provider_twice(fake_redis, monkeypatch):
    counter = _count_provider_calls(monkeypatch)
    headers = {"X-Payment-Proof": build_payment_proof("0xcache-2")}

    client.post("/validate", json=PAYLOAD, headers=headers)
    other = {
        "address": {
            "street": "1 Infinite Loop",
            "city": "Cupertino",
            "state": "CA",
            "zip": "95014",
            "country": "US",
        }
    }
    client.post("/validate", json=other, headers=headers)

    assert counter["calls"] == 2


def test_cache_hit_ratio_is_reported(fake_redis, monkeypatch):
    _count_provider_calls(monkeypatch)
    headers = {"X-Payment-Proof": build_payment_proof("0xcache-3")}
    client.post("/validate", json=PAYLOAD, headers=headers)
    client.post("/validate", json=PAYLOAD, headers=headers)
    body = client.get("/metrics").json()
    assert body["cache_hits"] >= 1
    assert 0.0 <= body["cache_hit_ratio"] <= 1.0


def test_no_redis_is_not_fatal(monkeypatch):
    """Sans Redis joignable, l'API doit continuer à répondre (dégradation)."""
    headers = {"X-Payment-Proof": build_payment_proof("0xcache-4")}
    response = client.post("/validate", json=PAYLOAD, headers=headers)
    assert response.status_code == 200
    assert "result" in response.json()


class _BrokenRedis:
    """Client Redis qui échoue systématiquement (backend éteint)."""

    def __init__(self):
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


def test_backend_failure_opens_cooldown(monkeypatch):
    """D25 : un échec marque le backend indisponible (plus d'attente par requête)."""
    import app.cache as cache

    broken = _BrokenRedis()
    monkeypatch.setattr(cache, "redis_client", broken)

    assert cache.cache_backend_available() is True
    assert cache.get_cached_result("k1") is None
    assert cache.cache_backend_available() is False

    # Une fois le cooldown ouvert, plus aucune tentative réseau : le compteur
    # de tentatives ne bouge plus, lecture comme écriture.
    attempts_after_failure = broken.attempts
    assert cache.get_cached_result("k2") is None
    cache.store_in_cache("k2", {"validated": True})
    assert broken.attempts == attempts_after_failure
    assert cache.cache_error_count() >= 1


def test_cooldown_expiry_retries_backend(monkeypatch):
    """La fenêtre expirée, on retente le backend (auto-rétablissement)."""
    import app.cache as cache

    broken = _BrokenRedis()
    monkeypatch.setattr(cache, "redis_client", broken)

    cache.get_cached_result("k1")
    assert cache.cache_backend_available() is False

    monkeypatch.setattr(cache, "_down_until", 0.0)
    assert cache.cache_backend_available() is True
    attempts = broken.attempts
    cache.get_cached_result("k2")
    assert broken.attempts == attempts + 1


def test_reset_backend_state_clears_error_counter(monkeypatch):
    import app.cache as cache

    monkeypatch.setattr(cache, "redis_client", _BrokenRedis())
    cache.get_cached_result("k1")
    cache.reset_backend_state()

    assert cache.cache_backend_available() is True
    assert cache.cache_error_count() == 0
