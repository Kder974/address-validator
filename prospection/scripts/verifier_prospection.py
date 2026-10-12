#!/usr/bin/env python
"""Vérification exécutable de la chaîne de prospection (oracle de recette).

Usage :
    python verifier_prospection.py              # tout, sans accès réseau
    python verifier_prospection.py --reseau     # inclut le sourcing public

Ce script est la preuve que la chaîne tient debout, et pas seulement qu'elle
s'exécute : il fabrique une base temporaire, importe les cinq prospects d'exemple,
génère la campagne, applique un export de réponses, analyse, exporte vers les cinq
formats d'outil, puis interroge les routes du CRM (`/prospection`) avec un client
de test — mot de passe absent, mauvais, bon, base manquante comprise.

Aucun fichier du dépôt n'est touché : tout se passe dans un dossier temporaire.

Lancé sur une copie du dossier prise **hors du dépôt**, il écrit
`SKIP routes du CRM` — ces routes vivent dans l'application (`app/`) — et le
rappelle dans sa dernière ligne : jamais de vert silencieux.

Sortie : une ligne `PASS` ou `FAIL` par vérification, puis
`VERIF PROSPECTION OK n/n` (code 0) ou `VERIF PROSPECTION FAIL n/<total>` (code 1).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Sequence

DOSSIER_SCRIPTS = Path(__file__).resolve().parent
RACINE_DEPOT = DOSSIER_SCRIPTS.parents[1]
sys.path.insert(0, str(DOSSIER_SCRIPTS))
sys.path.insert(0, str(RACINE_DEPOT))

import common as c  # noqa: E402

sys.path.insert(0, str(RACINE_DEPOT))

import analyze_results as analyse  # noqa: E402
import envoyer_smtp as envoi  # noqa: E402
import export_campaign as export  # noqa: E402
import generate_emails as generation  # noqa: E402
import import_prospects as importation  # noqa: E402
import sourcer_prospects as sourcing  # noqa: E402
import track_responses as suivi  # noqa: E402

#: Compteurs globaux : une vérification = une ligne.
REUSSITES = 0
ECHECS: list[str] = []
#: Vérifications volontairement non exécutées (dossier hors du dépôt), jamais des réussites.
IGNORES: list[str] = []


def verifier(condition: bool, libelle: str) -> bool:
    """Enregistre une vérification et affiche son verdict."""
    global REUSSITES
    if condition:
        REUSSITES += 1
        print(f"PASS  {libelle}")
        return True
    ECHECS.append(libelle)
    print(f"FAIL  {libelle}")
    return False


def preparer_base(dossier: Path):
    """Crée une base temporaire et y importe les cinq prospects d'exemple."""
    chemin = dossier / "prospects.db"
    base = c.connexion(chemin, creer=True)
    c.initialiser(base)
    lignes, _ = c.lire_csv(c.DOSSIER_DONNEES / "prospects_exemple.csv", ("nom_boutique", "email"))
    rapport = importation.importer(lignes, REGLAGES, base)
    return base, chemin, rapport


REGLAGES = c.charger_reglages()


def verifier_reglages() -> None:
    """Réglages et gabarits : le fichier se lit, les prompts se parsent."""
    verifier(len(c.liste_secteurs(REGLAGES)) >= 4, "settings.yaml déclare au moins 4 secteurs")
    verifier(
        c.trouver_secteur("decoration", REGLAGES) is not None,
        "un secteur se retrouve par son code (decoration)",
    )
    for nom in ("email_intro", "email_followup", "linkedin_message"):
        objet, corps = c.lire_prompt(nom, REGLAGES, objet_obligatoire=nom != "linkedin_message")
        verifier(bool(corps), f"gabarit {nom} : corps non vide")
        verifier(bool(objet) == (nom != "linkedin_message"), f"gabarit {nom} : objet conforme")
    verifier(bool(c.lire_signature(REGLAGES)), "signature lue depuis config/prompts/signature.txt")
    verifier(
        c.placeholders_restants(
            c.lire_prompt("email_intro", REGLAGES)[1] + c.lire_prompt("email_intro", REGLAGES)[0]
        )
        == ["accroche", "desinscription", "nom_boutique", "prenom", "produit", "signature"],
        "les placeholders du gabarit d'introduction sont ceux attendus",
    )


