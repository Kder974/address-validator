#!/usr/bin/env python
"""Sourcing de prospects depuis l'annuaire public des entreprises (objectif 100/semaine).

Usage :
    python sourcer_prospects.py --secteur mode --departement 75 --limite 100
    python sourcer_prospects.py --secteur cosmetiques --departement 974 --limite 50 --importer

Source : l'API publique **Recherche d'entreprises** (DINUM/INSEE),
`https://recherche-entreprises.api.gouv.fr/search` — sans clé, sans compte, sans
donnée personnelle cachée. Elle publie ce que la loi rend public, et le script
filtre explicitement sur les entreprises **diffusibles** (`statut_diffusion = O`)
et **actives** (`etat_administratif = A`) : une entreprise qui a demandé à ne pas
figurer n'est jamais reprise.

Ce que produit le script :

* un CSV prêt à importer (`data/sourcing_<date>.csv`) avec les colonnes exactes
  attendues par `import_prospects.py` ;
* avec `--importer`, l'import direct dans la base (mêmes contrôles que le script
  d'import : adresses, doublons, secteur).

Ce que le script ne fait pas, et qu'aucun outil ne fera à la place de Kder :
trouver l'adresse électronique de contact. L'annuaire ne la publie pas. Elle se
relève sur le site de la boutique (`import_prospects.py --decouvrir-emails`) ou à
la main — c'est le prix d'un sourcing propre.

Codes de retour : 0 réussi, 1 aucune entreprise trouvée ou base illisible,
2 usage invalide.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as c  # noqa: E402
import import_prospects as ip  # noqa: E402

#: Colonnes du CSV de sourcing : celles de `import_prospects.py`, ni plus ni moins.
COLONNES_SOURCING = (
    "nom_boutique",
    "site_web",
    "email",
    "prenom",
    "nom",
    "secteur",
    "plateforme",
    "taille",
    "ville",
    "siren",
    "source",
    "notes",
)

#: Tranches d'effectif INSEE → libellé lisible. Les codes viennent de l'INSEE
#: (`tranche_effectif_salarie`), pas d'une convention maison.
TRANCHES_EFFECTIF = {
    "00": "0 salarié",
    "01": "1-2 salariés",
    "02": "3-5 salariés",
    "03": "6-9 salariés",
    "11": "10-19 salariés",
    "12": "20-49 salariés",
    "21": "50-99 salariés",
    "22": "100-199 salariés",
    "31": "200-249 salariés",
    "32": "250-499 salariés",
    "41": "500-999 salariés",
    "42": "1000-1999 salariés",
    "NN": "non renseigné",
}

#: Qualités de dirigeant retenues pour personnaliser l'email. Les commissaires
#: aux comptes, présents dans la source, sont explicitement écartés : ils n'ont
#: rien à voir avec la boutique et donneraient un email adressé au mauvais nom.
QUALITES_RETENUES = (
    "président",
    "présidente",
    "gérant",
    "gérante",
    "directeur général",
    "directeur",
    "fondateur",
    "associé",
    "administrateur",
    "responsable",
)


def normaliser_nom(valeur: str) -> str:
    """Met un nom propre en forme (`DUPONT` → `Dupont`, `JEAN-PIERRE` → `Jean-Pierre`).

    La source INSEE accole parfois la particule d'usage entre parenthèses
    (« Raffaelli (appietto) ») : elle est retirée, un email n'a pas à porter
    l'état civil complet d'une personne.
    """
    sans_parentheses = re.sub(r"\([^)]*\)", " ", str(valeur or ""))
    sans_espaces = re.sub(r"\s+", " ", sans_parentheses).strip()
    return " ".join(mot.capitalize() for mot in sans_espaces.split())


def choisir_dirigeant(dirigeants: list[dict] | None) -> tuple[str, str, str]:
    """Retient le dirigeant le plus pertinent parmi ceux publiés.

    La personne physique dont la qualité est un mandat de direction passe avant
    tout le reste ; un commissaire aux comptes est écarté même s'il figure en
    tête de liste.

    Returns:
        `(prenom, nom, qualite)` — chaînes vides quand rien ne convient.
    """
    candidats: list[tuple[str, str, str]] = []
    for dirigeant in dirigeants or []:
        if str(dirigeant.get("type_dirigeant") or "") != "personne physique":
            continue
        qualite = str(dirigeant.get("qualite") or "").strip()
        if "commissaire" in c.normaliser(qualite):
            continue
        prenoms = str(dirigeant.get("prenoms") or "").strip().split()
        prenom = normaliser_nom(prenoms[0]) if prenoms else ""
        nom = normaliser_nom(str(dirigeant.get("nom") or ""))
        candidats.append((prenom, nom, qualite))
    if not candidats:
        return "", "", ""
    for prenom, nom, qualite in candidats:
        if any(retenue in c.normaliser(qualite) for retenue in QUALITES_RETENUES):
            return prenom, nom, qualite
    return candidats[0]


def resultat_vers_prospect(resultat: dict, secteur_code: str) -> dict:
    """Traduit un résultat de l'annuaire en ligne de prospection.

    Returns:
        Un dictionnaire dont les clés sont exactement celles du CSV d'import.
        `site_web` et `email` restent **vides** : l'annuaire ne les publie pas, et
        les inventer produirait une campagne envoyée à personne.
    """
    siege = resultat.get("siege") or {}
    prenom, nom, qualite = choisir_dirigeant(resultat.get("dirigeants"))
    enseigne = str(siege.get("nom_commercial") or "").strip()
    nom_boutique = enseigne or str(
        resultat.get("nom_complet") or resultat.get("nom_raison_sociale") or ""
    )
    tranche = str(resultat.get("tranche_effectif_salarie") or "")
    effectif = TRANCHES_EFFECTIF.get(tranche)
    notes = ["source:annuaire-entreprises"]
    if resultat.get("activite_principale"):
        notes.append(f"naf:{resultat['activite_principale']}")
    if qualite:
        notes.append(f"qualite:{qualite}")
    if effectif:
        notes.append(f"effectif:{effectif}")
    if resultat.get("nombre_etablissements_ouverts"):
        notes.append(f"etablissements:{resultat['nombre_etablissements_ouverts']}")
    return {
        "nom_boutique": nom_boutique,
        "site_web": "",
        "email": "",
        "prenom": prenom,
        "nom": nom,
        "secteur": secteur_code,
        "plateforme": "",
        "taille": "PME" if tranche in ("11", "12", "21", "22") else (effectif or ""),
        "ville": str(siege.get("libelle_commune") or siege.get("commune") or ""),
        "siren": str(resultat.get("siren") or ""),
        "source": "annuaire-entreprises",
        "notes": "; ".join(note for note in notes if not note.endswith(":")),
    }


def interroger(
    reglages: dict, naf: str, departement: str, tranche: str, page: int
) -> tuple[list[dict], int, str]:
    """Interroge une page de l'annuaire public.

    Args:
        reglages: réglages complets (section `sourcing`).
        naf: code d'activité principale (`47.91B`).
        departement: code de département (`75`, `974`).
        tranche: code de tranche d'effectif INSEE (`11`).
        page: numéro de page (1-based, `per_page` fixé par les réglages).

    Returns:
        `(résultats, total, note)` — `note` explique une réponse vide
        (`HTTP 429`, `robots.txt`, panne réseau) au lieu de la laisser deviner.
    """
    sourcing = reglages.get("sourcing") or {}
    parametres = {
        "activite_principale": naf,
        "departement": departement,
        "tranche_effectif_salarie": tranche,
        "etat_administratif": str(sourcing.get("etat_administratif") or "A"),
        "page": int(page),
        "per_page": int(sourcing.get("per_page") or 25),
    }
    url = f"{sourcing.get('api_url')}?{urllib.parse.urlencode(parametres)}"
    code, corps, note = c.requete(url, reglages, respecter_robots=True)
    if code != 200:
        return [], 0, note or f"HTTP {code}"
    try:
        charge = json.loads(corps)
    except ValueError as erreur:
        return [], 0, f"JSON illisible : {erreur}"
    resultats = [
        resultat
        for resultat in (charge.get("results") or [])
        # Double contrôle : les filtres sont demandés à l'API, et revérifiés ici.
        # Une réponse d'API qui ignorerait un filtre ne doit pas faire entrer une
        # entreprise non diffusible dans la base.
        if str(resultat.get("statut_diffusion") or "") == str(sourcing.get("statut_diffusion") or "O")
        and str(resultat.get("etat_administratif") or "A") == str(sourcing.get("etat_administratif") or "A")
    ]
    return resultats, int(charge.get("total_results") or 0), ""


def sourcer(
    reglages: dict,
    secteur: dict,
    departement: str,
    limite: int,
    tranches: Sequence[str] | None = None,
    nafs: Sequence[str] | None = None,
) -> dict:
    """Parcourt l'annuaire public et rend les fiches candidates.

    Le parcours croise les codes NAF du secteur et les tranches d'effectif, page
    par page, avec une pause entre deux requêtes (politesse d'usage et respect du
    service public qui héberge l'API). Les doublons sont écartés par SIREN : une
    entreprise ne peut pas entrer deux fois, même si elle est trouvée par deux
    codes NAF voisins.

    Args:
        reglages: réglages complets.
        secteur: secteur de `settings.yaml` (fournit les codes NAF et le code CSV).
        departement: code de département (`75`, `974`).
        limite: nombre maximal de fiches retenues (objectif : 100 par semaine).
        tranches: codes d'effectif ; par défaut ceux de `settings.yaml`.
        nafs: codes NAF ; par défaut ceux du secteur.

    Returns:
        `{"prospects", "requetes", "total_annonce", "notes"}`.
    """
    sourcing = reglages.get("sourcing") or {}
    delai = float(sourcing.get("delai_entre_requetes_s") or 1.0)
    taille_page = int(sourcing.get("per_page") or 25)
    codes_naf = list(nafs) if nafs else list(secteur.get("naf") or [])
    codes_tranche = (
        list(tranches) if tranches else list(sourcing.get("tranches_effectif") or ["11"])
    )
    vues: dict[str, dict] = {}
    requetes = 0
    total_annonce = 0
    notes: list[str] = []

    for naf in codes_naf:
        for tranche in codes_tranche:
            page = 1
            while len(vues) < limite:
                resultats, total, note = interroger(reglages, naf, departement, tranche, page)
                requetes += 1
                if note:
                    notes.append(f"{naf}/{tranche}/page {page} : {note}")
                if not resultats:
                    break
                total_annonce = max(total_annonce, total)
                ajouts = 0
                for resultat in resultats:
                    siren = str(resultat.get("siren") or "")
                    if not siren or siren in vues:
                        continue
                    vues[siren] = resultat_vers_prospect(
                        resultat, str(secteur.get("code") or "")
                    )
                    ajouts += 1
                if len(resultats) < taille_page:
                    break  # dernière page de ce croisement
                if not ajouts:
                    break  # page entièrement composée de doublons déjà vus
                page += 1
                if delai > 0 and len(vues) < limite:
                    time.sleep(delai)
            if len(vues) >= limite:
                break
        if len(vues) >= limite:
            break

    return {
        "prospects": list(vues.values())[:limite],
        "requetes": requetes,
        "total_annonce": total_annonce,
        "notes": notes,
    }


def afficher(rapport: dict, chemin: Path | None, secteur: dict, departement: str) -> None:
    """Résume le sourcing : volume, villes, dirigeants nommés, angle mort des emails."""
    prospects = rapport["prospects"]
    nommes = sum(1 for prospect in prospects if prospect.get("prenom"))
    villes: dict[str, int] = {}
    for prospect in prospects:
        ville = prospect.get("ville") or "(inconnue)"
        villes[ville] = villes.get(ville, 0) + 1
    print(
        c.tableau(
            ["indicateur", "valeur"],
            [
                ("secteur", secteur.get("nom")),
                ("département", departement),
                ("requêtes à l'annuaire", rapport["requetes"]),
                ("entreprises annoncées", rapport["total_annonce"]),
                ("fiches retenues", len(prospects)),
                ("avec un dirigeant nommé", nommes),
                ("sans adresse électronique", len(prospects)),
                ("fichier", str(chemin) if chemin else "(aucun — simulation)"),
            ],
        )
    )
    if villes:
        print("\nPrincipales villes :")
        maximum = max(villes.values())
        for ville, nombre in sorted(villes.items(), key=lambda item: -item[1])[:15]:
            print(f"  {ville[:28]:<30} {c.barre(nombre, maximum)} {nombre}")
    if prospects:
        print("\nAperçu (3 premières fiches) :")
        print(
            c.tableau(
                ["boutique", "dirigeant", "ville", "siren"],
                [
                    [
                        str(prospect["nom_boutique"])[:34],
                        f"{prospect['prenom']} {prospect['nom']}".strip() or "-",
                        str(prospect["ville"])[:18],
                        prospect["siren"],
                    ]
                    for prospect in prospects[:3]
                ],
            )
        )
    for note in rapport["notes"][:10]:
        c.alerte(note)
    if prospects:
        c.alerte(
            "aucune adresse électronique : l'annuaire ne les publie pas. Compléter "
            "`site_web` puis relancer l'import avec --decouvrir-emails, ou saisir les "
            "adresses des pages de contact."
        )


def construire_arguments() -> argparse.ArgumentParser:
    """Ligne de commande du sourcing."""
    analyseur = argparse.ArgumentParser(
        description=(
            "Source des prospects dans l'annuaire public des entreprises (API DINUM/INSEE)."
        ),
        epilog=(
            "Exemple : python sourcer_prospects.py --secteur mode --departement 75 --limite 100"
        ),
    )
    analyseur.add_argument(
        "--secteur", required=True, help="code de secteur (mode, high-tech, ...)"
    )
    analyseur.add_argument(
        "--departement", required=True, help="code de département (75, 974, 2A...)"
    )
    analyseur.add_argument(
        "--limite", type=int, default=100, help="nombre de fiches visées (défaut : 100)"
    )
    analyseur.add_argument(
        "--tranches",
        help="tranches d'effectif INSEE séparées par des virgules (défaut : settings.yaml)",
    )
    analyseur.add_argument(
        "--naf", help="codes NAF séparés par des virgules (défaut : ceux du secteur)"
    )
    analyseur.add_argument(
        "--sortie", help="CSV de sortie (défaut : data/sourcing_<date>.csv)"
    )
    analyseur.add_argument(
        "--importer",
        action="store_true",
        help="importe dans la base (les fiches sans adresse sont refusées, comme à l'import)",
    )
    analyseur.add_argument(
        "--decouvrir-emails", action="store_true", help="avec --importer : lit les pages de contact"
    )
    analyseur.add_argument(
        "--enrichir", action="store_true", help="avec --importer : détecte la plateforme"
    )
    analyseur.add_argument("--db", help=f"base SQLite (défaut : {c.CHEMIN_DB})")
    analyseur.add_argument("--dry-run", action="store_true", help="n'écrit aucun fichier")
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : 0 réussi, 1 rien trouvé, 2 usage invalide."""
    args = construire_arguments().parse_args(argv)
    c.securiser_sortie()
    c.titre("Sourcing des prospects — annuaire public des entreprises")

    try:
        reglages = c.charger_reglages()
    except (FileNotFoundError, ValueError) as erreur:
        print(f"SOURCING FAIL {erreur}")
        return 1

    secteur = c.trouver_secteur(args.secteur, reglages)
    if secteur is None:
        codes = ", ".join(str(item.get("code")) for item in c.liste_secteurs(reglages))
        print(f"SOURCING FAIL secteur inconnu : {args.secteur} (codes : {codes})")
        return 2
    nafs = [code.strip() for code in (args.naf or "").split(",") if code.strip()] or list(
        secteur.get("naf") or []
    )
    if not nafs:
        print(
            f"SOURCING FAIL aucun code NAF pour le secteur {secteur.get('code')} : "
            "précisez --naf (exemple : --naf 47.91B)"
        )
        return 2
    tranches = [code.strip() for code in (args.tranches or "").split(",") if code.strip()] or None
    if args.limite <= 0:
        print("SOURCING FAIL --limite doit être un entier positif")
        return 2

    rapport = sourcer(reglages, secteur, args.departement, args.limite, tranches, nafs)
    chemin: Path | None = None
    if rapport["prospects"] and not args.dry_run:
        chemin = (
            Path(args.sortie)
            if args.sortie
            else c.DOSSIER_DONNEES / f"sourcing_{c.horodatage_jour()}.csv"
        )
        c.ecrire_csv(chemin, COLONNES_SOURCING, rapport["prospects"])
    afficher(rapport, chemin, secteur, args.departement)

    if not rapport["prospects"]:
        print("SOURCING FAIL aucune entreprise trouvée avec ces filtres.")
        return 1

    if args.dry_run:
        c.alerte("simulation : aucun fichier écrit.")
        return 0

    c.journaliser(
        None,
        "sourcer_prospects",
        "sourcing",
        {
            "secteur": secteur.get("code"),
            "departement": args.departement,
            "requetes": rapport["requetes"],
            "retenues": len(rapport["prospects"]),
            "fichier": str(chemin),
        },
    )

    if args.importer:
        lignes = [
            {**prospect, "_ligne": index}
            for index, prospect in enumerate(rapport["prospects"], start=2)
        ]
        base = c.connexion(args.db, creer=True)
        try:
            c.initialiser(base)
            resultat = ip.importer(
                lignes,
                reglages,
                base,
                secteur_force=str(secteur.get("code") or ""),
                enrichir=args.enrichir,
                decouvrir=args.decouvrir_emails,
                dry_run=False,
            )
        finally:
            base.close()
        print(
            c.tableau(
                ["import", "valeur"],
                [
                    ("lignes examinées", resultat["lus"]),
                    ("insérées", resultat["inseres"]),
                    ("complétées", resultat["completes"]),
                    ("refusées", len(resultat["refuses"])),
                ],
            )
        )
        if resultat["refuses"]:
            motifs: dict[str, int] = {}
            for refus in resultat["refuses"]:
                motifs[refus["motif"]] = motifs.get(refus["motif"], 0) + 1
            for motif, nombre in sorted(motifs.items(), key=lambda item: -item[1]):
                c.alerte(f"{nombre} fiche(s) refusée(s) : {motif}")
            c.alerte(
                "une fiche sans adresse électronique n'entre pas dans la base : compléter "
                "`site_web` et `email` dans le CSV, puis relancer "
                "`import_prospects.py --input <csv> --decouvrir-emails`."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
