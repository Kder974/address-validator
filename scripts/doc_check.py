#!/usr/bin/env python
"""Contrôle mécanique de structure des documents exigés (README, PRIVACY, SECURITY).

Les portes de documentation étaient « manuelles » : il fallait compter des
sections à l'œil, ce qui n'est pas une preuve. Ce script compte les sections
numérotées de niveau 2 (`## 1.`, `## 2.`…), le format imposé par la spec à
README.md (10 sections), PRIVACY.md (6 points) et SECURITY.md (6 points).

Le compte doit être **exactement** l'attendu, et la numérotation doit être
séquentielle (1..N, sans trou ni doublon) : un document qui perd une section, ou
qui en ajoute une septième, fait échouer la porte au lieu de passer inaperçu.

    .venv\\Scripts\\python.exe scripts\\doc_check.py README.md 10
    -> "DOC OK README.md sections=10 chars=7421"    (code 0)
    -> "DOC FAIL README.md sections=9 expected=10 numbering=[1..9]"  (code 1)

Codes de retour : 0 vert, 1 porte rouge, 2 usage.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTION = re.compile(r"^##\s+(\d+)\.\s+\S", re.MULTILINE)

#: Garde-fou anti-coquille vide : une section doit porter du contenu réel.
MIN_CHARS_PER_SECTION = 200


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: doc_check.py <fichier.md> <nombre_de_sections>")
        return 2

    rel, expected_raw = argv
    try:
        expected = int(expected_raw)
    except ValueError:
        print(f"DOC FAIL {rel} expected=<entier>")
        return 2

    path = Path(rel)
    if not path.is_absolute():
        path = ROOT / rel

    if not path.is_file():
        print(f"DOC FAIL {rel} missing")
        return 1

    text = path.read_text(encoding="utf-8")
    numbers = [int(value) for value in SECTION.findall(text)]
    sections = len(numbers)

    if sections != expected or numbers != list(range(1, sections + 1)):
        print(f"DOC FAIL {rel} sections={sections} expected={expected} numbering={numbers}")
        return 1

    if len(text) < expected * MIN_CHARS_PER_SECTION:
        print(f"DOC FAIL {rel} sections={sections} chars={len(text)} contenu trop court")
        return 1

    print(f"DOC OK {rel} sections={sections} chars={len(text)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
