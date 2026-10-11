"""Normalisation des abréviations postales.

Conforme à la spec (Phase 2).
"""

from typing import Dict

ABBREVIATION_MAP = {
    "st": "Street", "ave": "Avenue", "blvd": "Boulevard",
    "dr": "Drive", "ln": "Lane", "ct": "Court", "rd": "Road",
    "pl": "Place", "sq": "Square", "ter": "Terrace",
    "n": "North", "s": "South", "e": "East", "w": "West",
    "ne": "Northeast", "nw": "Northwest", "se": "Southeast", "sw": "Southwest",
    "apt": "Apartment", "ste": "Suite", "fl": "Floor", "bldg": "Building",
    "hwy": "Highway", "pkwy": "Parkway", "cir": "Circle",
    "trl": "Trail", "way": "Way", "plz": "Plaza",
}


def standardize_address(parsed: Dict) -> Dict:
    street_name = parsed.get("street_name", "")

    words = street_name.split()
    standardized_words = []
    for word in words:
        lower = word.lower().rstrip(".")
        if lower in ABBREVIATION_MAP:
            standardized_words.append(ABBREVIATION_MAP[lower])
        else:
            standardized_words.append(word.capitalize())
    street_name = " ".join(standardized_words)

    zip_code = parsed.get("zip", "").replace(" ", "").replace("-", "")
    if parsed.get("country") == "US" and len(zip_code) >= 5:
        zip_code = zip_code[:5]

    return {
        "line1": f"{parsed.get('street_number', '')} {street_name}".strip(),
        "line2": f"Apartment {parsed.get('apartment')}" if parsed.get("apartment") else "",
        "city": parsed.get("city", "").title(),
        "state": parsed.get("state", "").upper(),
        "zip": zip_code,
        "country": parsed.get("country", "US"),
    }
