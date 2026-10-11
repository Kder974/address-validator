"""Pipeline de validation complet.

parse -> standardize -> verify (fournisseur) -> geocode -> score.

Conforme à la spec (Phase 2). `degraded=True` signale que le fournisseur n'a
pas pu être interrogé (panne, timeout, disjoncteur ouvert) : la réponse reste
honnête au lieu de faire croire à une adresse invalide certaine.
"""

from .geocoder import geocode
from .parser import parse_address
from .scorer import score_deliverability
from .standardizer import standardize_address
from .verifier import verify_address

__all__ = [
    "parse_address",
    "standardize_address",
    "verify_address",
    "geocode",
    "score_deliverability",
    "validate_full",
]


def validate_full(raw_address: dict) -> dict:
    parsed = parse_address(raw_address)
    standardized = standardize_address(parsed)
    verification = verify_address(standardized)
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
