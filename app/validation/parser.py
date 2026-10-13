"""Découpage d'une adresse brute en composants.

Conforme à la spec (Phase 2).
"""

import re
from typing import Dict


def parse_address(raw: Dict) -> Dict:
    street = raw.get("street", "").strip()

    number_match = re.match(r"^(\d+[\w-]*)", street)
    street_number = number_match.group(1) if number_match else ""
    street_name = street[len(street_number):].strip() if street_number else street

    country = raw.get("country", "US").strip().upper()

    # Correctif D51 : l'extraction d'unité est une convention USPS, donc US seulement,
    # et les mots-clés ne comptent qu'entiers (\b) : "Stewart" ne contient plus "ste".
    apartment = ""
    if country == "US":
        unite = r"(?:\b(?:apt|apartment|ste|suite|unit|fl|floor)\b|#)\s*([\w-]+)"
        apt_match = re.search(unite, street, re.IGNORECASE)
        apartment = apt_match.group(1) if apt_match else ""
        if apartment:
            street_name = re.sub(unite, "", street_name, flags=re.IGNORECASE)

    street_name = " ".join(street_name.split())

    return {
        "street_number": street_number,
        "street_name": street_name.strip(),
        "apartment": apartment,
        "city": raw.get("city", "").strip(),
        "state": raw.get("state", "").strip().upper(),
        "zip": raw.get("zip", "").strip(),
        "country": country,
    }