def verifier_donnees() -> None:
    """Validation d'adresses, URL et plateformes."""
    verifier(c.valider_email("contact@ma-boutique.fr")[0], "adresse normale acceptée")
    verifier(
        c.valider_email("contact@yopmail.com")[1] == "domaine_jetable",
        "domaine jetable refusé",
    )
    verifier(c.valider_email("jc..dupont@boutique.fr")[1] == "point_double", "point double refusé")
    verifier(
        "boite_personnelle" in c.valider_email("boutique@gmail.com")[2],
        "adresse grand public signalée (non bloquante)",
    )
    verifier(
        c.detecter_plateforme("https://exemple.myshopify.com", "", REGLAGES)[0] == "Shopify",
        "Shopify détecté depuis l'URL",
    )
    verifier(
        c.detecter_plateforme("https://exemple.fr", "<script>prestashop</script>", REGLAGES)[0]
        == "PrestaShop",
        "PrestaShop détecté depuis le HTML",
    )
    verifier(
        c.detecter_plateforme("https://exemple.fr", "<html></html>", REGLAGES)[0] == "Inconnue",
        "aucune plateforme devinée sans preuve",
    )
    verifier(c.normaliser_url("exemple.fr") == "https://exemple.fr", "URL sans schéma complétée")
    verifier(not c.hote_public("127.0.0.1"), "adresse locale refusée par requete()")
    verifier(c.hote_public("boutique.fr"), "domaine public accepté")


def verifier_import(base, chemin, rapport) -> None:
    """Import des cinq prospects d'exemple, puis rejeu (idempotence)."""
    verifier(rapport["inseres"] == 5, "les 5 prospects d'exemple sont insérés")
    verifier(len(rapport["refuses"]) == 0, "aucune ligne refusée dans l'exemple fourni")
    lignes, _ = c.lire_csv(
        c.DOSSIER_DONNEES / "prospects_exemple.csv", ("nom_boutique", "email")
    )
    rejeu = importation.importer(lignes, REGLAGES, base)
    verifier(
        rejeu["inseres"] == 0 and rejeu["inchanges"] == 5,
        "rejouer le même import ne réécrit rien (idempotence)",
    )
    total = base.execute("SELECT COUNT(*) AS n FROM prospects").fetchone()["n"]
    verifier(total == 5, "la base contient 5 fiches, pas 6")
    verifier(
        base.execute("SELECT COUNT(*) AS n FROM prospects WHERE plateforme = 'Inconnue'").fetchone()[
            "n"
        ]
        == 0,
        "les plateformes fournies dans le CSV ont été conservées",
    )
    verifier(chemin.is_file(), "le fichier SQLite a bien été créé")
    try:
        c.connexion(chemin.parent / "absent.db", creer=False)
        verifier(False, "une base absente doit lever FileNotFoundError")
    except FileNotFoundError:
        verifier(True, "une base absente lève FileNotFoundError (aucune création implicite)")


def verifier_generation(base, dossier: Path) -> Path:
    """Génération de la campagne d'exemple (5 emails) et stabilité des variantes."""
    prospects = generation.selectionner_prospects(base)
    verifier(len(prospects) == 5, "5 prospects jamais contactés sont éligibles à une campagne")
    gabarits = c.lire_prompt("email_intro", REGLAGES)
    rapport = generation.preparer(base, prospects, REGLAGES, gabarits, "campagne_test.csv")
    verifier(rapport["generes"] == 5, "5 emails sont générés")
    verifier(len(rapport["ecartes"]) == 0, "aucun email écarté : aucun placeholder ne subsiste")
    verifier(rapport["accroches"] >= 4, "au moins 4 emails portent une accroche spécifique")
    verifier(
        sorted(rapport["variantes"]) == ["1", "2", "3"],
        "les trois variantes d'objet sont utilisées",
    )
    corps = [ligne["corps_email"] for ligne in rapport["lignes"]]
    verifier(
        all(c.placeholders_restants(un_corps) == [] for un_corps in corps),
        "aucun corps d'email ne contient de placeholder",
    )
    verifier(
        all("{" not in ligne["objet_1"] for ligne in rapport["lignes"]),
        "aucun objet d'email ne contient d'accolade",
    )
    variantes = {ligne["email"]: ligne["variante"] for ligne in rapport["lignes"]}
    reprises = {
        ligne["email"]: c.variante_deterministe(ligne["email"], 3) for ligne in rapport["lignes"]
    }
    verifier(variantes == reprises, "l'affectation des variantes est stable (sha256)")
    verifier(
        len(generation.selectionner_prospects(base)) == 0,
        "générer ne remet pas le statut à « nouveau » (pas de double préparation)",
    )
    chemin = dossier / "campagne_test.csv"
    c.ecrire_csv(chemin, generation.COLONNES_CAMPAGNE, rapport["lignes"])
    verifier(chemin.is_file(), "le CSV de campagne est écrit")
    return chemin


