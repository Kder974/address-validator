"""Géocodage (résolution des coordonnées GPS) via Nominatim / OpenStreetMap.

Conforme à la spec (Phase 2). Ajout (D10) : en-tête `User-Agent` explicite
avec adresse de contact, exigé par la politique d'usage de Nominatim. L'adresse
provient du réglage `contact_email` (variable `CONTACT_EMAIL`), plus d'un
placeholder en dur.

D26 — timeout borné (mesuré : 0,83 s à 1,60 s en pratique) : le `timeout=10.0`
du squelette laissait un Nominatim qui *pend* immobiliser une requête payante
10 s pour un résultat de toute façon facultatif (l'échec retombe sur
`{lat: None, lng: None}`). Aligné sur le budget du fournisseur (D23).
"""

from typing import Dict

import httpx

from app.config import get_settings
from app.routes.metrics import metrics

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"AddressValidator/1.0 ({get_settings().contact_email})"
GEOCODER_TIMEOUT = httpx.Timeout(connect=2.0, read=3.0, write=3.0, pool=2.0)


def geocode(standardized: Dict) -> Dict:
    address_str = (
        f"{standardized.get('line1', '')}, {standardized.get('city', '')}, "
        f"{standardized.get('state', '')} {standardized.get('zip', '')}, "
        f"{standardized.get('country', '')}"
    )

    params = {
        "q": address_str,
        "format": "json",
        "limit": 1,
    }
    headers = {
        "User-Agent": USER_AGENT,
    }

    try:
        with httpx.Client(timeout=GEOCODER_TIMEOUT) as client:
            response = client.get(NOMINATIM_URL, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()

            if data and len(data) > 0:
                return {
                    "lat": float(data[0]["lat"]),
                    "lng": float(data[0]["lon"]),
                }
    except httpx.HTTPStatusError as exc:
        # D109 : 429 ou 403, Nominatim nous limite ou nous bloque. Les coordonnees
        # manquent alors dans les reponses ; la veille du matin le signale.
        metrics.incr("geocode_errors")
        if exc.response.status_code in (403, 429):
            metrics.incr("geocode_blocked")
    except Exception:
        metrics.incr("geocode_errors")

    return {"lat": None, "lng": None}
