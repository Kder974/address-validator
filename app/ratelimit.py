"""Limitation de débit par IP (décision D44) — Redis, avec repli local **assumé**.

Deux usages : l'inscription (`POST /signup`, 5 tentatives par IP et par heure,
exigence de la spec) et le formulaire public de validation
(`POST /validate-form`, 3 essais par IP et par 24 h, élément 3).

Le compteur vit dans Redis quand il est joignable : le TTL y est natif et le
compteur est partagé entre processus. Quand Redis ne répond pas, on **ne refuse
pas tout** : on compte dans le processus. Ce repli est écrit, pas subi, et il a
un prix qu'il faut dire : il protège d'un abus simple depuis une IP, pas d'un
abus distribué, et chaque processus a son propre compteur. Un plafond annoncé
mais absent serait pire qu'un garde-fou imparfait — et un site qui refuse toute
inscription parce que Redis est tombé serait une panne déguisée en sécurité.

La fenêtre Redis est **fixe** (elle démarre à la première requête et expire
`window_seconds` plus tard) : le compteur ne peut donc pas grandir sans fin, et
le comportement est celui qu'on peut expliquer au client (`Retry-After`).
"""

from __future__ import annotations

import threading
import time

from app.cache import redis_client

#: Fenêtres locales (repli) : clé → horodatages des requêtes conservées.
_fenetres: dict[str, list[float]] = {}
_lock = threading.Lock()
_fallback_total = 0
#: Cooldown après un échec Redis (même leçon que D25 pour le cache) : une panne
#: ne doit pas coûter `socket_connect_timeout` à **chaque** requête. `localhost`
#: résout deux adresses (::1 puis 127.0.0.1), une tentative ratée paie donc
#: jusqu'à 2 s ; sans cooldown, six inscriptions ratées coûteraient 12 s de
#: latence pour rien.
_down_until = 0.0
REDIS_RETRY_COOLDOWN = 30.0


def _redis_disponible() -> bool:
    return time.time() >= _down_until


def _local_allow(key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    """Compteur en mémoire, borné à la fenêtre demandée (repli sans Redis)."""
    maintenant = time.time()
    with _lock:
        marques = [t for t in _fenetres.get(key, []) if maintenant - t < window_seconds]
        if len(marques) >= max_requests:
            _fenetres[key] = marques
            return False, 0
        marques.append(maintenant)
        _fenetres[key] = marques
        return True, max_requests - len(marques)


def allow(key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
    """`(autorisé, essais restants)` pour `key` dans la fenêtre fixe annoncée.

    Une requête **refusée n'est pas comptée** : la fenêtre expire naturellement,
    donc un client bloqué n'est pas bloqué indéfiniment par ses propres refus.
    """
    global _fallback_total, _down_until
    if not _redis_disponible():
        with _lock:
            _fallback_total += 1
        return _local_allow(key, max_requests, window_seconds)

    prefixe = f"rl:{key}"
    try:
        compte = redis_client.incr(prefixe)
        if compte == 1:
            redis_client.expire(prefixe, window_seconds)
    except Exception:
        # Redis injoignable : on ouvre le cooldown, on repasse au compteur local,
        # et on compte le repli pour qu'il soit visible dans /metrics au lieu
        # d'etre invisible.
        with _lock:
            _down_until = time.time() + REDIS_RETRY_COOLDOWN
            _fallback_total += 1
        return _local_allow(key, max_requests, window_seconds)

    if compte > max_requests:
        return False, 0
    return True, max_requests - compte


def blocked(key: str, max_requests: int, window_seconds: int) -> bool:
    """`True` si `key` a déjà atteint `max_requests` dans la fenêtre, **sans compter**.

    Sert aux plafonds qui ne comptent que les échecs (connexion, D62) : on regarde
    avant de vérifier le mot de passe, et seul un refus appelle `allow`.
    """
    if _redis_disponible():
        try:
            valeur = redis_client.get(f"rl:{key}")
            return int(valeur or 0) >= max_requests
        except Exception:
            pass
    maintenant = time.time()
    with _lock:
        return len([t for t in _fenetres.get(key, []) if maintenant - t < window_seconds]) >= max_requests


def fallback_count() -> int:
    """Nombre de décisions prises par le repli local (Redis injoignable)."""
    with _lock:
        return _fallback_total


def retry_after(key: str, window_seconds: int) -> int:
    """Secondes restantes dans la fenêtre fixe en cours (élément 3).

    Un `429` qui annonce « réessayez plus tard » sans dire quand est un mur : le
    client ne peut pas savoir s'il doit attendre une minute ou une journée. Redis
    connaît le TTL exact de la fenêtre ; le repli local, lui, garde l'horodatage
    de la **première** requête de la fenêtre, donc l'instant où elle se referme.

    Si rien n'est mesurable (fenêtre expirée, compteur absent, Redis en panne), on
    annonce le maximum — `window_seconds`—, jamais un chiffre plus optimiste que
    la réalité : promettre « 5 minutes » et refuser encore serait pire que
    d'annoncer la borne haute.
    """
    if _redis_disponible():
        try:
            ttl = int(redis_client.ttl(f"rl:{key}"))
        except Exception:
            ttl = -1
        if ttl > 0:
            return min(ttl, window_seconds)
    with _lock:
        marques = _fenetres.get(key, [])
        if marques:
            reste = window_seconds - (time.time() - marques[0])
            if reste > 0:
                # Arrondi au-dessus (le client ne doit pas revenir une seconde trop
                # tôt) et **borné** par la fenêtre : annoncer plus que la durée de
                # la fenêtre serait une promesse que le compteur ne tient pas.
                return min(window_seconds, int(reste) + 1)
    return window_seconds


def reset() -> None:
    """Vide les fenêtres locales et le compteur de repli (tests, supervision)."""
    global _fallback_total, _down_until
    with _lock:
        _fenetres.clear()
        _fallback_total = 0
        _down_until = 0.0