def verifier_envoi(base, chemin_campagne: Path) -> None:
    """Envoi SMTP : fail-closed sans identifiants, sélection sûre, message propre."""
    try:
        envoi.lire_identifiants({})
        verifier(False, "sans variables d'environnement, l'envoi doit refuser de démarrer")
    except ValueError:
        verifier(True, "sans PROSPECTION_SMTP_*, l'envoi refuse de démarrer (fail-closed)")
    identifiants = envoi.lire_identifiants(
        {
            "PROSPECTION_SMTP_HOST": "smtp.exemple.fr",
            "PROSPECTION_SMTP_USER": "contact@kder-ai.com",
            "PROSPECTION_SMTP_PASSWORD": "x",
        }
    )
    verifier(identifiants["port"] == 587 and not identifiants["securise"], "port 587 (STARTTLS)")
    verifier(
        identifiants["expediteur"] == "contact@kder-ai.com",
        "expéditeur = utilisateur par défaut",
    )
    try:
        envoi.verifier_expediteur({"expediteur": "kder@gmail.com"}, False)
        verifier(False, "un expéditeur grand public doit être refusé")
    except ValueError:
        verifier(True, "un expéditeur grand public est refusé par défaut")
    identifiants["expediteur"] = "kder@gmail.com"
    try:
        envoi.verifier_expediteur(identifiants, True)
        verifier(True, "--accepter-domaine-public lève le refus explicitement")
    except ValueError:
        verifier(False, "--accepter-domaine-public devrait lever le refus")
    message = envoi.construire_message(
        {"email": "a@boutique.fr", "objet_retenu": "Objet", "corps_email": "Bonjour"},
        identifiants,
    )
    verifier(
        message["To"] == "a@boutique.fr" and message["List-Unsubscribe"] and message["Message-ID"],
        "le message porte To, Message-ID et List-Unsubscribe",
    )
    lignes = export.lire_campagne(chemin_campagne)
    a_envoyer, ecartes = envoi.selectionner(base, lignes, relance=False)
    verifier(
        len(a_envoyer) == 5 and len(ecartes) == 0,
        "les 5 prospects jamais contactés sont tous éligibles à l'introduction",
    )
    verifier(
        envoi.selectionner(base, lignes, relance=True)[0] == [],
        "aucune relance possible avant le premier envoi",
    )


def verifier_export(chemin_campagne: Path, dossier: Path) -> None:
    """Relecture du CSV de campagne et export vers les cinq formats d'outil."""
    lignes = export.lire_campagne(chemin_campagne)
    verifier(len(lignes) == 5, "le CSV de campagne se relit (5 lignes)")
    sequences = export.rendre_sequences(lignes, REGLAGES)
    verifier(len(sequences[0]["etapes"]) == 3, "la séquence contient introduction + 2 relances")
    verifier(
        [etape["delai_jours"] for etape in sequences[0]["etapes"]] == [0, 3, 7],
        "les délais de relance viennent de settings.yaml",
    )
    verifier(
        all(
            c.placeholders_restants(etape["corps"]) == []
            for etape in sequences[0]["etapes"]
        ),
        "les relances sont rendues sans placeholder",
    )
    for outil, format_ in export.FORMATS.items():
        premiere = export.csv_outil(lignes, outil)[0]
        verifier(
            set(premiere) == set(format_["entetes"]),
            f"colonnes conformes au format {outil}",
        )
    dossier_sortie = dossier / "export_test"
    nombre = c.ecrire_csv(
        dossier_sortie / "campagne_instantly.csv",
        export.FORMATS["instantly"]["entetes"],
        export.csv_outil(lignes, "instantly"),
    )
    verifier(nombre == 5, "l'export Instantly écrit 5 lignes")
    verifier(
        "Procédure" in export.instructions("lemlist", dossier_sortie, sequences, 50),
        "le mode opératoire de l'outil est rédigé",
    )


