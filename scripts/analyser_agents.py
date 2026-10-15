#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Rapport agents IA : qui vient, par quelle porte, et ce que cela convertit (D67).

QUI   : l'exploitant, a la main ou depuis un timer de supervision.
QUOI  : lit les sources d'appelants disponibles et rend les **cinq mesures**
        demandees — `agents` (par classe), `top_ip`, `top_agents`, `top_pages`
        et `conversion`.
COMMENT : agrege le journal JSONL de D30 (`DISCOVERY_LOG_PATH`, ecrit par
        `app/observability.py`) et, s'il est lisible, le journal d'acces Nginx
        au format `combined` — c'est lui qui porte le User-Agent de tout le
        trafic, humains compris. Le classement vit dans `app/observability.py` :
        une seule table, donc une seule verite entre l'endpoint
        `GET /metrics/agents` et ce script.
QUAND : apres une campagne de decouverte, ou chaque jour avec le rapport.
OU    : scripts/analyser_agents.py, lance depuis la racine du depot :
        .venv\\Scripts\\python.exe scripts\\analyser_agents.py
        .venv\\Scripts\\python.exe scripts\\analyser_agents.py --since 7d --json
FAIT QUOI EXACTEMENT : affiche cinq sections lisibles, ou les cinq mesures en
        JSON (`--json`). La conversion est mesuree sur les agents IA : un `200`
        sur `/validate` signifie que le paiement a ete accepte, un `402` qu'il
        ne l'a pas ete — `/validate` est la seule porte payante du service, donc
        la seule conversion qui existe.

Codes de retour : 0 rapport produit — un rapport vide est une mesure, pas une
erreur ; 2 une source **nommee explicitement** est illisible.
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
from app.config import get_settings  # noqa: E402

#: Journal d'acces Nginx (format `combined`, celui de `nginx.conf`). Chemin
#: absent d'une machine de developpement : ce n'est pas une erreur, c'est une
#: source en moins — le rapport le dit au lieu de se taire.
ACCES_DEFAUT = "/var/log/nginx/access.log"

#: Fenetre par defaut : la retention du journal (30 jours) n'est pas un hasard,
#: c'est la memoire maximale du systeme — interroger plus large ne lirait rien.
SINCE_DEFAUT = "30d"


def _forcer_utf8() -> None:
    """Sortie en UTF-8 : sans cela un accent redirige vers un fichier echoue."""
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")


def parseur() -> argparse.ArgumentParser:
    """Options de la ligne de commande."""
    analyseur = argparse.ArgumentParser(
        prog="analyser_agents.py",
        description=(
            "Qui vient sur le service : agents IA, annuaires MCP, moteurs, "
            "outils, navigateurs — et ce que les agents IA convertissent."
        ),
        epilog=(
            "Codes de retour : 0 rapport produit (meme vide), 2 source nommee "
            "explicitement illisible."
        ),
    )
    analyseur.add_argument(
        "--journal",
        default=None,
        metavar="FICHIER",
        help=(
            "journal JSONL de decouverte (defaut : DISCOVERY_LOG_PATH de la "
            "configuration)"
        ),
    )
    analyseur.add_argument(
        "--acces",
        default=ACCES_DEFAUT,
        metavar="FICHIER",
        help=f"journal d'acces Nginx (defaut : {ACCES_DEFAUT})",
    )
    analyseur.add_argument(
        "--since",
        default=SINCE_DEFAUT,
        metavar="DUREE|DATE",
        help=(
            "fenetre lue : duree (30m, 24h, 7d) ou date ISO 8601 "
            f"(defaut : {SINCE_DEFAUT})"
        ),
    )
    analyseur.add_argument(
        "--limite",
        type=int,
        default=10,
        metavar="N",
        help="taille des tops (defaut : 10)",
    )
    analyseur.add_argument(
        "--json",
        action="store_true",
        help="sortie JSON, pour un script ou une trace",
    )
    return analyseur


