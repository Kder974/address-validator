#!/usr/bin/env python
"""Génération des emails personnalisés d'une campagne (Tâche 4).

Usage :
    python generate_emails.py --secteur mode --limit 50
    python generate_emails.py --template email_intro --sortie data/campagne_20261010.csv
    python generate_emails.py --limit 20 --enrichir --template email_followup

Ce que fait le script :

1. sélectionne les prospects **jamais contactés** (`statut = nouveau`) du secteur
   demandé, en excluant sans condition les refus et les désinscriptions ;
2. personnalise : détecte le produit depuis le nom de la boutique et le domaine
   (et, avec `--enrichir`, depuis la page d'accueil), puis construit une phrase
   spécifique au prospect ;
3. rend le gabarit `config/prompts/<template>.txt` et **vérifie** qu'aucun
   `{placeholder}` ne subsiste — un email qui part avec `{prenom}` en clair est
   un email perdu ;
4. propose trois objets pour l'A/B testing et affecte à chaque adresse une
   variante **stable** (sha256) : ré-exporter une campagne ne redistribue pas les
   groupes en cours de test ;
5. écrit un CSV prêt pour Instantly / Lemlist / Mailshake / SMTP
   (`email, prenom, nom_boutique, objet_1, objet_2, objet_3, corps_email`, puis
   les colonnes de fusion utiles) ;
6. marque en base ce qui a été préparé (campagne, variante, produit, accroche)
   sans changer le statut : générer n'est pas envoyer.

Le script n'envoie rien, n'ouvre aucune connexion SMTP et n'appelle aucun outil
tiers : il produit un fichier, et c'est Kder qui envoie.

Codes de retour : 0 réussi, 1 aucun email généré, 2 usage invalide.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as c  # noqa: E402

#: Colonnes du fichier de campagne, dans l'ordre attendu par les outils d'envoi.
COLONNES_CAMPAGNE = (
    "email",
    "prenom",
    "nom_boutique",
    "objet_1",
    "objet_2",
    "objet_3",
    "corps_email",
    # Colonnes de fusion et de contrôle, ajoutées après les sept ci-dessus :
    # un outil d'envoi lit les premières colonnes et ignore le reste.
    "variante",
    "objet_retenu",
    "nom",
    "secteur",
    "plateforme",
    "site_web",
    "ville",
    "produit",
    "accroche",
    "campagne",
)

#: Longueur maximale d'un texte de site analysé pour deviner le produit : de quoi
#: lire un titre et une description, pas de quoi comparer 400 ko de HTML.
FENETRE_ANALYSE = 20_000


def mots_du_secteur_presents(texte: str, secteur: dict | None) -> list[str]:
    """Mots-clés du secteur réellement présents dans un texte.

    La comparaison tolère les abréviations de domaine : le jeton `deco` de
    `deco-maison.fr` reconnaît le mot-clé `décoration`. La règle est un préfixe
    commun de quatre caractères au moins, dans un sens ou dans l'autre — assez
    large pour attraper `deco`/`décoration`, assez étroit pour ne pas confondre
    `sport` et `sportwear`… qui sont en fait la même famille, et tant mieux.

    Args:
        texte: nom de boutique, domaine, contenu de page… (déjà concaténables).
        secteur: secteur de `settings.yaml`, ou `None`.

    Returns:
        Les mots-clés trouvés, dans l'ordre du fichier de réglages.
    """
    if not secteur:
        return []
    normalise = c.normaliser(texte)
    jetons = {jeton for jeton in re.split(r"[^a-z0-9]+", normalise) if len(jeton) >= 4}
    trouves: list[str] = []
    for mot in secteur.get("mots_cles") or []:
        cible = c.normaliser(str(mot))
        if not cible:
            continue
        if cible in normalise or any(
            cible.startswith(jeton) or jeton.startswith(cible) for jeton in jetons
        ):
            trouves.append(str(mot))
    return trouves


def produit_pour(fiche: dict, secteur: dict | None, texte_site: str = "") -> tuple[str, list[str]]:
    """Produit à écrire dans le premier paragraphe, et les mots-clés trouvés.

    Deux règles, pour que la phrase reste française dans tous les cas :

    * un mot-clé **pluriel** trouvé dans le nom, le domaine ou la page
      (`montres`, `bijoux`, `sacs`) devient le produit : « vend des montres » ;
    * sinon, on retombe sur la formule du secteur (`produits de beauté`,
      `articles de décoration et de maison`) : « vend des produits de beauté ».
      Un mot-clé comme « décoration » donne « vend des décoration », ce qu'aucun
      commerçant ne prendra au sérieux.
    """
    matiere = " ".join(
        [
            str(fiche.get("nom_boutique") or ""),
            str(fiche.get("domaine") or ""),
            str(fiche.get("produit") or ""),
            (texte_site or "")[:FENETRE_ANALYSE],
        ]
    )
    trouves = mots_du_secteur_presents(matiere, secteur)
    for mot in trouves:
        if len(mot.split()) == 1 and mot.lower().endswith(("s", "x")):
            return mot, trouves
    return c.produit_par_defaut(secteur), trouves


def construire_accroche(
    fiche: dict, secteur: dict | None, trouves: list[str], produit: str
) -> str:
    """Phrase spécifique au prospect, ou chaîne vide.

    Elle n'est écrite que si elle s'appuie sur quelque chose de **vérifiable**
    dans la fiche : un mot-clé du catalogue, une plateforme détectée. Rien n'est
    inventé — une accroche creuse se voit immédiatement et coûte la réponse.

    Returns:
        Une à deux phrases, ou `""` (le gabarit résorbe alors la ligne vide).
    """
    phrases: list[str] = []
    if trouves and produit == c.produit_par_defaut(secteur):
        phrases.append(
            f"En parcourant votre site, j'ai vu que vous travaillez surtout "
            f"sur {trouves[0]}."
        )
    plateforme = str(fiche.get("plateforme") or "")
    if plateforme and plateforme != "Inconnue":
        # « aussi » ne se justifie que s'il y a déjà une phrase : une accroche
        # qui commence par « J'ai aussi noté » alors qu'elle est seule trahit
        # l'automate autant qu'un placeholder oublié.
        liaison = "J'ai aussi noté" if phrases else "J'ai noté"
        phrases.append(f"{liaison} que votre boutique tourne sous {plateforme}.")
    return " ".join(phrases[:2])


def salutation_propre(corps: str) -> str:
    """Corrige « Bonjour , » quand le prénom du prospect est inconnu.

    Un import ne garantit pas le prénom : la source publique ne le donne pas
    toujours. Plutôt que d'écrire `Bonjour ,` (ou pire, d'inventer un prénom), on
    laisse la salutation générique — c'est la seule correction automatique faite
    au texte, et elle est ici, pas dans le gabarit.
    """
    return re.sub(r"Bonjour\s+,", "Bonjour,", corps or "")


def generer_pour_fiche(
    fiche: dict,
    reglages: dict,
    gabarit_objet: str,
    gabarit_corps: str,
    variante: int,
    signature: str,
    desinscription: str,
    texte_site: str = "",
) -> dict:
    """Rend les trois objets et le corps d'un email pour une fiche donnée.

    Args:
        fiche: ligne `prospects` (ou dictionnaire équivalent).
        reglages: réglages complets (objets d'A/B testing, secteurs).
        gabarit_objet: objet du gabarit, utilisé si `settings.yaml` n'en propose pas.
        gabarit_corps: corps du gabarit.
        variante: numéro de variante retenue (1..3).
        signature: bloc de signature déjà lu.
        desinscription: mention de désinscription déjà lue.
        texte_site: page d'accueil récupérée (`--enrichir`), sinon vide.

    Returns:
        `{"produit", "accroche", "objets", "objet_retenu", "corps", "restants"}`
        où `restants` liste les placeholders non remplacés : non vide = email
        inutilisable, à ne pas exporter.
    """
    secteur = c.trouver_secteur(str(fiche.get("secteur") or ""), reglages)
    produit, trouves = produit_pour(fiche, secteur, texte_site)
    accroche = construire_accroche(fiche, secteur, trouves, produit)
    valeurs = {
        "prenom": str(fiche.get("prenom") or ""),
        "nom": str(fiche.get("nom") or ""),
        "nom_boutique": str(fiche.get("nom_boutique") or ""),
        "produit": produit,
        "accroche": accroche,
        "signature": signature,
        "desinscription": desinscription,
        "secteur": str(fiche.get("secteur") or ""),
        "plateforme": str(fiche.get("plateforme") or ""),
        "ville": str(fiche.get("ville") or ""),
    }
    modeles = list((reglages.get("emails") or {}).get("objets_ab") or [])
    if not modeles:
        modeles = [gabarit_objet]
    objets = [c.rendre_gabarit(str(modele), valeurs) for modele in modeles]
    while len(objets) < 3:
        objets.append(objets[0])
    corps = salutation_propre(c.rendre_gabarit(gabarit_corps, valeurs))
    retenue = objets[variante - 1] if 1 <= variante <= len(objets) else objets[0]
    restants = c.placeholders_restants(corps) + c.placeholders_restants(" ".join(objets[:3]))
    return {
        "produit": produit,
        "accroche": accroche,
        "objets": objets[:3],
        "objet_retenu": retenue,
        "corps": corps,
        "restants": sorted(set(restants)),
    }


def selectionner_prospects(
    base, secteur: str | None = None, limite: int | None = None, reprendre: bool = False
) -> list:
    """Prospects éligibles à une campagne.

    Critères : `statut = nouveau` (donc ni envoyé, ni refusé, ni désinscrit, ni
    client) ; un secteur si demandé ; et — sauf `--reprendre` — pas déjà préparé
    dans une campagne précédente.

    Args:
        base: connexion SQLite.
        secteur: code de secteur, ou `None` pour tous.
        limite: nombre maximal de fiches rendues.
        reprendre: inclut les fiches déjà préparées (ré-export après correction).

    Returns:
        Liste de fiches sous forme de dictionnaires (`dict(ligne)`), ce qui
        permet une lecture uniforme `fiche.get("prenom")` dans tout le script.
    """
    requete = "SELECT * FROM prospects WHERE statut = 'nouveau'"
    parametres: list = []
    if secteur:
        requete += " AND secteur = ?"
        parametres.append(secteur)
    if not reprendre:
        requete += " AND campagne = ''"
    requete += " ORDER BY secteur, nom_boutique LIMIT ?"
    parametres.append(int(limite) if limite else 100000)
    return [dict(ligne) for ligne in base.execute(requete, parametres).fetchall()]


def preparer(
    base,
    prospects: list,
    reglages: dict,
    gabarits: tuple[str, str],
    nom_campagne: str,
    variante_force: int | None = None,
    enrichir: bool = False,
    dry_run: bool = False,
) -> dict:
    """Génère les emails d'une sélection et marque la base.

    La base est marquée (campagne, variante, objet retenu, produit, accroche)
    **sans changer le statut** : générer n'est pas envoyer, et un fichier de
    campagne peut être relu, corrigé, voire jeté sans que le CRM mente.

    Args:
        base: connexion SQLite.
        prospects: lignes rendues par `selectionner_prospects`.
        reglages: réglages complets.
        gabarits: `(objet, corps)` du gabarit choisi.
        nom_campagne: nom du fichier de campagne (écrit dans la fiche).
        variante_force: force une variante (1..3) au lieu de la répartition A/B.
        enrichir: lit la page d'accueil pour affiner le produit (réseau).
        dry_run: n'écrit ni en base, ni fichier.

    Returns:
        Rapport : `prospects`, `generes`, `accroches`, `trop_longs`,
        `variantes`, `secteurs`, `ecartes` (`{"email", "motif"}`), `lignes`.
    """
    gabarit_objet, gabarit_corps = gabarits
    signature = c.lire_signature(reglages)
    emails_cfg = reglages.get("emails") or {}
    desinscription = str(emails_cfg.get("mention_desinscription") or "").strip()
    longueur_max = int(emails_cfg.get("longueur_max_caracteres") or 0)
    rapport: dict = {
        "prospects": len(prospects),
        "generes": 0,
        "accroches": 0,
        "trop_longs": 0,
        "variantes": {},
        "secteurs": {},
        "ecartes": [],
        "lignes": [],
    }
    for fiche in prospects:
        texte_site = ""
        if enrichir and fiche["site_web"]:
            code, corps_html, note = c.requete(fiche["site_web"], reglages)
            texte_site = corps_html if code else ""
            if not code:
                c.alerte(f"{fiche['email']} : enrichissement impossible ({note})")
            c.attendre(reglages)

        variante = (
            int(variante_force)
            if variante_force
            else c.variante_deterministe(fiche["email"], 3)
        )
        rendu = generer_pour_fiche(
            fiche,
            reglages,
            gabarit_objet,
            gabarit_corps,
            variante,
            signature,
            desinscription,
            texte_site,
        )
        if rendu["restants"]:
            rapport["ecartes"].append(
                {
                    "email": fiche["email"],
                    "motif": "placeholders:" + ",".join(rendu["restants"]),
                }
            )
            continue
        if longueur_max and len(rendu["corps"]) > longueur_max:
            rapport["trop_longs"] += 1

        ligne = {
            "email": fiche["email"],
            "prenom": fiche["prenom"],
            "nom_boutique": fiche["nom_boutique"],
            "objet_1": rendu["objets"][0],
            "objet_2": rendu["objets"][1],
            "objet_3": rendu["objets"][2],
            "corps_email": rendu["corps"],
            "variante": variante,
            "objet_retenu": rendu["objet_retenu"],
            "nom": fiche["nom"],
            "secteur": fiche["secteur"],
            "plateforme": fiche["plateforme"],
            "site_web": fiche["site_web"],
            "ville": fiche["ville"],
            "produit": rendu["produit"],
            "accroche": rendu["accroche"],
            "campagne": nom_campagne,
        }
        rapport["lignes"].append(ligne)
        rapport["generes"] += 1
        if rendu["accroche"]:
            rapport["accroches"] += 1
        cle_variante = str(variante)
        rapport["variantes"][cle_variante] = rapport["variantes"].get(cle_variante, 0) + 1
        cle_secteur = fiche["secteur"] or "(non qualifié)"
        rapport["secteurs"][cle_secteur] = rapport["secteurs"].get(cle_secteur, 0) + 1

        if not dry_run:
            c.maj_prospect(
                base,
                int(fiche["id"]),
                campagne=nom_campagne,
                variante=variante,
                objet_retenu=rendu["objet_retenu"],
                produit=rendu["produit"],
                accroche=rendu["accroche"],
            )
    return rapport


def afficher_apercu(ligne: dict) -> None:
    """Montre un email complet : seule façon honnête de juger un gabarit."""
    print("\n--- Aperçu : " + str(ligne.get("email")) + " ---")
    print(f"objet retenu : {ligne.get('objet_retenu')}")
    print(f"variante     : {ligne.get('variante')} (répartition stable, indexée sur sha256)")
    print("-" * 72)
    print(ligne.get("corps_email"))
    print("-" * 72)


def construire_arguments() -> argparse.ArgumentParser:
    """Ligne de commande du générateur."""
    analyseur = argparse.ArgumentParser(
        description="Génère les emails personnalisés d'une campagne de prospection.",
        epilog=(
            "Exemple : python generate_emails.py --secteur mode --limit 50 "
            "--template email_intro"
        ),
    )
    analyseur.add_argument("--secteur", help="code de secteur à traiter (défaut : tous)")
    analyseur.add_argument("--limit", type=int, help="nombre maximal d'emails générés")
    analyseur.add_argument(
        "--template",
        default="email_intro",
        help="gabarit de config/prompts/ (défaut : email_intro ; relance : email_followup)",
    )
    analyseur.add_argument(
        "--sortie", help=f"CSV de sortie (défaut : data/campagne_{c.horodatage_jour()}.csv)"
    )
    analyseur.add_argument("--db", help=f"base SQLite (défaut : {c.CHEMIN_DB})")
    analyseur.add_argument(
        "--variante", type=int, choices=(1, 2, 3), help="force la variante d'objet"
    )
    analyseur.add_argument(
        "--enrichir",
        action="store_true",
        help="lit la page d'accueil pour affiner le produit détecté (accès réseau)",
    )
    analyseur.add_argument(
        "--reprendre",
        action="store_true",
        help="inclut les prospects déjà préparés dans une campagne précédente",
    )
    analyseur.add_argument(
        "--dry-run",
        action="store_true",
        help="affiche le résultat sans écrire le CSV ni marquer la base",
    )
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : 0 réussi, 1 aucun email généré, 2 usage invalide."""
    args = construire_arguments().parse_args(argv)
    c.securiser_sortie()
    c.titre("Génération des emails — Address Validator")

    try:
        reglages = c.charger_reglages()
    except (FileNotFoundError, ValueError) as erreur:
        print(f"GENERATION FAIL {erreur}")
        return 1

    if args.secteur and c.trouver_secteur(args.secteur, reglages) is None:
        codes = ", ".join(str(secteur.get("code")) for secteur in c.liste_secteurs(reglages))
        print(f"GENERATION FAIL secteur inconnu : {args.secteur} (codes : {codes})")
        return 2

    try:
        gabarits = c.lire_prompt(args.template, reglages)
    except (FileNotFoundError, ValueError) as erreur:
        print(f"GENERATION FAIL {erreur}")
        return 1

    try:
        base = c.connexion(args.db, creer=False)
    except FileNotFoundError as erreur:
        print(f"GENERATION FAIL {erreur}")
        return 1

    sortie = (
        Path(args.sortie)
        if args.sortie
        else c.DOSSIER_DONNEES / f"campagne_{c.horodatage_jour()}.csv"
    )
    try:
        prospects = selectionner_prospects(base, args.secteur, args.limit, args.reprendre)
        if not prospects:
            print(
                "GENERATION FAIL aucun prospect éligible (statut « nouveau », non préparé).\n"
                "  Importez une liste : python import_prospects.py --input data/prospects.csv"
            )
            return 1
        c.resultat(f"{len(prospects)} prospect(s) éligible(s), gabarit {args.template}")

        rapport = preparer(
            base,
            prospects,
            reglages,
            gabarits,
            sortie.name,
            variante_force=args.variante,
            enrichir=args.enrichir,
            dry_run=args.dry_run,
        )
        print(
            c.tableau(
                ["indicateur", "valeur"],
                [
                    ("prospects examinés", rapport["prospects"]),
                    ("emails générés", rapport["generes"]),
                    ("avec accroche spécifique", rapport["accroches"]),
                    ("écartés (placeholders)", len(rapport["ecartes"])),
                    ("au-dessus de la longueur cible", rapport["trop_longs"]),
                ],
            )
        )
        if rapport["variantes"]:
            print("\nRépartition A/B :")
            maximum = max(rapport["variantes"].values())
            for variante, nombre in sorted(rapport["variantes"].items()):
                print(f"  variante {variante}  {c.barre(nombre, maximum)} {nombre}")
        for ecarte in rapport["ecartes"][:10]:
            c.alerte(f"{ecarte['email']} écarté — {ecarte['motif']}")
        if rapport["trop_longs"]:
            cible = (reglages.get("emails") or {}).get("longueur_max_caracteres")
            c.alerte(
                f"{rapport['trop_longs']} email(s) au-dessus de {cible} caractères : "
                "raccourcir le gabarit, jamais la personnalisation."
            )
        if rapport["lignes"]:
            afficher_apercu(rapport["lignes"][0])

        if args.dry_run:
            c.alerte("simulation : ni CSV écrit, ni base marquée.")
        else:
            nombre = c.ecrire_csv(sortie, COLONNES_CAMPAGNE, rapport["lignes"])
            c.resultat(f"{nombre} ligne(s) écrite(s) dans {sortie}")
            c.journaliser(
                base,
                "generate_emails",
                "generation",
                {
                    "campagne": sortie.name,
                    "template": args.template,
                    "secteur": args.secteur or "tous",
                    "generes": rapport["generes"],
                    "ecartes": len(rapport["ecartes"]),
                    "variantes": rapport["variantes"],
                },
            )
            c.noter_campagne(
                sortie.name,
                {
                    "template": args.template,
                    "prospects": rapport["generes"],
                    "variantes": rapport["variantes"],
                    "secteurs": rapport["secteurs"],
                },
            )
    finally:
        base.close()

    plafond = int((reglages.get("prospection") or {}).get("envois_max_par_jour") or 0)
    if plafond and rapport["generes"] > plafond:
        c.alerte(
            f"{rapport['generes']} emails préparés pour un plafond de {plafond}/jour : "
            "étaler l'envoi sur plusieurs jours (délivrabilité, plaintes)."
        )
    return 0 if rapport["generes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
