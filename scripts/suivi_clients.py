#!/usr/bin/env python
"""Suivi des clients : qui joindre, pourquoi, dans quel ordre (D50).

QUI : l'exploitant, a la main ; le script est assez simple pour un planificateur.
QUOI : lit la base des comptes (`customers`, `customer_validations`) et ecrit un
       rapport d'appels — qui est refuse maintenant, qui va l'etre, qui n'a jamais
       reussi a s'integrer.
COMMENT : les regles vivent dans `app/suivi.py` (etats, seuils, ordre d'urgence) ;
       ce script ne fait que lire, formater et rendre un code de retour. Un seul
       acces sortant : la base citee (aucun appel reseau vers un fournisseur).
QUAND : une fois par semaine, et avant une campagne d'appels.
OU : ce fichier, a cote des autres outils d'exploitation (`scripts/`).

    .venv\\Scripts\\python.exe scripts\\suivi_clients.py
    .venv\\Scripts\\python.exe scripts\\suivi_clients.py --format csv --sortie suivi.csv
    .venv\\Scripts\\python.exe scripts\\suivi_clients.py --url sqlite:///./recette.sqlite

Codes de retour : 0 = rapport produit (meme sur une base vide : « zero compte » est
une mesure), 2 = rien n'a pu etre mesure (base injoignable, tables absentes, seuil
invalide). Un rapport vide n'est pas un « tout va bien » : le code 2 le dit.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import suivi  # noqa: E402
from app.config import get_settings  # noqa: E402


def horodatage(valeur: str) -> datetime:
    """`--maintenant` : instant ISO 8601, pour rejouer un rapport a une date donnee.

    Utile pour expliquer apres coup pourquoi un client a ete classe « essai
    termine » tel jour, et pour les essais de recette (aucun test ne depend de
    l'horloge du poste).
    """
    texte = valeur.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(texte)
    except ValueError as erreur:
        raise argparse.ArgumentTypeError(
            f"instant ISO 8601 attendu (ex. 2026-10-13T09:00:00+00:00), recu {valeur!r}"
        ) from erreur
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def parseur() -> argparse.ArgumentParser:
    """Les options du suivi. Les seuils par defaut viennent d'`app/suivi.py`."""
    analyseur = argparse.ArgumentParser(
        description="Suivi des clients : qui joindre, pourquoi, dans quel ordre.",
        epilog="Codes de retour : 0 rapport produit, 2 rien n'a pu etre mesure.",
    )
    analyseur.add_argument(
        "--url",
        default=None,
        help="URL SQLAlchemy de la base (defaut : DATABASE_URL de la configuration)",
    )
    analyseur.add_argument(
        "--format",
        choices=("texte", "csv"),
        default="texte",
        help="format du rapport (defaut : texte)",
    )
    analyseur.add_argument(
        "--sortie",
        default=None,
        help="fichier ou ecrire le rapport, en UTF-8 (defaut : sortie standard)",
    )
    analyseur.add_argument(
        "--maintenant",
        type=horodatage,
        default=None,
        help="instant de reference en ISO 8601 (defaut : maintenant, en UTC)",
    )
    analyseur.add_argument(
        "--essai-bientot-jours",
        type=int,
        default=suivi.SEUIL_ESSAI_BIENTOT_JOURS,
        help="essai a joindre quand il reste au plus ce nombre de jours "
        f"(defaut : {suivi.SEUIL_ESSAI_BIENTOT_JOURS})",
    )
    analyseur.add_argument(
        "--jamais-utilise-jours",
        type=int,
        default=suivi.SEUIL_JAMAIS_UTILISE_JOURS,
        help="compte jamais servi depuis au moins ce nombre de jours "
        f"(defaut : {suivi.SEUIL_JAMAIS_UTILISE_JOURS})",
    )
    analyseur.add_argument(
        "--dormant-jours",
        type=int,
        default=suivi.SEUIL_DORMANT_JOURS,
        help="abonne sans appel depuis au moins ce nombre de jours "
        f"(defaut : {suivi.SEUIL_DORMANT_JOURS})",
    )
    analyseur.add_argument(
        "--quota-pct",
        type=int,
        default=suivi.SEUIL_QUOTA_PCT,
        help="part du quota mensuel qui declenche un appel (defaut : "
        f"{suivi.SEUIL_QUOTA_PCT})",
    )
    return analyseur


def main(argv: list[str] | None = None) -> int:
    """Produit le rapport. `0` = mesure faite, `2` = rien n'a pu etre mesure."""
    args = parseur().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        # La console Windows par defaut n'est pas en UTF-8 : sans cela, un nom de
        # boutique accentue ferait planter le rapport sur un poste francais. Sous
        # capture (tests), la sortie peut refuser la reconfiguration : ce n'est
        # pas une raison pour faire echouer un rapport.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

    moteur = create_engine(args.url or get_settings().database_url)
    try:
        with sessionmaker(bind=moteur)() as session:
            lignes = suivi.suivi(
                session,
                maintenant=args.maintenant,
                seuil_essai_jours=args.essai_bientot_jours,
                seuil_jamais_utilise_jours=args.jamais_utilise_jours,
                seuil_dormant_jours=args.dormant_jours,
                seuil_quota_pct=args.quota_pct,
            )
            if args.format == "csv":
                rapport = suivi.rapport_csv(lignes)
            else:
                rapport = suivi.rapport_texte(lignes, args.maintenant)
    except ValueError as erreur:
        # Un seuil impossible : mieux vaut refuser que rendre un rapport vide.
        print(f"SUIVI REFUSE : {erreur}", file=sys.stderr)
        return 2
    except SQLAlchemyError as erreur:
        print(
            f"SUIVI INDISPONIBLE : base non joignable ou tables absentes "
            f"({type(erreur).__name__}: {erreur})",
            file=sys.stderr,
        )
        print(
            "Rien n'a ete mesure : ce n'est pas un « tout va bien » — verifier "
            "--url et que la base a ete initialisee (init_db).",
            file=sys.stderr,
        )
        return 2
    finally:
        moteur.dispose()

    if args.sortie:
        with open(args.sortie, "w", encoding="utf-8", newline="\n") as fichier:
            fichier.write(rapport)
        precision = " (separateur « ; » : a ouvrir via Donnees > A partir d'un texte)" if args.format == "csv" else ""
        print(f"Rapport ecrit : {args.sortie}{precision}")
    else:
        sys.stdout.write(rapport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