def verifier_suivi(base, dossier: Path) -> None:
    """Suivi d'un export de réponses : transitions, idempotence, taux."""
    lignes = [
        dict(ligne)
        for ligne in c.lire_csv(c.DOSSIER_DONNEES / "exemple_reponses.csv", ("email",))[0]
    ]
    rapport = suivi.appliquer(base, lignes)
    verifier(
        rapport["appliquees"] == len(lignes),
        "chaque ligne de l'export des réponses change un statut",
    )
    verifier(len(rapport["inconnues"]) == 0, "toutes les adresses de l'export sont en base")
    verifier(
        "nouveau->envoye" in rapport["transitions"],
        "une transition nouveau->envoye est enregistree",
    )
    verifier(rapport["revenu_declare"] > 0, "le revenu déclaré est repris (0,02 USDC)")
    rejeu = suivi.appliquer(base, lignes)
    verifier(rejeu["appliquees"] == 0, "réimporter le même export ne change plus rien")
    revenu = base.execute("SELECT SUM(revenu_usdc) AS total FROM prospects").fetchone()["total"]
    verifier(abs(float(revenu) - 0.02) < 1e-9, "le revenu n'est pas doublé par le rejeu")
    synthese = suivi.synthese(base)
    verifier(synthese["taux"]["ouverture"] != "n/a", "le taux d'ouverture est calculé")
    verifier(
        suivi.rapport_markdown(
            synthese, rapport["transitions"], "exemple_reponses.csv", str(dossier / "suivi.md")
        ).is_file(),
        "le rapport de suivi markdown est écrit",
    )
    verifier(suivi.statut_cible("refuse", "envoye") == "refuse", "un refus reste définitif")
    verifier(suivi.statut_cible("ouvert", "envoye") == "ouvert", "un statut ne recule jamais")
    verifier(suivi.normaliser_statut("Not interested") == "refuse", "vocabulaire d'outil traduit")
    verifier(suivi.normaliser_statut("bounced") == "refuse", "un rebond dur vaut un refus")
    verifier(suivi.normaliser_statut("bonjour") == "", "vocabulaire inconnu : aucun statut inventé")
    verifier(
        suivi.ligne_marquage("contact@boutique.fr:converti")["statut"] == "converti",
        "le marquage manuel email:statut est accepté",
    )


def verifier_analyse(base, dossier: Path) -> None:
    """Analyse : agrégats, recommandations, rapport HTML autonome."""
    agregats = analyse.analyser(base)
    verifier(agregats["totaux"]["prospects"] == 5, "l'analyse voit les 5 prospects")
    verifier(agregats["totaux"]["envois"] >= 2, "l'analyse compte les envois consignés")
    conseils = analyse.recommandations(agregats)
    verifier(bool(conseils), "des recommandations sont produites")
    chemin = analyse.rapport_html(
        agregats, conseils, str(dossier / "analyse_test.html")
    )
    contenu = chemin.read_text(encoding="utf-8")
    verifier("http" not in contenu, "le rapport HTML ne cite aucune ressource externe")
    verifier("<style>" in contenu and "<table>" in contenu, "le rapport HTML a style et tableaux")
    verifier(
        analyse.z_deux_proportions(5, 100, 5, 100) == 0.0,
        "deux taux identiques donnent z = 0 (aucune fausse différence)",
    )
    verifier(
        analyse.z_deux_proportions(0, 0, 1, 10) == 0.0,
        "sans effectif, aucune conclusion n'est tirée",
    )


