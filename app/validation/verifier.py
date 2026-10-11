"""Vérification de l'adresse auprès du fournisseur externe (SmartyStreets).

Conforme à la spec (Phase 2). Ajouts documentés (D9) :
  * verrou (Lock) autour de l'état du CircuitBreaker -> sûr en concurrence
    (uvicorn sert plusieurs requêtes en parallèle) ;
  * méthode `reset()` pour les tests et la supervision.

Le CircuitBreaker évite de marteler un fournisseur en panne et transforme un
timeout réseau en réponse rapide et dégradée (critère de succès n°4).

D23 — budget d'appel fournisseur borné (mesuré, voir TEST_REPORT.md) : le
`httpx.Client(timeout=10.0)` du squelette, combiné au `@retry` 3 tentatives,
produisait une réponse dégradée à **15,2 s** (constaté en recette réelle) tout
en devant échouer 5 fois avant l'ouverture du disjoncteur, soit 75 s de latence
dégradée. C'est incompatible avec une API payante synchrone. Correction :
  * timeout *granulaire* (connexion 2 s, lecture 4 s) au lieu d'un 10 s global ;
  * seuil d'ouverture du disjoncteur abaissé à 3 (les valeurs par défaut de la
    classe `CircuitBreaker`, 5 et 60 s, restent celles de la spec) ;
  * politique de retry (3 tentatives, back-off exponentiel) conservée telle
    quelle.
Un fournisseur qui *refuse* la connexion échoue donc en ~3 s (back-off seul),
un fournisseur qui *pend* est coupé à 12 s, et une fois le disjoncteur ouvert
la réponse est immédiate (échec local, sans réseau).
"""

import threading
import time
from typing import Dict

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings

settings = get_settings()

SMARTY_URL = "https://us-street.api.smarty.com/street-address"

# Timeout granulaire (D23) : ne jamais attendre 10 s sur une connexion morte.
PROVIDER_TIMEOUT = httpx.Timeout(connect=2.0, read=4.0, write=4.0, pool=2.0)



class CircuitBreaker:
    """Disjoncteur simple : closed -> open -> half-open -> closed."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"
        self._lock = threading.Lock()

    def call(self, func, *args, **kwargs):
        with self._lock:
            if self.state == "open":
                # `>=` (et non `>`, comme le squelette) : sur les horloges à
                # résolution grossière (Windows ~15 ms) un délai écoulé peut
                # valoir exactement 0.0 et bloquer indéfiniment la sonde
                # half-open. Voir DECISIONS.md (D19).
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    self.state = "half-open"
                else:
                    raise Exception("Circuit breaker is OPEN")

        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            with self._lock:
                self.failure_count += 1
                self.last_failure_time = time.time()
                if self.failure_count >= self.failure_threshold:
                    self.state = "open"
            raise exc

        with self._lock:
            if self.state == "half-open":
                self.state = "closed"
                self.failure_count = 0
        return result

    def reset(self) -> None:
        with self._lock:
            self.failure_count = 0
            self.last_failure_time = None
            self.state = "closed"


# Instance applicative : seuil à 3 échecs (D23) pour que le fast-fail arrive
# après 3 requêtes dégradées au lieu de 5. La classe garde les valeurs de la
# spec (failure_threshold=5, recovery_timeout=60).
breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
def _call_smarty(street: str, city: str, state: str, zip_code: str) -> Dict:
    url = SMARTY_URL
    params = {
        "auth-id": settings.smarty_auth_id,
        "auth-token": settings.smarty_auth_token,
        "street": street,
        "city": city,
        "state": state,
        "zipcode": zip_code,
        "match": "invalid",
    }

    with httpx.Client(timeout=PROVIDER_TIMEOUT) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def verify_address(standardized: Dict) -> Dict:
    try:
        results = breaker.call(
            _call_smarty,
            standardized.get("line1", ""),
            standardized.get("city", ""),
            standardized.get("state", ""),
            standardized.get("zip", ""),
        )

        if results and len(results) > 0:
            result = results[0]
            return {
                "valid": True,
                "deliverability": result.get("analysis", {}).get("dpv_match_code", "unknown"),
                "metadata": result.get("metadata", {}),
                "raw": result,
            }
        else:
            return {
                "valid": False,
                "deliverability": "invalid",
                "metadata": {},
                "raw": None,
            }
    except Exception as e:
        return {
            "valid": False,
            "deliverability": "unknown",
            "metadata": {},
            "raw": None,
            "error": str(e),
        }
