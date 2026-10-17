"""Métriques d'utilisation.

Non fourni par la spec (l'endpoint GET /metrics est exigé en Phase 2 / couche 1
mais son contenu n'est pas spécifié). Décision D17 : registre de compteurs en
mémoire, thread-safe, sans dépendance externe. Prometheus peut être branché
ensuite sans changer les appels `metrics.incr(...)`.

D41 (durcissement W2) ajoute `ledger_write_failures_total` : un paiement dont
l'inscription au registre échoue n'est plus servi (500 côté `/validate`), donc il
faut pouvoir le compter — un incident invisible serait indiscernable d'une
absence de trafic.

D45 (abonnement par clé API) ajoute les compteurs du mode clé. Sans eux, on ne
peut pas distinguer « personne n'utilise les clés » de « les clés sont
refusées » : les deux se ressemblent, et la seconde demande une action.

D46 (essai gratuit public, `/validate-form`) ajoute `validate_form_requests`,
`validate_form_validations` et `validate_form_denied` : un essai refusé par le
plafond et un essai jamais tenté produisent tous deux « zéro validation » — seul
le refus compté dit la différence.
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
        # D41 : un encaissement qui n'a pas pu entrer au registre. Le compter est
        # la seule facon de distinguer « personne ne paie » de « on n'arrive plus
        # a inscrire ceux qui paient ».
        "ledger_write_failures_total",
        # D45 : le mode cle API. `api_key_requests` sont les appels qui ont
        # presente une cle ; `api_key_validations` ceux qui ont ete servis ;
        # `api_key_denied` les refus (cle inconnue, essai fini, quota du mois
        # atteint) ; `api_key_unavailable` les appels refuses faute de base des
        # comptes joignable — un 503 qui n'est ni la faute du client ni un refus
        # de paiement.
        "api_key_requests",
        "api_key_validations",
        "api_key_denied",
        "api_key_unavailable",
        # D46 : l'essai gratuit public. `validate_form_requests` compte les essais
        # soumis, `validate_form_validations` ceux qui ont abouti a une reponse du
        # pipeline, `validate_form_denied` les refus de plafond. Sans ces trois
        # compteurs, « personne n'essaie » et « tout le monde est bloque »
        # produiraient le meme chiffre : zero.
        "validate_form_requests",
        "validate_form_validations",
        "validate_form_denied",
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


@router.get("/metrics/agents")
async def get_agent_metrics():
    """Agents IA : qui vient, par quelle porte, et ce que cela convertit (D72).

    Meme rapport que `scripts/analyser_agents.py` (les cinq mesures `agents`,
    `top_ip`, `top_agents`, `top_pages`, `conversion`), mais la source est la
    memoire du processus et non le journal sur disque : l'endpoint dit « depuis
    le demarrage », le script dit « ce qui est ecrit ». Les deux se completent,
    aucun des deux ne remplace l'autre.

    Aucune adresse en clair n'est publiee : l'IP est pseudonymisee a l'entree
    (D30, sha256 + sel) et c'est le pseudonyme qui sort ici. Une adresse
    pseudonymisee reste une donnee d'exploitation : elle compte, elle ne
    designe personne sans le sel, qui n'est pas publie.
    """
    from app import observability

    return observability.agents_snapshot()

