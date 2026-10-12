#!/usr/bin/env python
"""Import des prospects dans la base locale `data/prospects.db` (Tâche 3).

Usage :
    python import_prospects.py --input data/prospects_exemple.csv
    python import_prospects.py --input data/prospects.csv --secteur mode --dry-run
    python import_prospects.py --input data/sourcing_20261010.csv --enrichir
    python import_prospects.py --input data/prospects.csv --decouvrir-emails --limite 50

Ce que fait le script, dans l'ordre :

1. lit le CSV et **refuse** un en-tête qui ne correspond pas (plutôt que de
   décaler silencieusement toutes les colonnes) ;
2. valide chaque adresse (syntaxe stricte, domaines jetables refusés, adresses
   grand public et boîtes génériques signalées) ;
3. enrichit ce qui manque : `mailto:` publié sur le site avec
   `--decouvrir-emails`, plateforme depuis la page d'accueil avec `--enrichir` ;
4. déduplique : une même adresse (insensible à la casse) et une même boutique
   (domaine + nom normalisés) n'entrent qu'une fois ;
5. écrit dans SQLite sans écraser un statut déjà avancé (`envoye`, `repondu`…) ;
6. rend un rapport — importés, doublons, incomplets, refusés ligne par ligne — et
   l'écrit en JSON dans `reports/`.

Hors ligne par défaut : seules `--enrichir` et `--decouvrir-emails` sortent du
poste, et elles s'arrêtent au premier refus de `robots.txt`.

Codes de retour : 0 réussi, 1 aucun import possible (fichier ou en-tête
invalide), 2 usage invalide.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

# Lancé depuis `scripts/`, ce dossier est dans `sys.path` : `common` s'importe
# directement, sans dépendre du répertoire courant.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as c  # noqa: E402

#: Colonnes sans lesquelles le fichier est refusé.
COLONNES_OBLIGATOIRES = ("nom_boutique", "email")

#: Colonnes reconnues. Toute autre colonne du CSV est ignorée sans bruit.
COLONNES_UTILES = (
    "nom_boutique", "site_web", "email", "prenom", "nom", "secteur", "plateforme",
    "taille", "ville", "siren", "pays", "source", "notes",
)


def decouvrir_email(fiche: dict, reglages: dict) -> tuple[str, list[str]]:
    """Cherche une adresse de contact **publiée** par la boutique elle-même.

    Le script ne remplit aucun formulaire et ne devine aucune adresse : il lit
    les pages de contact publiques listées dans `settings.yaml` et retient la
    première adresse `mailto:` visible. Chaque page est visitée une seule fois,
    avec une pause entre deux requêtes, et `robots.txt` est respecté — un site
    qui refuse n'est pas visité, et la raison est écrite dans les notes.

    Args:
        fiche: fiche en cours de construction (utilise `site_web`).
        reglages: réglages complets (section `enrichissement`).

    Returns:
        `(adresse, notes)` : adresse trouvée (chaîne vide si aucune) et la trace
        de ce qui a été tenté, chemin par chemin.
    """
    if not fiche.get("site_web"):
        return "", ["decouverte:site_absent"]
    base = c.normaliser_url(fiche["site_web"]).rstrip("/")
    chemins = ((reglages.get("enrichissement") or {}).get("chemins_contact") or [""])
    notes: list[str] = []
    for chemin in chemins:
        code, corps, note = c.requete(base + str(chemin), reglages)
        etiquette = str(chemin) or "/"
        if code == 0:
            notes.append(f"decouverte:{etiquette}:{note}")
            c.attendre(reglages)
            continue
        adresses = c.extraire_emails(corps)
        if adresses:
            notes.append(f"decouverte:{etiquette}:{adresses[0]}")
            return adresses[0], notes
        notes.append(f"decouverte:{etiquette}:aucune")
        c.attendre(reglages)
    return "", notes


def analyser_ligne(
    ligne: dict,
    reglages: dict,
    secteur_force: str | None = None,
    decouvrir: bool = False,
) -> tuple[dict, list[str], str]:
    """Transforme une ligne CSV brute en fiche, ou en refus motivé.

    Args:
        ligne: ligne lue par `common.lire_csv` (clés en minuscules).
        reglages: réglages complets.
        secteur_force: valeur de `--secteur`, prioritaire sur la colonne du CSV.
        decouvrir: tente de trouver une adresse sur le site quand la colonne
            `email` est vide (accès réseau, opt-in).

    Returns:
        `(fiche, avertissements, motif_refus)`. `motif_refus` est vide quand la
        fiche est exploitable. Un avertissement ne bloque pas : il est recopié
        dans `notes` pour être relu dans le CRM (`boite_personnelle`,
        `prenom_absent`, `secteur_inconnu:xxx`…).
    """
    fiche = {cle: str(ligne.get(cle) or "").strip() for cle in COLONNES_UTILES}
    avertissements: list[str] = []
    notes: list[str] = []

    # 1. Adresse : découverte d'abord, si elle manque et si l'option est active.
    if not fiche["email"] and decouvrir:
        adresse, trace = decouvrir_email(fiche, reglages)
        notes.extend(trace)
        if adresse:
            fiche["email"] = adresse
            avertissements.append("email_decouvert_sur_le_site")

    ok, motif, alertes = c.valider_email(fiche["email"])
    avertissements.extend(alertes)
    if not ok:
        return fiche, avertissements, motif
    fiche["email"] = fiche["email"].lower()

    # 2. Nom de boutique : obligatoire. Le déduire du domaine produirait un
    #    « votre boutique ma-boutique-montres » dans le premier paragraphe.
    if not fiche["nom_boutique"]:
        return fiche, avertissements, "nom_boutique_absent"

    # 3. Site et domaine.
    if fiche["site_web"]:
        fiche["site_web"] = c.normaliser_url(fiche["site_web"])
        fiche["domaine"] = c.domaine_depuis_url(fiche["site_web"])
        if not fiche["domaine"]:
            avertissements.append("site_web_inexploitable")
    else:
        avertissements.append("site_web_absent")

    # 4. Secteur : forcé, sinon déduit du libellé fourni.
    secteur = c.trouver_secteur(secteur_force or fiche["secteur"], reglages)
    if secteur is None:
        if fiche["secteur"]:
            avertissements.append(f"secteur_inconnu:{fiche['secteur']}")
        fiche["secteur"] = ""
    else:
        fiche["secteur"] = str(secteur.get("code") or "")

    # 5. Plateforme : une valeur écrite à la main est conservée ; sinon la
    #    détection hors ligne (motif d'URL) s'applique. Le doute ne s'invente pas.
    plateforme, preuve = c.detecter_plateforme(fiche["site_web"], "", reglages)
    if plateforme != "Inconnue":
        fiche["plateforme"] = plateforme
        notes.append(f"plateforme:{preuve}")
    elif not fiche["plateforme"]:
        fiche["plateforme"] = "Inconnue"

    # 6. Ce qui manque et qui compte pour la personnalisation.
    if not fiche["prenom"]:
        avertissements.append("prenom_absent")
    if not fiche["pays"]:
        fiche["pays"] = "FR"
    if not fiche["source"]:
        fiche["source"] = "import_csv"

    fiche["notes"] = "; ".join(dict.fromkeys(notes + avertissements))
    return fiche, avertissements, ""


def enrichir_fiche(fiche: dict, reglages: dict) -> tuple[dict, list[str]]:
    """Visite la page d'accueil d'une boutique pour nommer sa plateforme.

    La détection hors ligne ne lit que l'URL ; une plateforme utilisée sur un
    domaine propre (Shopify sur `ma-boutique.fr`) n'y est pas visible. Cette
    étape lit donc la page d'accueil — un seul appel, `robots.txt` respecté — et
    ne remplace la valeur existante que si elle apporte une preuve.

    Returns:
        `(fiche, notes)` où `notes` trace l'appel : `plateforme:html:...`,
        `enrichissement:aucune_preuve`, ou la raison d'un échec.
    """
    if not fiche.get("site_web"):
        return fiche, ["enrichissement:site_absent"]
    code, corps, note = c.requete(fiche["site_web"], reglages)
    if code == 0:
        return fiche, [f"enrichissement:{note}"]
    plateforme, preuve = c.detecter_plateforme(fiche["site_web"], corps, reglages)
    if preuve and plateforme != "Inconnue":
        fiche["plateforme"] = plateforme
        return fiche, [f"plateforme:{preuve}"]
    return fiche, ["enrichissement:aucune_preuve"]


def doublon_boutique(base, fiche: dict):
    """Retrouve une fiche en base qui désigne **la même boutique**.

    Règle : même domaine **et** même nom de boutique (comparés sans casse ni
    accent). Deux adresses de contact d'une même enseigne comptent donc pour un
    seul prospect : démarcher deux fois le même commerce sous deux adresses est le
    moyen le plus sûr de passer pour un automate.

    Returns:
        La ligne existante, ou `None`.
    """
    if not fiche.get("domaine"):
        return None
    lignes = base.execute(
        "SELECT * FROM prospects WHERE domaine = ?", (fiche["domaine"],)
    ).fetchall()
    for ligne in lignes:
        if c.normaliser(ligne["nom_boutique"]) == c.normaliser(fiche["nom_boutique"]):
            return ligne
    return None


#: Colonnes copiées à l'insertion, dans l'ordre des valeurs.
COLONNES_INSERTION = (
    "email", "nom_boutique", "site_web", "domaine", "prenom", "nom", "secteur",
    "plateforme", "taille", "ville", "pays", "siren", "source", "notes",
)

#: Colonnes qu'une mise à jour peut compléter — uniquement quand elles sont vides
#: en base. Un import ne doit jamais écraser une correction faite à la main.
COLONNES_COMPLETABLES = (
    "nom_boutique", "site_web", "domaine", "prenom", "nom", "secteur", "plateforme",
    "taille", "ville", "siren",
)


def ecrire_fiche(base, fiche: dict) -> str:
    """Insère ou complète une fiche déjà analysée.

    Returns:
        `insere` (nouvelle ligne), `complete` (des champs vides ont été remplis)
        ou `inchange` (rien de neuf à écrire).
    """
    existant = c.prospect_par_email(base, fiche["email"])
    horodatage = c.maintenant()
    if existant is None:
        colonnes = list(COLONNES_INSERTION) + ["date_import", "date_derniere_maj"]
        valeurs = [fiche.get(colonne, "") for colonne in COLONNES_INSERTION]
        valeurs += [horodatage, horodatage]
        base.execute(
            f"INSERT INTO prospects ({', '.join(colonnes)})"
            f" VALUES ({', '.join('?' * len(colonnes))})",
            valeurs,
        )
        base.commit()
        return "insere"

    champs = {
        colonne: fiche[colonne]
        for colonne in COLONNES_COMPLETABLES
        if not existant[colonne] and fiche.get(colonne) and fiche.get(colonne) != "Inconnue"
    }
    # Les notes ne sont rafraîchies que sur un prospect jamais contacté, et
    # seulement si elles changent : un import rejoué ne doit rien réécrire.
    if existant["statut"] == "nouveau" and str(fiche.get("notes") or "") != str(
        existant["notes"] or ""
    ):
        champs["notes"] = fiche.get("notes", "")
    if not champs:
        return "inchange"
    c.maj_prospect(base, int(existant["id"]), **champs)
    return "complete"


def importer(
    lignes: list[dict],
    reglages: dict,
    base,
    secteur_force: str | None = None,
    enrichir: bool = False,
    decouvrir: bool = False,
    limite: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Écrit les lignes analysées dans la base et rend le rapport chiffré.

    Args:
        lignes: lignes CSV nettoyées.
        reglages: réglages complets.
        base: connexion SQLite ouverte (jamais écrite si `dry_run`).
        secteur_force: valeur de `--secteur`, prioritaire sur la colonne.
        enrichir: visite la page d'accueil pour nommer la plateforme.
        decouvrir: cherche une adresse publiée quand la colonne `email` est vide.
        limite: nombre maximal de lignes traitées (`--limite`).
        dry_run: aucune écriture ; les doublons sont tout de même détectés.

    Returns:
        Compteurs et listes : `lus`, `inseres`, `completes`, `inchanges`,
        `doublons_email`, `doublons_boutique`, `refuses`
        (`{"ligne", "boutique", "email", "motif"}`), `avertissements`,
        `plateformes`, `secteurs` (comptés chacun), `enrichis`.
    """
    rapport: dict = {
        "lus": 0,
        "inseres": 0,
        "completes": 0,
        "inchanges": 0,
        "doublons_email": 0,
        "doublons_boutique": 0,
        "exploitables": 0,
        "refuses": [],
        "avertissements": {},
        "plateformes": {},
        "secteurs": {},
        "enrichis": 0,
    }
    for ligne in lignes:
        if limite is not None and rapport["lus"] >= limite:
            break
        rapport["lus"] += 1
        fiche, avertissements, motif = analyser_ligne(
            ligne, reglages, secteur_force=secteur_force, decouvrir=decouvrir
        )
        for avertissement in avertissements:
            rapport["avertissements"][avertissement] = (
                rapport["avertissements"].get(avertissement, 0) + 1
            )
        if motif:
            rapport["refuses"].append(
                {
                    "ligne": ligne.get("_ligne"),
                    "boutique": fiche.get("nom_boutique") or ligne.get("nom_boutique") or "",
                    "email": fiche.get("email", ""),
                    "motif": motif,
                }
            )
            continue

        if enrichir:
            fiche, notes = enrichir_fiche(fiche, reglages)
            rapport["enrichis"] += 1
            fiche["notes"] = "; ".join(
                dict.fromkeys([fiche.get("notes", ""), *notes])
            ).strip("; ")

        existant = c.prospect_par_email(base, fiche["email"])
        homologue = doublon_boutique(base, fiche)
        if existant is not None:
            # Adresse déjà connue : on ne crée pas de second contact, on laisse
            # `ecrire_fiche` compléter les trous.
            rapport["doublons_email"] += 1
        elif homologue is not None:
            rapport["doublons_boutique"] += 1
            continue

        rapport["exploitables"] += 1
        if not dry_run:
            action = ecrire_fiche(base, fiche)
            cle = {"insere": "inseres", "complete": "completes", "inchange": "inchanges"}
            rapport[cle[action]] += 1

        rapport["plateformes"][fiche["plateforme"]] = (
            rapport["plateformes"].get(fiche["plateforme"], 0) + 1
        )
        cle_secteur = fiche["secteur"] or "(non qualifié)"
        rapport["secteurs"][cle_secteur] = rapport["secteurs"].get(cle_secteur, 0) + 1
    return rapport


