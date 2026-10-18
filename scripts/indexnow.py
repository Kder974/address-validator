#!/usr/bin/env python
"""Signale les pages publiques aux moteurs IndexNow (Bing, Yandex, Seznam, Naver) — D81.

Sans option, le script **affiche** ce qu'il enverrait et vérifie que la clé est bien
servie par le site (sinon IndexNow refuse en 403). Avec `--envoyer`, il poste la
liste à `https://api.indexnow.org/indexnow`, qui la relaie à tous les moteurs du
protocole. À relancer après une mise en ligne qui ajoute ou change des pages.

Usage :

    .venv/bin/python scripts/indexnow.py
    .venv/bin/python scripts/indexnow.py --envoyer

Codes de retour : 0 accepté (200 ou 202), 1 refusé ou clé introuvable, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.seo import indexnow_corps  # noqa: E402

API = "https://api.indexnow.org/indexnow"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--envoyer", action="store_true", help="poster la liste à IndexNow")
    args = parser.parse_args(argv)

    corps = indexnow_corps(get_settings().public_base_url.rstrip("/"))
    print(f"hote : {corps['host']} | cle : {corps['keyLocation']} | {len(corps['urlList'])} pages")
    try:
        servie = httpx.get(corps["keyLocation"], timeout=15, follow_redirects=False)
    except httpx.HTTPError as exc:
        print(f"CLE INTROUVABLE : {exc}")
        return 1
    if servie.status_code != 200 or servie.text.strip() != corps["key"]:
        print(f"CLE INTROUVABLE : {corps['keyLocation']} repond {servie.status_code}")
        return 1
    print("cle servie par le site : OK")
    if not args.envoyer:
        print(json.dumps(corps["urlList"][:6], indent=2), "...")
        print("Pour envoyer : --envoyer")
        return 0

    reponse = httpx.post(API, json=corps, timeout=30, headers={"Content-Type": "application/json; charset=utf-8"})
    print(f"IndexNow : HTTP {reponse.status_code} {reponse.text[:200]}")
    return 0 if reponse.status_code in (200, 202) else 1


if __name__ == "__main__":
    sys.exit(main())
