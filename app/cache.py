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

D47 — un résultat **dégradé** ne se garde pas 30 jours. `degraded: true` ne dit
pas « adresse invalide », il dit « le fournisseur n'a pas répondu » (D23) : le
conserver `CACHE_TTL` reviendrait à transformer une panne passagère en réponse
fausse d'un mois, pour une adresse que le client paie (ou consomme au quota) à
chaque appel, et sans aucun moyen de forcer une nouvelle vérification. On le
garde donc `CACHE_TTL_DEGRADED` (5 minutes) : assez pour ne pas marteler un
fournisseur en panne — c'est ce que mesurent D23/D25 —, pas assez pour mentir
longtemps. Un résultat normal, lui, reste trente jours : c'est ce qui évite de
payer deux fois la même interrogation fournisseur.
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

CACHE_TTL = 60 * 60 * 24 * 30  # 30 jours — un résultat vérifié, jamais recalculé à l'identique
#: TTL d'un résultat **dégradé** (D47) : « le fournisseur n'a pas répondu » n'est
#: pas un verdict. Cinq minutes suffisent à ne pas marteler un fournisseur en
#: panne ; au-delà, une nouvelle tentative doit pouvoir obtenir une vraie réponse.
CACHE_TTL_DEGRADED = 5 * 60
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
    """Écrit le résultat, avec le TTL qui correspond à ce qu'il affirme (D47).

    Un résultat dégradé ne dit rien de l'adresse : il est gardé
    `CACHE_TTL_DEGRADED` (5 minutes), pas `CACHE_TTL` (30 jours). Le reste du
    contrat ne change pas — un cache injoignable reste un non-événement, compté
    (D12/D25).
    """
    if not _available():
        return
    ttl = CACHE_TTL_DEGRADED if result.get("degraded") else CACHE_TTL
    try:
        redis_client.setex(f"{KEY_PREFIX}{key}", ttl, json.dumps(result))
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