def ouvrir_base(chemin: str | None, dry_run: bool):
    """Ouvre la base cible ; en simulation, une base absente reste en mémoire.

    Raison : une simulation qui crée un fichier a déjà modifié le disque. Si la
    base n'existe pas encore, la simulation travaille donc sur une base SQLite en
    mémoire — mêmes requêtes, mêmes doublons détectés, rien d'écrit.

    Returns:
        `(connexion, chemin cible, en_memoire)`.
    """
    cible = Path(chemin) if chemin else c.CHEMIN_DB
    if dry_run and not cible.is_file():
        base = sqlite3.connect(":memory:")
        base.row_factory = sqlite3.Row
        c.initialiser(base)
        return base, cible, True
    base = c.connexion(cible, creer=True)
    c.initialiser(base)
    return base, cible, False


def ecrire_rapport(
    rapport: dict,
    entree: str,
    base_cible: Path,
    dry_run: bool,
    sortie: str | None = None,
) -> Path:
    """Écrit le rapport d'import en JSON dans `reports/` (hors dépôt).

    Un rapport horodaté qui reste après coup est ce qui permet de répondre à
    « qu'est-ce qui a été importé, et qu'est-ce qui a été refusé ? » trois
    semaines plus tard, quand le CSV source a été écrasé.
    """
    chemin = (
        Path(sortie)
        if sortie
        else c.DOSSIER_RAPPORTS / f"import_{c.horodatage_complet()}.json"
    )
    chemin.parent.mkdir(parents=True, exist_ok=True)
    charge = {
        "date": c.maintenant(),
        "entree": entree,
        "base": str(base_cible),
        "simulation": dry_run,
        **rapport,
    }
    chemin.write_text(
        json.dumps(charge, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return chemin


def afficher(rapport: dict, base_cible: Path, en_memoire: bool, dry_run: bool) -> None:
    """Rend le rapport lisible : compteurs, répartitions, refus, avertissements."""
    lignes = [
        ("lues", rapport["lus"]),
        ("insérées", rapport["inseres"]),
        ("complétées", rapport["completes"]),
        ("inchangées", rapport["inchanges"]),
        ("doublons (adresse)", rapport["doublons_email"]),
        ("doublons (boutique)", rapport["doublons_boutique"]),
        ("refusées", len(rapport["refuses"])),
    ]
    print(c.tableau(["compteur", "valeur"], lignes))
    if rapport["secteurs"]:
        print("\nRépartition par secteur :")
        maximum = max(rapport["secteurs"].values())
        for secteur, nombre in sorted(rapport["secteurs"].items(), key=lambda item: -item[1]):
            print(f"  {secteur:<14} {c.barre(nombre, maximum)} {nombre}")
    if rapport["plateformes"]:
        print("\nPlateformes détectées :")
        for plateforme, nombre in sorted(
            rapport["plateformes"].items(), key=lambda item: -item[1]
        ):
            print(f"  {plateforme:<14} {nombre}")
    if rapport["doublons_boutique"]:
        c.alerte(
            f"{rapport['doublons_boutique']} ligne(s) visant une boutique déjà connue "
            "sous une autre adresse : écartées (un commerce = un prospect)."
        )
    if rapport["refuses"]:
        print("\nLignes refusées (motif) :")
        apercu = rapport["refuses"][:15]
        print(
            c.tableau(
                ["ligne", "boutique", "email", "motif"],
                [
                    [item["ligne"], item["boutique"][:28], item["email"][:28], item["motif"]]
                    for item in apercu
                ],
            )
        )
        if len(rapport["refuses"]) > len(apercu):
            c.alerte(f"{len(rapport['refuses']) - len(apercu)} autres refus : voir le rapport.")
    if rapport["avertissements"]:
        print("\nAvertissements (non bloquants, recopiés dans `notes`) :")
        for avertissement, nombre in sorted(
            rapport["avertissements"].items(), key=lambda item: -item[1]
        ):
            print(f"  {avertissement:<38} {nombre}")
    cible = f"{base_cible} (mémoire, simulation)" if en_memoire else str(base_cible)
    marque = "SIMULATION" if dry_run else "IMPORT"
    total = rapport["exploitables"]
    print()
    print(
        f"{marque} OK lues={rapport['lus']} exploitables={total} "
        f"refusees={len(rapport['refuses'])} base={cible}"
    )


def construire_arguments() -> argparse.ArgumentParser:
    """Déclare la ligne de commande : un script de prospection se lit d'abord là."""
    analyseur = argparse.ArgumentParser(
        description="Importe un CSV de prospects dans la base locale de prospection.",
        epilog=(
            "Exemple : python import_prospects.py --input data/prospects.csv "
            "--secteur mode --dry-run"
        ),
    )
    analyseur.add_argument(
        "--input", required=True, help="CSV source (colonnes nom_boutique, email, ...)"
    )
    analyseur.add_argument(
        "--secteur",
        help="code de secteur appliqué à toutes les lignes (mode, high-tech, decoration, "
        "cosmetiques, sport) ; prioritaire sur la colonne du CSV",
    )
    analyseur.add_argument("--db", help=f"base SQLite cible (défaut : {c.CHEMIN_DB})")
    analyseur.add_argument("--limite", type=int, help="nombre maximal de lignes traitées")
    analyseur.add_argument(
        "--dry-run", action="store_true", help="simulation : rien n'est écrit sur le disque"
    )
    analyseur.add_argument(
        "--enrichir",
        action="store_true",
        help="visite la page d'accueil pour nommer la plateforme (accès réseau)",
    )
    analyseur.add_argument(
        "--decouvrir-emails",
        action="store_true",
        help="cherche l'adresse publiée sur le site quand la colonne email est vide (réseau)",
    )
    analyseur.add_argument(
        "--rapport", help="chemin du rapport JSON (défaut : reports/import_<horodatage>.json)"
    )
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : 0 réussi, 1 aucun import exploitable, 2 usage invalide."""
    args = construire_arguments().parse_args(argv)
    c.securiser_sortie()
    c.titre("Import des prospects — Address Validator")

    try:
        reglages = c.charger_reglages()
    except (FileNotFoundError, ValueError) as erreur:
        print(f"IMPORT FAIL {erreur}")
        return 1

    if args.secteur and c.trouver_secteur(args.secteur, reglages) is None:
        codes = ", ".join(str(secteur.get("code")) for secteur in c.liste_secteurs(reglages))
        print(f"IMPORT FAIL secteur inconnu : {args.secteur} (codes utilisables : {codes})")
        return 2

    try:
        lignes, _ = c.lire_csv(args.input, COLONNES_OBLIGATOIRES)
    except (FileNotFoundError, ValueError) as erreur:
        print(f"IMPORT FAIL {erreur}")
        return 1
    c.resultat(f"{len(lignes)} ligne(s) lue(s) dans {args.input}")

    if args.enrichir or args.decouvrir_emails:
        c.alerte(
            "accès réseau activé : robots.txt est lu avant chaque visite et une pause "
            "sépare deux requêtes."
        )

    base, base_cible, en_memoire = ouvrir_base(args.db, args.dry_run)
    try:
        rapport = importer(
            lignes,
            reglages,
            base,
            secteur_force=args.secteur,
            enrichir=args.enrichir,
            decouvrir=args.decouvrir_emails,
            limite=args.limite,
            dry_run=args.dry_run,
        )
        afficher(rapport, base_cible, en_memoire, args.dry_run)
        if args.dry_run:
            c.alerte("simulation : aucun rapport écrit, aucune ligne insérée.")
        else:
            c.journaliser(
                base,
                "import_prospects",
                "import",
                {
                    "entree": str(args.input),
                    "lues": rapport["lus"],
                    "inseres": rapport["inseres"],
                    "completes": rapport["completes"],
                    "refuses": len(rapport["refuses"]),
                    "doublons": rapport["doublons_email"] + rapport["doublons_boutique"],
                },
            )
            chemin_rapport = ecrire_rapport(
                rapport, str(args.input), base_cible, args.dry_run, args.rapport
            )
            c.resultat(f"rapport écrit : {chemin_rapport}")
    finally:
        base.close()

    exploitables = rapport["exploitables"]
    return 0 if exploitables > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
