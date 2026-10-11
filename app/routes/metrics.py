"""Métriques d'utilisation.

Non fourni par la spec (l'endpoint GET /metrics est exigé en Phase 2 / couche 1
mais son contenu n'est pas spécifié). Décision D17 : registre de compteurs en
mémoire, thread-safe, sans dépendance externe. Prometheus peut être branché
ensuite sans changer les appels `metrics.incr(...)`.
"""

import threading
import time

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter()
settings = get_settings()

_START_TIME = time.time()


class Metrics:
    """Compteurs atomiques en mémoire."""

    _FIELDS = (
        "requests_total",
        "paid_requests",
        "unpaid_requests",
        "cache_hits",
        "cache_misses",
        "degraded_results",
        "provider_errors",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters = {name: 0 for name in self._FIELDS}

    def incr(self, name: str, value: int = 1) -> None:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = 0
            self._counters[name] += value

    def snapshot(self) -> dict:
        with self._lock:
            data = dict(self._counters)
        hits = data["cache_hits"]
        misses = data["cache_misses"]
        total = hits + misses
        data["cache_hit_ratio"] = round(hits / total, 4) if total else 0.0
        return data


metrics = Metrics()


@router.get("/metrics")
async def get_metrics():
    # État du disjoncteur fournisseur exposé pour la supervision (D24) : sans
    # cela, impossible de distinguer « fournisseur interrogé et en échec » de
    # « disjoncteur ouvert, plus aucun appel réseau » — les deux produisent
    # `degraded=true`. Import local pour ne pas créer de dépendance de module.
    from app.cache import (
        cache_backend_available,
        cache_error_count,
    )
    from app.validation.verifier import breaker

    payload = metrics.snapshot()
    payload["uptime_seconds"] = round(time.time() - _START_TIME, 1)
    payload["price_per_validation_usdc"] = settings.x402_price
    payload["environment"] = settings.environment
    payload["provider_circuit_breaker"] = {
        "state": breaker.state,
        "failures": breaker.failure_count,
        "threshold": breaker.failure_threshold,
    }
    payload["cache_backend_available"] = cache_backend_available()
    payload["cache_errors"] = cache_error_count()
    return payload

