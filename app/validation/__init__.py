"""Pipeline de validation complet.

parse -> standardize -> verify (fournisseur) -> geocode -> score.

Conforme à la spec (Phase 2). `degraded=True` signale que le fournisseur n'a
pas pu être interrogé (panne, timeout, disjoncteur ouvert) : la réponse reste
honnête au lieu de faire croire à une adresse invalide certaine.

D79 — le fournisseur dépend du pays : SmartyStreets est un service **américain**
et renvoie la saisie sans code DPV pour une adresse étrangère (le pipeline la
déclarait alors « validée ») ; les adresses françaises et d'outre-mer passent
donc par la **Base Adresse Nationale** (`app/validation/ban.py`), jamais par
SmartyStreets, et un échec BAN donne `degraded: true` sans repli.

D84 — une adresse **reconnue** est rendue telle que le fournisseur l'a corrigée
(`corrige` : orthographe, ville, ZIP ou code postal ajoutés), mise en forme par
le même `parse` + `standardize` que la saisie ; une adresse refusée ou un
fournisseur injoignable rendent la saisie mise en forme, comme avant.
"""

from .ban import PAYS_BAN, verify_ban
from .geocoder import geocode
from .parser import parse_address
from .scorer import score_deliverability
from .standardizer import standardize_address
from .verifier import verify_address

__all__ = [
    "PAYS_BAN",
    "parse_address",
    "standardize_address",
    "verify_address",
    "verify_ban",
    "geocode",
    "score_deliverability",
    "validate_full",
]


def validate_full(raw_address: dict) -> dict:
    parsed = parse_address(raw_address)
    standardized = standardize_address(parsed)
    if standardized.get("country") in PAYS_BAN:
        verification = verify_ban(standardized)
    else:
        verification = verify_address(standardized)
    if verification.get("corrige"):
        standardized = standardize_address(parse_address(verification["corrige"]))
    geo = geocode(standardized)
    score = score_deliverability(verification, geo)

    return {
        "validated": verification.get("valid", False),
        "standardized": standardized,
        "latitude": geo.get("lat"),
        "longitude": geo.get("lng"),
        "deliverability": score["deliverability"],
        "confidence": score["confidence"],
        "degraded": "error" in verification,
    }
