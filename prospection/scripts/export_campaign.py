#!/usr/bin/env python
"""Export d'une campagne vers un outil d'envoi (Tâche 5.3).

Usage :
    python export_campaign.py --campagne data/campagne_20261010.csv --outil instantly
    python export_campaign.py --campagne data/campagne_exemple.csv --outil lemlist
    python export_campaign.py --campagne data/campagne_exemple.csv --outil smtp

Ce que produit le script, dans `data/export_<outil>_<date>/` :

1. `campagne_<outil>.csv` — le fichier d'import, avec les en-têtes exacts
   attendus par l'outil (Instantly, Lemlist, Mailshake, SMTP) ;
2. `sequence.json` — la séquence complète : introduction J+0, relances J+3 et
   J+7 (cadence lue dans `settings.yaml`), objets et corps rendus, sans aucun
   placeholder ;
3. `instructions_<outil>.md` — la procédure d'import, écran par écran, pour
   l'outil choisi.

Source : le CSV produit par `generate_emails.py` (il contient le corps déjà
rendu). La séquence est re-rendue depuis `config/prompts/`, ce qui permet de
corriger un gabarit de relance et de ré-exporter sans regénérer la campagne.

Le script n'envoie rien et ne se connecte à aucun service.

Codes de retour : 0 réussi, 1 campagne illisible, 2 usage invalide.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as c  # noqa: E402
# `salutation_propre` vit dans `generate_emails` : une seule implémentation de la
# correction de salutation, partagée par la génération et l'export des relances.
import generate_emails as generateur  # noqa: E402

#: Colonnes obligatoires du CSV de campagne : ce que l'export ne peut pas inventer.
COLONNES_CAMPAGNE_OBLIGATOIRES = ("email", "nom_boutique", "corps_email")

#: Formats d'import par outil. Chaque entrée porte :
#:   * `titre` : nom lisible de l'outil ;
#:   * `entetes` : en-têtes exacts du fichier d'import ;
#:   * `correspondance` : `en-tête de l'outil -> colonne de la campagne` ;
#:   * `note` : ce qu'il faut savoir avant d'importer.
FORMATS: dict[str, dict] = {
    "instantly": {
        "titre": "Instantly.ai",
        "entetes": (
            "email",
            "first_name",
            "last_name",
            "company_name",
            "subject_a",
            "subject_b",
            "subject_c",
            "body",
        ),
        "correspondance": {
            "email": "email",
            "first_name": "prenom",
            "last_name": "nom",
            "company_name": "nom_boutique",
            "subject_a": "objet_1",
            "subject_b": "objet_2",
            "subject_c": "objet_3",
            "body": "corps_email",
        },
        "note": (
            "Instantly lit l'en-tête des colonnes : toute colonne inconnue devient une "
            "variable de fusion ({{company_name}}). Le test A/B d'objet se règle ensuite "
            "dans la campagne, avec subject_a / subject_b / subject_c."
        ),
    },
    "lemlist": {
        "titre": "Lemlist",
        "entetes": (
            "email",
            "firstName",
            "lastName",
            "companyName",
            "subjectA",
            "subjectB",
            "subjectC",
            "body",
        ),
        "correspondance": {
            "email": "email",
            "firstName": "prenom",
            "lastName": "nom",
            "companyName": "nom_boutique",
            "subjectA": "objet_1",
            "subjectB": "objet_2",
            "subjectC": "objet_3",
            "body": "corps_email",
        },
        "note": (
            "Lemlist attend les colonnes camelCase ; les variables de fusion s'écrivent "
            "{{firstName}}, {{companyName}}."
        ),
    },
    "mailshake": {
        "titre": "Mailshake",
        "entetes": ("Email Address", "First Name", "Last Name", "Company", "Subject", "Body"),
        "correspondance": {
            "Email Address": "email",
            "First Name": "prenom",
            "Last Name": "nom",
            "Company": "nom_boutique",
            "Subject": "objet_retenu",
            "Body": "corps_email",
        },
        "note": (
            "Mailshake importe une fiche par prospect ; la séquence et l'A/B testing "
            "d'objet se configurent dans la campagne, pas dans le CSV."
        ),
    },
    "smtp": {
        "titre": "SMTP direct (scripts/envoyer_smtp.py)",
        "entetes": ("to", "subject", "body", "objet_2", "objet_3", "variante"),
        "correspondance": {
            "to": "email",
            "subject": "objet_retenu",
            "body": "corps_email",
            "objet_2": "objet_2",
            "objet_3": "objet_3",
            "variante": "variante",
        },
        "note": (
            "Format lu par `scripts/envoyer_smtp.py`, qui refuse de démarrer sans "
            "identifiants SMTP et sans `--confirmer`, applique le plafond quotidien de "
            "`settings.yaml` et met à jour le CRM après chaque envoi. La variante "
            "conservée est celle du fichier ; les objets 2 et 3 servent à un test ultérieur."
        ),
    },
    "generique": {
        "titre": "Format générique (tableur, autre outil)",
        "entetes": (
            "email",
            "prenom",
            "nom_boutique",
            "objet_1",
            "objet_2",
            "objet_3",
            "objet_retenu",
            "corps_email",
            "variante",
            "secteur",
            "plateforme",
            "site_web",
            "ville",
            "produit",
            "accroche",
        ),
        "correspondance": {
            entete: entete
            for entete in (
                "email",
                "prenom",
                "nom_boutique",
                "objet_1",
                "objet_2",
                "objet_3",
                "objet_retenu",
                "corps_email",
                "variante",
                "secteur",
                "plateforme",
                "site_web",
                "ville",
                "produit",
                "accroche",
            )
        },
        "note": "Colonnes complètes : à utiliser avec un tableur ou un outil non listé.",
    },
}


def lire_campagne(chemin: Path | str) -> list[dict]:
    """Lit le CSV de campagne produit par `generate_emails.py`.

    Args:
        chemin: fichier de campagne (`data/campagne_*.csv`).

    Returns:
        Les lignes, telles que lues (clés en minuscules).

    Raises:
        FileNotFoundError: fichier absent.
        ValueError: colonnes obligatoires manquantes — un fichier sans
            `corps_email` ne serait qu'une liste d'adresses.
    """
    lignes, _ = c.lire_csv(chemin, COLONNES_CAMPAGNE_OBLIGATOIRES)
    return lignes


def rendre_sequences(lignes: list[dict], reglages: dict) -> list[dict]:
    """Construit la séquence complète de chaque prospect : intro + relances.

    L'introduction vient du fichier de campagne (déjà rendu et relu) ; les
    relances sont rendues **maintenant** depuis `config/prompts/email_followup.txt`,
    à l'instant où le script tourne : corriger le gabarit de relance puis
    ré-exporter suffit, sans regénérer la campagne.

    Args:
        lignes: lignes du CSV de campagne.
        reglages: réglages complets (cadence `sequence.relances_jours`).

    Returns:
        Une liste de `{"email", "nom_boutique", "variante", "etapes"}`, chaque
        étape portant `rang`, `type`, `delai_jours`, `objet`, `corps`.

    Raises:
        ValueError: un placeholder subsiste dans une relance (gabarit incomplet).
    """
    objet_gabarit, corps_gabarit = c.lire_prompt("email_followup", reglages)
    signature = c.lire_signature(reglages)
    emails_cfg = reglages.get("emails") or {}
    desinscription = str(emails_cfg.get("mention_desinscription") or "").strip()
    delais = list((reglages.get("sequence") or {}).get("relances_jours") or [3, 7])

    sequences: list[dict] = []
    for ligne in lignes:
        valeurs = {
            "prenom": str(ligne.get("prenom") or ""),
            "nom": str(ligne.get("nom") or ""),
            "nom_boutique": str(ligne.get("nom_boutique") or ""),
            "produit": str(ligne.get("produit") or "produits en ligne"),
            "accroche": str(ligne.get("accroche") or ""),
            "signature": signature,
            "desinscription": desinscription,
            "secteur": str(ligne.get("secteur") or ""),
            "plateforme": str(ligne.get("plateforme") or ""),
            "ville": str(ligne.get("ville") or ""),
        }
        objet_relance = c.rendre_gabarit(objet_gabarit, valeurs)
        corps_relance = generateur.salutation_propre(c.rendre_gabarit(corps_gabarit, valeurs))
        restants = c.placeholders_restants(objet_relance + "\n" + corps_relance)
        if restants:
            raise ValueError(
                f"relance non rendue pour {ligne.get('email')} : placeholders {restants} "
                "— corriger config/prompts/email_followup.txt"
            )
        etapes = [
            {
                "rang": 1,
                "type": "intro",
                "delai_jours": 0,
                "objet": str(ligne.get("objet_retenu") or ligne.get("objet_1") or ""),
                "corps": str(ligne.get("corps_email") or ""),
            }
        ]
        for index, delai in enumerate(delais[:2], start=1):
            etapes.append(
                {
                    "rang": index + 1,
                    "type": f"relance_{index}",
                    "delai_jours": int(delai),
                    "objet": objet_relance,
                    "corps": corps_relance,
                }
            )
        sequences.append(
            {
                "email": str(ligne.get("email") or ""),
                "nom_boutique": str(ligne.get("nom_boutique") or ""),
                "variante": ligne.get("variante") or "",
                "etapes": etapes,
            }
        )
    return sequences


def csv_outil(lignes: list[dict], outil: str) -> list[dict]:
    """Traduit les lignes de campagne vers les en-têtes attendus par l'outil.

    Args:
        lignes: lignes du CSV de campagne.
        outil: clé de `FORMATS` (validation faite par l'appelant).

    Returns:
        Les lignes prêtes à écrire, une clé par en-tête d'outil.
    """
    correspondance = FORMATS[outil]["correspondance"]
    return [
        {entete: ligne.get(colonne, "") for entete, colonne in correspondance.items()}
        for ligne in lignes
    ]


def instructions(outil: str, dossier: Path, sequences: list[dict], plafond: int) -> str:
    """Rédige la procédure d'import propre à l'outil, en markdown.

    Le texte est volontairement opérationnel (où cliquer, quoi vérifier avant de
    lancer) et rappelle les garde-fous RGPD : ce ne sont pas des formalités, ce
    sont les conditions pour que le domaine d'envoi reste utilisable.
    """
    format_ = FORMATS[outil]
    lignes = [
        f"# Import de la campagne dans {format_['titre']}",
        "",
        f"Dossier produit : `{dossier.as_posix()}`",
        f"Prospects dans la séquence : {len(sequences)}",
        "",
        "## Fichiers",
        "",
        f"- `campagne_{outil}.csv` : le fichier d'import (en-têtes de l'outil).",
        "- `sequence.json` : la séquence complète (introduction, puis relances J+3 et "
        "J+7), avec objet et corps rendus pour chaque prospect.",
        f"- `instructions_{outil}.md` : ce mode opératoire.",
        "",
        "## Procédure",
        "",
        f"1. Créer la campagne dans {format_['titre']} et y importer `campagne_{outil}.csv`.",
        "2. Vérifier l'aperçu d'un prospect : prénom, nom de boutique et objet doivent "
        "apparaître en clair (aucune accolade visible).",
        "3. Créer la séquence : étape 1 = introduction, étape 2 = relance (délai 3 jours), "
        "étape 3 = relance (délai 7 jours). Le corps de chaque étape est dans "
        "`sequence.json` (`etapes[].corps`).",
        "4. Activer l'A/B testing d'objet : les trois variantes sont dans les colonnes "
        "`objet_1`, `objet_2`, `objet_3` et dans `etapes[0].objet`.",
        f"5. Plafonner l'envoi à {plafond} messages par jour (réglage "
        "`prospection.envois_max_par_jour`).",
        "6. Lancer sur 10 prospects d'abord, lire une vraie réponse, puis élargir.",
        "",
        "## À vérifier avant d'envoyer",
        "",
        "- **Délivrabilité** : SPF, DKIM et DMARC publiés pour le domaine d'envoi ; un "
        "domaine neuf se chauffe progressivement (jamais de rafale).",
        "- **Désinscription** : chaque email porte déjà une phrase de retrait ; une "
        "demande doit être honorée le jour même (`track_responses.py --marquer "
        "email:desinscrit`).",
        "- **Ciblage** : écrire à une adresse professionnelle publiée par la boutique "
        "(RGPD, intérêt légitime, prospection B2B).",
        "- **Contenu** : aucune promesse chiffrée qui ne soit démontrable ; le prix "
        "0,02 $ et le coût d'un échec (17 à 30 $) doivent correspondre à la réalité.",
        "",
        "## Après l'envoi",
        "",
        "1. Exporter les événements de l'outil (envoyé, ouvert, répondu, rebond).",
        "2. `python track_responses.py --input <export.csv>` pour mettre à jour le CRM.",
        "3. `python analyze_results.py` pour comparer les objets et les secteurs.",
        "",
        f"Note sur l'outil : {format_['note']}",
        "",
    ]
    return "\n".join(lignes)


def construire_arguments() -> argparse.ArgumentParser:
    """Ligne de commande de l'export."""
    analyseur = argparse.ArgumentParser(
        description="Exporte une campagne vers un outil d'envoi (Instantly, Lemlist, ...).",
        epilog=(
            "Exemple : python export_campaign.py --campagne data/campagne_20261010.csv "
            "--outil instantly"
        ),
    )
    analyseur.add_argument("--campagne", required=True, help="CSV produit par generate_emails.py")
    analyseur.add_argument(
        "--outil", required=True, choices=sorted(FORMATS), help="format de sortie attendu"
    )
    analyseur.add_argument(
        "--dossier", help="dossier de sortie (défaut : data/export_<outil>_<date>/)"
    )
    analyseur.add_argument(
        "--dry-run", action="store_true", help="affiche l'aperçu sans rien écrire"
    )
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : 0 réussi, 1 campagne illisible, 2 usage invalide."""
    args = construire_arguments().parse_args(argv)
    c.securiser_sortie()
    c.titre(f"Export de campagne — {FORMATS[args.outil]['titre']}")

    try:
        reglages = c.charger_reglages()
    except (FileNotFoundError, ValueError) as erreur:
        print(f"EXPORT FAIL {erreur}")
        return 1

    try:
        lignes = lire_campagne(args.campagne)
    except (FileNotFoundError, ValueError) as erreur:
        print(f"EXPORT FAIL {erreur}")
        return 1
    if not lignes:
        print(f"EXPORT FAIL campagne vide : {args.campagne}")
        return 1
    c.resultat(f"{len(lignes)} prospect(s) lus dans {args.campagne}")

    try:
        sequences = rendre_sequences(lignes, reglages)
    except (FileNotFoundError, ValueError) as erreur:
        print(f"EXPORT FAIL {erreur}")
        return 1

    dossier = (
        Path(args.dossier)
        if args.dossier
        else c.DOSSIER_DONNEES / f"export_{args.outil}_{c.horodatage_jour()}"
    )
    plafond = int((reglages.get("prospection") or {}).get("envois_max_par_jour") or 0)
    etapes = len(sequences[0]["etapes"]) if sequences else 0
    print(
        c.tableau(
            ["indicateur", "valeur"],
            [
                ("prospects", len(sequences)),
                ("étapes par prospect", etapes),
                ("relances", max(0, etapes - 1)),
                ("plafond quotidien", plafond or "non défini"),
                ("dossier de sortie", str(dossier)),
            ],
        )
    )
    print("\nPremière étape de la séquence :")
    premiere = sequences[0]["etapes"][0]
    print(f"  objet : {premiere['objet']}")
    print("  corps :")
    for ligne in str(premiere["corps"]).splitlines()[:6]:
        print(f"    {ligne}")

    if args.dry_run:
        c.alerte("simulation : aucun fichier écrit.")
        return 0

    dossier.mkdir(parents=True, exist_ok=True)
    chemin_csv = dossier / f"campagne_{args.outil}.csv"
    c.ecrire_csv(chemin_csv, FORMATS[args.outil]["entetes"], csv_outil(lignes, args.outil))
    chemin_json = dossier / "sequence.json"
    charge = {
        "outil": args.outil,
        "genere_le": c.maintenant(),
        "source": str(args.campagne),
        "sequence": [
            {
                "email": sequence["email"],
                "nom_boutique": sequence["nom_boutique"],
                "variante": sequence["variante"],
                "etapes": sequence["etapes"],
            }
            for sequence in sequences
        ],
    }
    chemin_json.write_text(
        json.dumps(charge, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    chemin_md = dossier / f"instructions_{args.outil}.md"
    chemin_md.write_text(instructions(args.outil, dossier, sequences, plafond), encoding="utf-8")
    c.resultat(f"import écrit : {chemin_csv}")
    c.resultat(f"séquence écrite : {chemin_json}")
    c.resultat(f"mode opératoire écrit : {chemin_md}")
    c.journaliser(
        None,
        "export_campaign",
        "export",
        {
            "outil": args.outil,
            "campagne": str(args.campagne),
            "prospects": len(sequences),
            "dossier": str(dossier),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
