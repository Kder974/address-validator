#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verificateur des URL publiques du service (tache 7) : sept portes, un verdict.

QUI   : l'exploitant, juste apres un deploiement, puis apres tout passage de
        `certbot` (qui reecrit le vhost et peut faire disparaitre l'`include` des
        fichiers de decouverte — voir `nginx-discovery.conf`).
QUOI  : interroge les sept URL publiques attendues et rend un verdict : `200`
        partout, ou la liste de ce qui ne repond pas.
COMMENT : GET HTTP sans authentification, avec l'`User-Agent` du verificateur,
        redirections suivies, delai borne par URL. Aucune ecriture, aucun envoi
        de donnee : c'est une lecture.
QUAND : apres `systemctl restart address-validator` et apres tout deploiement.
OU    : scripts/verifier_urls_public.py, lance depuis la racine du depot :
        .venv\\Scripts\\python.exe scripts\\verifier_urls_public.py
        .venv\\Scripts\\python.exe scripts\\verifier_urls_public.py --json
        .venv\\Scripts\\python.exe scripts\\verifier_urls_public.py --base-url https://kder-ai.com
FAIT QUOI EXACTEMENT : affiche une ligne par URL (code, type de contenu, octets,
        duree) puis le verdict `URLS PUBLIQUES OK 7/7`, ou la liste des URL en
        echec avec la raison. `--json` rend le meme contenu en structure.

Les sept URL sont celles que le service **publie** : sante, metriques du
processus, observabilite des appelants (D67), manifeste machine, `llms.txt`,
`AGENTS.md` et `security.txt`. `robots.txt` et `sitemap.xml` n'y sont pas encore :
ils ne sont pas publies (voir `docs/SEO-OPTIMIZATION.md` § 3.1-3.2) et un
verificateur qui attendrait un fichier absent mentirait tous les jours.

Codes de retour : 0 les sept URL repondent `200` ; 1 au moins une ne repond pas ;
2 erreur d'appel.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Sequence

#: Base publique par defaut. Le domaine est celui du vhost ; il peut etre
#: remplace (`--base-url`) pour verifier une pre-production.
BASE_DEFAUT = "https://kder-ai.com"

#: Les sept portes publiees, avec ce qu'elles servent.
URLS = (
    ("/health", "sante du service et de ses dependances"),
    ("/metrics", "compteurs du processus"),
    ("/metrics/agents", "observabilite des appelants (D67)"),
    ("/manifest", "manifeste machine-lisible"),
    ("/llms.txt", "carte de decouverte des LLM et agents"),
    ("/AGENTS.md", "mode d'emploi des agents autonomes"),
    ("/.well-known/security.txt", "canal de signalement (RFC 9116)"),
)

#: Delai par URL (secondes) : une porte qui pend n'est pas une porte qui passe.
DELAI_DEFAUT = 15.0

#: Un User-Agent honnete : le service le classera `outil`.
AGENT = "address-validator-verificateur/1.0 (+https://kder-ai.com/AGENTS.md)"


def _forcer_utf8() -> None:
    """Sortie en UTF-8 : sans cela un accent redirige vers un fichier echoue."""
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")


def interroger(chemin: str, base: str, delai: float = DELAI_DEFAUT) -> dict:
    """Une URL : `(code, type, octets, duree, erreur)`. Ne leve jamais.

    Une panne reseau est un resultat, pas une exception : la porte doit dire
    « cette URL ne repond pas », pas s'arreter sur la premiere.
    """
    url = f"{base.rstrip('/')}{chemin}"
    requete = urllib.request.Request(url, headers={"User-Agent": AGENT})
    debut = time.monotonic()
    try:
        with urllib.request.urlopen(requete, timeout=delai) as reponse:
            corps = reponse.read()
            return {
                "url": url,
                "code": reponse.status,
                "type": reponse.headers.get("Content-Type", ""),
                "octets": len(corps),
                "duree_ms": round((time.monotonic() - debut) * 1000, 1),
                "erreur": None,
            }
    except urllib.error.HTTPError as erreur:
        return {
            "url": url,
            "code": erreur.code,
            "type": erreur.headers.get("Content-Type", "") if erreur.headers else "",
            "octets": 0,
            "duree_ms": round((time.monotonic() - debut) * 1000, 1),
            "erreur": f"HTTP {erreur.code} {erreur.reason}",
        }
    except (urllib.error.URLError, OSError, ValueError) as erreur:
        return {
            "url": url,
            "code": None,
            "type": "",
            "octets": 0,
            "duree_ms": round((time.monotonic() - debut) * 1000, 1),
            "erreur": str(getattr(erreur, "reason", erreur)),
        }


def controler(
    base: str = BASE_DEFAUT, delai: float = DELAI_DEFAUT, urls: Sequence = URLS
) -> tuple[dict, int]:
    """`(rapport, code de retour)`. Le rapport porte toujours les sept entrees."""
    entrees = []
    for chemin, description in urls:
        resultat = interroger(chemin, base, delai)
        resultat["chemin"] = chemin
        resultat["description"] = description
        resultat["ok"] = resultat["code"] == 200
        entrees.append(resultat)

    reussis = sum(1 for entree in entrees if entree["ok"])
    verdict = "OK" if reussis == len(entrees) else "KO"
    rapport = {
        "horodatage": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base": base.rstrip("/"),
        "total": len(entrees),
        "reussis": reussis,
        "verdict": f"URLS PUBLIQUES {verdict} {reussis}/{len(entrees)}",
        "urls": entrees,
    }
    return rapport, 0 if reussis == len(entrees) else 1


def _afficher(rapport: dict) -> None:
    """Une ligne par URL, puis le verdict."""
    print(f"=== URL publiques - {rapport['horodatage']} - {rapport['base']} ===")
    for entree in rapport["urls"]:
        if entree["ok"]:
            type_ = entree["type"].split(";")[0]
            detail = (
                f"{entree['code']} {type_:<26} {entree['octets']:>7} octet(s) "
                f"{entree['duree_ms']:>7.1f} ms"
            )
        else:
            detail = entree["erreur"] or "pas de reponse"
        print(f"  {'OK   ' if entree['ok'] else 'ECHEC'} {entree['chemin']:<26} {detail}")
        if not entree["ok"]:
            print(f"        attendu : {entree['description']}")
    print(rapport["verdict"])


def parseur() -> argparse.ArgumentParser:
    """Options de la ligne de commande."""
    analyseur = argparse.ArgumentParser(
        prog="verifier_urls_public.py",
        description="Verifie que les sept URL publiques du service repondent 200.",
    )
    analyseur.add_argument(
        "--base-url",
        default=BASE_DEFAUT,
        help=f"base publique a interroger (defaut : {BASE_DEFAUT})",
    )
    analyseur.add_argument(
        "--delai",
        type=float,
        default=DELAI_DEFAUT,
        help=f"delai par URL en secondes (defaut : {DELAI_DEFAUT:g})",
    )
    analyseur.add_argument("--json", action="store_true", help="sortie JSON")
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entree : un rapport, un code de retour."""
    options = parseur().parse_args(argv)
    _forcer_utf8()
    rapport, code = controler(options.base_url, options.delai)
    if options.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _afficher(rapport)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
