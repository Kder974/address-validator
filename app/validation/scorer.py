"""Attribution d'un score de livrabilité.

Conforme à la spec (Phase 2).

Le code DPV (Delivery Point Validation) provient de SmartyStreets/UPS :
  Y = adresse complète (résidentiel), S = voie reconnue, D = boîte commerciale,
  X = inconnu, N = inexistant.

D79 — la BAN (adresses françaises, `app/validation/ban.py`) ne rend pas de code
DPV mais un score et un type : `housenumber` (point de livraison complet) et
`street` (voie reconnue) sont les deux niveaux équivalents à `Y` et `S`. Un
résultat BAN non valide (`valid: false`) vaut `invalid`, comme un DPV `N`.
"""

from typing import Dict

#: Score BAN -> (livrabilité, confiance) : les deux niveaux de la BAN.
BAN_MAPPING = {
    "housenumber": ("residential", 0.99),
    "street": ("residential", 0.95),
}


def score_deliverability(verification: Dict, geo: Dict) -> Dict:
    valid = verification.get("valid", False)

    if verification.get("source") == "ban":
        # BAN : le type dit le niveau de précision, pas la nature du lieu.
        if valid:
            deliverability, confidence = BAN_MAPPING.get(
                str(verification.get("type") or ""), ("unknown", 0.50)
            )
        else:
            deliverability, confidence = "invalid", 0.00
    else:
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