def verifier_purge(base) -> None:
    """Conservation RGPD : la durée annoncée est applicable, et protégée.

    Placée en dernier dans l'oracle : elle vide la base temporaire (les
    événements suivent leur fiche), ce qui ne doit gêner aucune autre
    vérification.
    """
    simulation = suivi.purger(base, "2099-01-01", confirmer=False)
    verifier(simulation["candidates"] == 5, "la purge annonce les 5 fiches périmées")
    verifier(simulation["supprimees"] == 0, "sans --confirmer, la purge ne supprime rien")
    verifier(
        base.execute("SELECT COUNT(*) AS n FROM prospects").fetchone()["n"] == 5,
        "la simulation de purge laisse la base intacte",
    )
    verifier(
        suivi.purger(base, "2000-01-01", confirmer=True)["supprimees"] == 0,
        "aucune fiche récente n'est supprimée (la date borne vraiment)",
    )
    verifier(
        suivi.purger(base, "2099-01-01", confirmer=True)["supprimees"] == 5,
        "la purge confirmée supprime les fiches périmées",
    )
    verifier(
        base.execute("SELECT COUNT(*) AS n FROM prospects").fetchone()["n"] == 0,
        "la base ne contient plus aucune fiche après purge",
    )
    verifier(
        base.execute("SELECT COUNT(*) AS n FROM envois").fetchone()["n"] == 0,
        "les événements suivent leur fiche (cascade)",
    )


def verifier_sourcing() -> None:
    """Sourcing public (uniquement avec `--reseau` : l'annuaire est distant)."""
    secteur = c.trouver_secteur("mode", REGLAGES)
    rapport = sourcing.sourcer(REGLAGES, secteur, "75", 3)
    verifier(len(rapport["prospects"]) >= 1, "l'annuaire public rend au moins une fiche")
    fiche = rapport["prospects"][0]
    verifier(
        fiche["email"] == "" and fiche["site_web"] == "",
        "le sourcing n'invente ni adresse électronique ni site web",
    )
    verifier(len(fiche["siren"]) == 9, "un SIREN à 9 chiffres est repris")
    verifier(
        set(fiche) == set(sourcing.COLONNES_SOURCING),
        "les colonnes du sourcing sont exactement celles de l'import",
    )
    verifier(
        sourcing.normaliser_nom("DUPONT (MARTIN)") == "Dupont",
        "le nom d'usage entre parenthèses est retiré",
    )
    verifier(
        sourcing.choisir_dirigeant(
            [
                {
                    "type_dirigeant": "personne physique",
                    "nom": "MARTIN",
                    "prenoms": "JEAN",
                    "qualite": "Commissaire aux comptes suppléant",
                },
                {
                    "type_dirigeant": "personne physique",
                    "nom": "DUPONT",
                    "prenoms": "MARIE",
                    "qualite": "Présidente",
                },
            ]
        )
        == ("Marie", "Dupont", "Présidente"),
        "le commissaire aux comptes est écarté au profit du dirigeant",
    )


