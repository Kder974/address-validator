#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Rapport de visites : sept sections pour lire le trafic reel du service (D68).

QUI   : l'exploitant, a la main ou depuis le rapport de supervision.
QUOI  : agrege le journal d'acces Nginx en **sept sections** — chiffres cles,
        repartition dans le temps, top pages, top IP, codes de statut, clients,
        anomalies (sondes de scanners, chemins sensibles, fuites).
COMMENT : lit le journal d'acces Nginx (`combined`) avec
        `observability.lire_access_log`, donc avec la **meme** table de detection
        des appelants que `analyser_agents.py` et `GET /metrics/agents`. Les
        adresses sont pseudonymisees (SHA-256 + sel, D30) ; `--ip-en-clair` est
        le seul moyen de les lire en clair, et il ne s'utilise qu'a la demande.
QUAND : chaque semaine, ou juste apres une campagne de decouverte ; `--since 24h`
        pour relire un incident de la journee.
OU    : scripts/analyser_visites.py, lance depuis la racine du depot :
        .venv\\Scripts\\python.exe scripts\\analyser_visites.py
        .venv\\Scripts\\python.exe scripts\\analyser_visites.py --since 7d
        .venv\\Scripts\\python.exe scripts\\analyser_visites.py --since 24h --json
FAIT QUOI EXACTEMENT : affiche les sept sections en clair (une ligne par fait),
        ou les sept sections **structurees** en JSON (`--json`) pour un script.
        Le service ne journalise pas le trafic humain dans son propre journal :
        seul Nginx voit passer tout le monde. C'est pourquoi ce rapport lit Nginx
        et non `DISCOVERY_LOG_PATH` — melanger les deux sources compterait deux
        fois chaque requete d'agent IA, qui figure dans les deux journaux.

Codes de retour : 0 rapport produit — un journal sans ligne dans la fenetre est
une mesure (personne n'est venu), pas une erreur ; 2 un journal **nomme
explicitement** par l'operateur est illisible.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import observability  # noqa: E402

#: Journal d'acces Nginx (format `combined`, celui de `nginx.conf`). Absent d'un
#: poste de developpement : ce n'est pas une erreur, c'est une source en moins —
#: le rapport le dit au lieu de se taire.
ACCES_DEFAUT = "/var/log/nginx/access.log"

#: Fenetre par defaut : la retention du journal (30 jours) est la memoire
#: maximale du systeme — interroger plus large ne lirait rien de plus.
SINCE_DEFAUT = "30d"


def _forcer_utf8() -> None:
    """Sortie en UTF-8 : sans cela un accent redirige vers un fichier echoue."""
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")


def parseur() -> argparse.ArgumentParser:
    """Options de la ligne de commande."""
    analyseur = argparse.ArgumentParser(
        prog="analyser_visites.py",
        description=(
            "Le trafic reel du service : volume, chemins, adresses, statuts, "
            "clients — et ce qui sort de l'ordinaire (sondes, chemins sensibles)."
        ),
        epilog=(
            "Codes de retour : 0 rapport produit (meme vide), 2 journal nomme "
            "explicitement illisible."
        ),
    )
    analyseur.add_argument(
        "--acces",
        default=ACCES_DEFAUT,
        metavar="FICHIER",
        help=f"journal d'acces Nginx a lire (defaut : {ACCES_DEFAUT})",
    )
    analyseur.add_argument(
        "--since",
        default=SINCE_DEFAUT,
        metavar="DUREE",
        help=(
            "fenetre de lecture : `30d`, `7d`, `24h`, `90m` ou un instant ISO "
            f"(defaut : {SINCE_DEFAUT})"
        ),
    )
    analyseur.add_argument(
        "--limite",
        type=int,
        default=10,
        metavar="N",
        help="longueur des tops pages / adresses (defaut : 10)",
    )
    analyseur.add_argument(
        "--ip-en-clair",
        action="store_true",
        help=(
            "affiche les adresses en clair au lieu du pseudonyme SHA-256 + sel "
            "(D30) : a n'utiliser que sur demande explicite"
        ),
    )
    analyseur.add_argument(
        "--json",
        action="store_true",
        help="sortie structuree (les sept sections avec leurs donnees) au lieu du texte",
    )
    return analyseur


def _lire_acces(chemin: str, depuis_le: datetime | None) -> tuple[list[dict], dict]:
    """Mesures du journal d'acces, avec l'etat de la source.

    L'etat accompagne toujours les mesures : un rapport qui ne dit pas ce qu'il a
    lu ne se verifie pas. `lu` faux signifie « rien a lire » (journal absent), ce
    qui n'est une erreur que si l'operateur a **nomme** le chemin lui-meme.
    """
    fichier = Path(chemin)
    if not fichier.is_file():
        return [], {"chemin": chemin, "lu": False, "raison": "fichier absent"}
    mesures = list(observability.lire_access_log(fichier, depuis_le))
    return mesures, {
        "chemin": chemin,
        "lu": True,
        "octets": fichier.stat().st_size,
        "mesures": len(mesures),
    }


def rapport(options: argparse.Namespace) -> tuple[dict, int]:
    """Les sept sections, et le code de retour qui va avec.

    Une source absente et une source illisible ne sont pas la meme chose : le
    chemin par defaut peut manquer (poste sans Nginx) sans etre une erreur, alors
    qu'un chemin **nomme par l'operateur** qui ne repond pas est une erreur
    d'appel — elle sort en code 2, pas en silence.
    """
    depuis_le = observability.depuis(options.since)
    mesures, etat = _lire_acces(options.acces, depuis_le)
    visites = observability.visites_report(
        mesures, limite=options.limite, ip_en_clair=options.ip_en_clair
    )
    sortie = {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since": options.since,
        "depuis": depuis_le.isoformat(timespec="seconds") if depuis_le else None,
        "source": etat,
        **visites,
    }
    code = 2 if options.acces != ACCES_DEFAUT and not etat["lu"] else 0
    return sortie, code


def _afficher(sortie: dict) -> None:
    """Les sept sections, dans l'ordre : le chiffre, puis la ligne qui le lit."""
    print(
        f"=== Visites - {sortie['genere_le']} - depuis {sortie['since']} "
        f"({sortie['depuis']}) ==="
    )
    etat = sortie["source"]
    detail = etat.get("raison") or ", ".join(
        f"{cle}={valeur}" for cle, valeur in etat.items() if cle != "chemin"
    )
    print(f"source : {etat['chemin'] or '-'} -> {detail}")

    for rang, section in enumerate(sortie["sections"], start=1):
        print(f"\n{rang}. {section['titre']}")
        for ligne in section["lignes"]:
            print(f"   {ligne}")


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entree : 0 rapport produit, 2 journal nomme illisible."""
    _forcer_utf8()
    options = parseur().parse_args(argv)
    sortie, code = rapport(options)
    if options.json:
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    else:
        _afficher(sortie)
    return code


if __name__ == "__main__":
    sys.exit(main())
