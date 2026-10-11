"""Attribution d'un score de livrabilité.

Conforme à la spec (Phase 2).

Le code DPV (Delivery Point Validation) provient de SmartyStreets/UPS :
  Y = adresse complète (résidentiel), S = voie reconnue, D = boîte commerciale,
  X = inconnu, N = inexistant.
"""

from typing import Dict


def score_deliverability(verification: Dict, geo: Dict) -> Dict:
    valid = verification.get("valid", False)
    dpv = verification.get("deliverability", "unknown")

    dpv_mapping = {
        "Y": ("residential", 0.99),
        "S": ("residential", 0.95),
        "D": ("commercial", 0.99),
        "X": ("unknown", 0.50),
        "N": ("invalid", 0.00),
    }

    if dpv in dpv_mapping:
        deliverability, confidence = dpv_mapping[dpv]
    else:
        deliverability = "unknown"
        confidence = 0.50

    if not valid:
        deliverability = "invalid"
        confidence = 0.00

    if geo.get("lat") and geo.get("lng"):
        confidence = min(1.0, confidence + 0.05)

    return {
        "deliverability": deliverability,
        "confidence": round(confidence, 2),
    }