def _lire_journal(chemin: str) -> tuple[list, dict]:
    """Lit le journal de decouverte et decrit ce qui a ete lu."""
    if not chemin:
        return [], {
            "chemin": None,
            "lu": False,
            "raison": "aucun journal configure (DISCOVERY_LOG_PATH vide)",
        }
    fichier = Path(chemin)
    if not fichier.is_file():
        return [], {"chemin": chemin, "lu": False, "raison": "fichier absent"}
    evenements = list(observability.read_events(fichier))
    return evenements, {"chemin": chemin, "lu": True, "entrees": len(evenements)}


def _lire_acces(chemin: str, depuis_le) -> tuple[list, dict]:
    """Lit le journal d'acces Nginx (format `combined`), s'il existe."""
    fichier = Path(chemin)
    if not fichier.is_file():
        return [], {
            "chemin": chemin,
            "lu": False,
            "raison": "fichier absent (cas d'une machine sans Nginx)",
        }
    lignes = list(observability.lire_access_log(fichier, depuis_le=depuis_le))
    return lignes, {"chemin": chemin, "lu": True, "requetes": len(lignes)}


def rapport(options) -> tuple[dict, int]:
    """Rapport des cinq mesures, et code de retour.

    Une source absente et une source illisible ne sont pas la meme chose : le
    chemin par defaut peut manquer (poste sans Nginx) sans etre une erreur,
    alors qu'un chemin **nomme par l'operateur** qui ne repond pas est une
    erreur d'appel — elle sort en code 2, pas en silence.
    """
    depuis_le = observability.depuis(options.since)
    sources: dict = {}
    code = 0

    chemin_journal = (
        options.journal if options.journal is not None else get_settings().discovery_log_path
    )
    evenements, etat_journal = _lire_journal(chemin_journal)
    sources["journal"] = etat_journal
    if options.journal and not etat_journal["lu"]:
        code = 2

    requetes, etat_acces = _lire_acces(options.acces, depuis_le)
    sources["acces"] = etat_acces
    if options.acces != ACCES_DEFAUT and not etat_acces["lu"]:
        code = 2

    mesures = observability.agents_report(
        list(observability.filtrer_depuis(evenements, depuis_le)) + requetes,
        limite=options.limite,
    )
    sortie = {
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since": options.since,
        "depuis": depuis_le.isoformat(timespec="seconds") if depuis_le else None,
        "sources": sources,
        "whitelist": observability.ai_agent_whitelist(),
        **mesures,
    }
    return sortie, code


def _afficher(sortie: dict) -> None:
    """Sortie lisible : cinq sections, dans l'ordre des mesures publiees."""
    print(
        f"=== Agents IA - {sortie['genere_le']} - depuis {sortie['since']} "
        f"({sortie['depuis']}) ==="
    )
    for nom, etat in sortie["sources"].items():
        detail = etat.get("raison") or ", ".join(
            f"{cle}={valeur}" for cle, valeur in etat.items() if cle != "chemin"
        )
        print(f"source {nom} : {etat['chemin'] or '-'} -> {detail}")
    print(f"liste blanche : {len(sortie['whitelist'])} agents IA reconnus")

    print("\n1. Requetes par classe d'appelant")
    for classe, nombre in sortie["agents"].items():
        print(f"   {classe:<12} {nombre}")

    print(f"\n2. Top IP des agents IA (pseudonymisees) : {len(sortie['top_ip'])}")
    for ligne in sortie["top_ip"]:
        print(f"   {ligne['requetes']:>6}  {ligne['ip']}")

    print(f"\n3. Top agents : {len(sortie['top_agents'])}")
    for ligne in sortie["top_agents"]:
        editeur = f"  [{ligne['editeur']}]" if ligne.get("editeur") else ""
        print(f"   {ligne['requetes']:>6}  {ligne['agent']}{editeur}")

    print(f"\n4. Top pages des agents IA : {len(sortie['top_pages'])}")
    for ligne in sortie["top_pages"]:
        print(f"   {ligne['requetes']:>6}  {ligne['chemin']}")

    conversion = sortie["conversion"]
    print("\n5. Conversion")
    print(f"   {conversion['definition']}")
    print(
        f"   {conversion['payees']} paiement(s) accepte(s) sur "
        f"{conversion['requetes']} requete(s) d'agent IA -> "
        f"taux {conversion['taux']}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entree : 0 rapport produit, 2 source nommee illisible."""
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
