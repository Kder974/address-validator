"""Cache Redis des résultats récents.

Conforme à la spec (Phase 4). Ajouts documentés (D12) : timeouts de connexion
courts pour que l'absence de Redis dégrade la latence sans bloquer l'API, et
compteur d'erreurs exposé pour la supervision.

Le cache est la brique qui permet le critère de succès n°3 : plus de 80 % des
requêtes répétées n'atteignent jamais SmartyStreets.

D25 — cooldown après échec (mesuré, voir TEST_REPORT.md) : `socket_connect_timeout=1`
est appliqué *par adresse*, et `localhost` en résout deux (::1 puis 127.0.0.1).
Redis éteint, chaque appel payait donc ~2 s sur la lecture **et** ~2 s sur
l'écriture, soit ~4 s par requête, indéfiniment — au point que le disjoncteur
fournisseur ouvert (0 s) ne se voyait plus dans la latence. Après un échec, on
marque le backend indisponible pendant `REDIS_RETRY_COOLDOWN` secondes : une
panne Redis coûte une fois, pas à chaque requête. `check_redis()` reste une
sonde réelle (le rôle de /health est de dire la vérité).
"""

import json
import threading
import time

import redis

from app.config import get_settings

settings = get_settings()

redis_client = redis.from_url(
    settings.redis_url,
    socket_connect_timeout=1,
    socket_timeout=1,
    decode_responses=False,
)

CACHE_TTL = 60 * 60 * 24 * 30  # 30 jours
KEY_PREFIX = "addr:"
REDIS_RETRY_COOLDOWN = 30.0  # secondes

_lock = threading.Lock()
_down_until = 0.0
_error_count = 0


def _available() -> bool:
    with _lock:
        return time.time() >= _down_until


def _mark_down() -> None:
    global _down_until, _error_count
    with _lock:
        _down_until = time.time() + REDIS_RETRY_COOLDOWN
        _error_count += 1


def _mark_up() -> None:
    global _down_until
    with _lock:
        _down_until = 0.0


def cache_backend_available() -> bool:
    """False si le dernier échec a ouvert la fenêtre de cooldown."""
    return _available()


def cache_error_count() -> int:
    with _lock:
        return _error_count


def reset_backend_state() -> None:
    """Réarme l'état local du cache (tests / supervision)."""
    global _down_until, _error_count
    with _lock:
        _down_until = 0.0
        _error_count = 0


def get_cached_result(key: str):
    if not _available():
        return None
    try:
        data = redis_client.get(f"{KEY_PREFIX}{key}")
        if data:
            return json.loads(data)
    except Exception:
        _mark_down()
    return None


def store_in_cache(key: str, result: dict) -> None:
    if not _available():
        return
    try:
        redis_client.setex(f"{KEY_PREFIX}{key}", CACHE_TTL, json.dumps(result))
    except Exception:
        _mark_down()


def check_redis() -> str:
    """Sonde réelle (jamais court-circuitée par le cooldown)."""
    try:
        redis_client.ping()
        _mark_up()
        return "ok"
    except Exception as e:
        _mark_down()
        return f"error: {e}"