def verifier_crm(chemin_base: Path, dossier: Path) -> None:
    """Routes du CRM : fermée par défaut, authentifiée, lecture, écriture, export.

    Les routes vivent dans l'application FastAPI du dépôt (`app/`), pas dans ce
    dossier : si l'application n'est pas là (dossier copié ailleurs), on le dit au
    lieu de planter — mais un `app/` présent dont l'import échoue reste un échec,
    pour ne jamais obtenir un vert qui masque une régression.
    """
    import base64

    if not (RACINE_DEPOT / "app" / "main.py").is_file():
        IGNORES.append("routes du CRM /prospection (app/ absent)")
        print(f"SKIP  routes du CRM : application FastAPI absente de {RACINE_DEPOT}")
        return

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    parametres = get_settings()
    origine = (parametres.dashboard_password, parametres.prospection_db)
    client = TestClient(app)
    try:
        parametres.dashboard_password = ""
        reponse = client.get("/prospection")
        verifier(
            reponse.status_code == 503 and "DASHBOARD_PASSWORD" in reponse.text,
            "sans mot de passe, /prospection répond 503 (fermé par défaut)",
        )
        parametres.dashboard_password = "porte-prospection-secret"
        parametres.prospection_db = str(chemin_base)
        verifier(
            client.get("/prospection").status_code == 401,
            "sans identifiants, /prospection répond 401",
        )
        entetes = {
            "Authorization": "Basic "
            + base64.b64encode(b"kder:porte-prospection-secret").decode()
        }
        page = client.get("/prospection", headers=entetes)
        verifier(page.status_code == 200, "avec le bon couple, la page répond 200")
        verifier("content-security-policy" in page.headers, "la page porte une CSP")
        verifier(
            "connect-src 'self'" in page.headers.get("content-security-policy", ""),
            "la CSP borne le script aux appels vers ce service",
        )
        verifier("http" not in page.text, "la page ne cite aucune ressource externe")
        liste = client.get("/prospection/api/prospects", headers=entetes)
        verifier(
            liste.status_code == 200 and len(liste.json()["prospects"]) == 5,
            "l'API des prospects rend les 5 fiches",
        )
        filtre = client.get(
            "/prospection/api/prospects", headers=entetes, params={"statut": "desinscrit"}
        )
        verifier(filtre.status_code == 200, "le filtre par statut est accepté")
        stats = client.get("/prospection/api/stats", headers=entetes)
        verifier(
            stats.status_code == 200
            and "taux" in stats.json()
            and "generated_at" in stats.json(),
            "l'API des statistiques répond avec les taux",
        )
        identifiant = liste.json()["prospects"][0]["id"]
        majour = client.post(
            f"/prospection/api/prospects/{identifiant}/statut",
            headers=entetes,
            json={"statut": "desinscrit"},
        )
        verifier(
            majour.status_code == 200 and majour.json()["prospect"]["statut"] == "desinscrit",
            "un statut se met à jour depuis le CRM",
        )
        verifier(
            client.post(
                f"/prospection/api/prospects/{identifiant}/statut",
                headers=entetes,
                json={"statut": "bidon"},
            ).status_code
            == 400,
            "un statut inconnu est refusé (400)",
        )
        export_csv = client.get("/prospection/export.csv", headers=entetes)
        verifier(
            export_csv.status_code == 200 and export_csv.text.startswith("id,email"),
            "l'export CSV part avec ses en-têtes",
        )
        verifier(
            "attachment" in export_csv.headers.get("content-disposition", ""),
            "l'export CSV est proposé en téléchargement",
        )
        parametres.prospection_db = str(dossier / "absente.db")
        verifier(
            client.get("/prospection", headers=entetes).status_code == 503,
            "base absente : la page répond 503 au lieu d'inventer des zéros",
        )
    finally:
        parametres.dashboard_password, parametres.prospection_db = origine


def construire_arguments() -> argparse.ArgumentParser:
    """Ligne de commande de la vérification."""
    analyseur = argparse.ArgumentParser(
        description="Vérifie de bout en bout la chaîne de prospection (oracle de recette).",
        epilog="Exemple : python verifier_prospection.py --reseau",
    )
    analyseur.add_argument(
        "--reseau",
        action="store_true",
        help="inclut le sourcing public (accès Internet vers l'annuaire des entreprises)",
    )
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Exécute toutes les vérifications et rend 0 (tout vert) ou 1 (au moins un échec)."""
    args = construire_arguments().parse_args(argv)
    c.securiser_sortie()
    c.titre("Vérification de la chaîne de prospection — Address Validator")
    with tempfile.TemporaryDirectory(prefix="prospection-verif-") as temporaire:
        dossier = Path(temporaire)
        verifier_reglages()
        verifier_donnees()
        base, chemin, rapport = preparer_base(dossier)
        try:
            verifier_import(base, chemin, rapport)
            chemin_campagne = verifier_generation(base, dossier)
            verifier_envoi(base, chemin_campagne)
            verifier_export(chemin_campagne, dossier)
            verifier_suivi(base, dossier)
            verifier_analyse(base, dossier)
            verifier_crm(chemin, dossier)
            verifier_purge(base)
            if args.reseau:
                verifier_sourcing()
            else:
                print("SKIP  sourcing public (relancer avec --reseau pour l'inclure)")
        finally:
            base.close()

    total = REUSSITES + len(ECHECS)
    suffixe = f"  (ignoré : {' ; '.join(IGNORES)})" if IGNORES else ""
    print()
    if ECHECS:
        print(f"VERIF PROSPECTION FAIL {REUSSITES}/{total}{suffixe}")
        for echec in ECHECS:
            print(f"  - {echec}")
        return 1
    print(f"VERIF PROSPECTION OK {REUSSITES}/{total}{suffixe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

