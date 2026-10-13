#!/usr/bin/env python
"""Envoi SMTP direct d'une campagne — **simulation par défaut** (Tâche 4).

Usage :
    python envoyer_smtp.py --campagne data/campagne_20261010.csv              # simulation
    python envoyer_smtp.py --campagne data/campagne_20261010.csv --confirmer --limite 20
    python envoyer_smtp.py --campagne data/campagne_20261010.csv --confirmer --relance

Ce script existe parce que « je n'ai pas d'outil d'envoi » n'est pas une raison de
ne pas prospecter. Il est écrit pour qu'on ne puisse pas s'en servir par accident :

* **simulation par défaut** : sans `--confirmer`, il affiche exactement ce qu'il
  enverrait et n'ouvre aucune connexion ;
* **identifiants hors du dépôt** : lus dans l'environnement
  (`PROSPECTION_SMTP_HOST`, `PROSPECTION_SMTP_PORT`, `PROSPECTION_SMTP_USER`,
  `PROSPECTION_SMTP_PASSWORD`, `PROSPECTION_SMTP_FROM`, `PROSPECTION_SMTP_FROM_NAME`,
  `PROSPECTION_SMTP_REPLY_TO`). Aucun secret n'est écrit ici, et le script refuse
  de démarrer si l'un des trois premiers manque ;
* **expéditeur crédible** : un domaine grand public (gmail, orange…) est refusé,
  sauf `--accepter-domaine-public` — envoyer de la prospection depuis une adresse
  personnelle détruit la délivrabilité du domaine en quelques jours ;
* **plafond quotidien** appliqué (`prospection.envois_max_par_jour`), pause entre
  deux messages, et jamais deux fois au même prospect : les statuts terminaux
  (`refuse`, `desinscrit`, `converti`) et les envois déjà faits sont écartés ;
* **CRM à jour** : chaque envoi réussi écrit son statut et son événement dans la
  base, sinon le suivi et l'analyse porteraient sur autre chose que la réalité.

Codes de retour : 0 réussi, 1 envoi impossible (configuration ou SMTP en échec),
2 usage invalide.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as c  # noqa: E402
# `lire_campagne` vient d'`export_campaign` : une seule lecture du fichier de
# campagne, une seule définition de ses colonnes.
import export_campaign as export  # noqa: E402

#: Variables d'environnement exigées. Le mot de passe n'est jamais lu ailleurs que
#: dans l'environnement du processus : ni argument (visible dans l'historique du
#: terminal), ni fichier du dépôt.
VARIABLES_SMTP = (
    "PROSPECTION_SMTP_HOST",
    "PROSPECTION_SMTP_USER",
    "PROSPECTION_SMTP_PASSWORD",
)

#: Domaines grand public : expéditeur refusé par défaut (délivrabilité).
DOMAINES_EXPEDITEUR_REFUSES = (
    "gmail.com",
    "googlemail.com",
    "orange.fr",
    "wanadoo.fr",
    "hotmail.fr",
    "hotmail.com",
    "outlook.fr",
    "outlook.com",
    "live.fr",
    "free.fr",
    "sfr.fr",
    "laposte.net",
    "yahoo.fr",
    "yahoo.com",
    "icloud.com",
    "protonmail.com",
    "proton.me",
)


def lire_identifiants(environ: dict | None = None) -> dict:
    """Lit la configuration SMTP dans l'environnement, et la valide.

    Args:
        environ: mapping à interroger ; par défaut `os.environ`.

    Returns:
        `{"hote", "port", "utilisateur", "mot_de_passe", "expediteur",
        "expediteur_nom", "repondre_a", "securise"}`.

    Raises:
        ValueError: variable manquante ou port illisible (le message nomme la
            variable exacte à poser).
    """
    source = os.environ if environ is None else environ
    manquantes = [nom for nom in VARIABLES_SMTP if not str(source.get(nom) or "").strip()]
    if manquantes:
        raise ValueError(
            "identifiants SMTP absents de l'environnement : "
            + ", ".join(manquantes)
            + "\n  Exemple (PowerShell, session courante seulement) :\n"
            "    $env:PROSPECTION_SMTP_HOST='smtp.exemple.fr'\n"
            "    $env:PROSPECTION_SMTP_PORT='587'\n"
            "    $env:PROSPECTION_SMTP_USER='address-validator@kder-ai.com'\n"
            "    $env:PROSPECTION_SMTP_PASSWORD='...'\n"
            "  Jamais dans un fichier du dépôt."
        )
    try:
        port = int(str(source.get("PROSPECTION_SMTP_PORT") or "587").strip())
    except ValueError as erreur:
        raise ValueError("PROSPECTION_SMTP_PORT doit être un nombre (587 ou 465)") from erreur
    utilisateur = str(source["PROSPECTION_SMTP_USER"]).strip()
    return {
        "hote": str(source["PROSPECTION_SMTP_HOST"]).strip(),
        "port": port,
        "utilisateur": utilisateur,
        "mot_de_passe": str(source["PROSPECTION_SMTP_PASSWORD"]),
        "expediteur": str(source.get("PROSPECTION_SMTP_FROM") or utilisateur).strip(),
        "expediteur_nom": str(
            source.get("PROSPECTION_SMTP_FROM_NAME") or "Address Validator"
        ).strip(),
        "repondre_a": str(source.get("PROSPECTION_SMTP_REPLY_TO") or "").strip(),
        # 465 = TLS implicite ; 587 = STARTTLS. Les deux sont chiffrés.
        "securise": port == 465,
    }


def verifier_expediteur(identifiants: dict, accepter_domaine_public: bool) -> None:
    """Refuse un expéditeur dont le domaine n'est pas professionnel.

    Raises:
        ValueError: domaine grand public utilisé sans l'option explicite.
    """
    domaine = identifiants["expediteur"].split("@")[-1].lower()
    if domaine in DOMAINES_EXPEDITEUR_REFUSES and not accepter_domaine_public:
        raise ValueError(
            f"expéditeur sur un domaine grand public ({domaine}) : la prospection depuis "
            "une boîte personnelle finit en indésirables et abîme l'adresse.\n"
            "  Utilisez un domaine professionnel (par exemple address-validator@kder-ai.com), ou "
            "ajoutez --accepter-domaine-public en connaissance de cause."
        )


def construire_message(fiche: dict, identifiants: dict) -> EmailMessage:
    """Assemble un message RFC 5322 complet, en texte brut uniquement.

    Un email de prospection sans en-tête de désinscription est un signal de spam :
    ici, le corps part en texte UTF-8, avec `Date`, `Message-ID`, `Reply-To` et
    `List-Unsubscribe` posés comme les pose un expéditeur sérieux.
    """
    message = EmailMessage()
    message["From"] = formataddr((identifiants["expediteur_nom"], identifiants["expediteur"]))
    message["To"] = str(fiche["email"])
    message["Subject"] = str(fiche.get("objet_retenu") or fiche.get("objet_1") or "")
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=identifiants["expediteur"].split("@")[-1])
    if identifiants["repondre_a"]:
        message["Reply-To"] = identifiants["repondre_a"]
    adresse_retrait = identifiants["repondre_a"] or identifiants["expediteur"]
    message["List-Unsubscribe"] = f"<mailto:{adresse_retrait}?subject=stop>"
    message.set_content(str(fiche.get("corps_email") or ""), charset="utf-8")
    return message


def selectionner(base, lignes: list[dict], relance: bool) -> tuple[list[dict], list[dict]]:
    """Écarte ce qui ne doit pas partir, et dit pourquoi.

    Aucun envoi vers un prospect inconnu, terminal (`refuse`, `desinscrit`,
    `converti`) ou déjà contacté — sauf en mode `--relance`, qui ne garde que les
    prospects contactés **sans réponse**.

    Returns:
        `(à_envoyer, écartés)` où chaque écarté porte `{"email", "motif"}`.
    """
    a_envoyer: list[dict] = []
    ecartes: list[dict] = []
    for ligne in lignes:
        email = str(ligne.get("email") or "")
        fiche = c.prospect_par_email(base, email)
        if fiche is None:
            ecartes.append({"email": email, "motif": "absent_de_la_base"})
            continue
        statut = str(fiche["statut"])
        if statut in c.STATUTS_TERMINAUX:
            ecartes.append({"email": email, "motif": f"statut_{statut}"})
            continue
        if relance:
            # Une relance ne s'adresse qu'à un prospect déjà contacté, sans réponse.
            if statut not in ("envoye", "ouvert") or fiche["date_reponse"]:
                ecartes.append({"email": email, "motif": "pas_eligible_a_la_relance"})
                continue
        else:
            # L'introduction ne s'adresse qu'à un prospect jamais contacté : une
            # fiche `ouvert` ou `repondu` a déjà reçu l'email, même si l'export de
            # l'outil d'envoi ne contenait pas la ligne « envoyé ».
            if statut != "nouveau" or fiche["date_envoi"]:
                ecartes.append({"email": email, "motif": "deja_contacte"})
                continue
        a_envoyer.append({**ligne, "_id": int(fiche["id"]), "_statut": statut})
    return a_envoyer, ecartes


def type_envoi_relance(base, prospect_id: int) -> str:
    """Rang d'une relance : `relance_1` si une seule introduction est consignée."""
    lignes = base.execute(
        "SELECT DISTINCT substr(date_evenement, 1, 10) AS jour FROM envois"
        " WHERE prospect_id = ? AND type IN ('intro','relance_1','relance_2')",
        (int(prospect_id),),
    ).fetchall()
    return "relance_1" if len({ligne["jour"] for ligne in lignes}) <= 1 else "relance_2"


def ouvrir_connexion(identifiants: dict):
    """Ouvre une session SMTP chiffrée (TLS implicite sur 465, STARTTLS sinon).

    Raises:
        smtplib.SMTPAuthenticationError: identifiants refusés par le serveur.
    """
    if identifiants["securise"]:
        serveur = smtplib.SMTP_SSL(
            identifiants["hote"],
            identifiants["port"],
            timeout=20,
            context=ssl.create_default_context(),
        )
    else:
        serveur = smtplib.SMTP(identifiants["hote"], identifiants["port"], timeout=20)
        serveur.starttls(context=ssl.create_default_context())
    serveur.login(identifiants["utilisateur"], identifiants["mot_de_passe"])
    return serveur


def envoyer(
    base,
    a_envoyer: list[dict],
    identifiants: dict,
    relance: bool,
    pause: float,
    dry_run: bool,
) -> dict:
    """Envoie les messages un par un et tient le CRM à jour après chaque succès.

    L'échec d'un message n'interrompt pas la campagne : il est compté et le
    suivant part. Une authentification refusée, en revanche, arrête tout à la
    première tentative — insister ne ferait qu'aggraver le blocage du compte.

    Returns:
        `{"envoyes", "echecs", "messages"}`.
    """
    rapport: dict = {"envoyes": 0, "echecs": [], "messages": []}
    serveur = None
    try:
        for position, fiche in enumerate(a_envoyer, start=1):
            message = construire_message(fiche, identifiants)
            if dry_run:
                rapport["messages"].append(
                    {
                        "email": fiche["email"],
                        "objet": str(message["Subject"]),
                        "caracteres": len(str(fiche.get("corps_email") or "")),
                    }
                )
                continue
            try:
                if serveur is None:
                    serveur = ouvrir_connexion(identifiants)
                serveur.send_message(message)
            except smtplib.SMTPAuthenticationError as erreur:
                rapport["echecs"].append(
                    {
                        "email": fiche["email"],
                        "motif": f"authentification refusée ({erreur.smtp_code})",
                    }
                )
                raise
            except (smtplib.SMTPException, OSError, ssl.SSLError) as erreur:
                rapport["echecs"].append(
                    {"email": fiche["email"], "motif": f"{type(erreur).__name__}: {erreur}"}
                )
                continue

            rapport["envoyes"] += 1
            type_evenement = type_envoi_relance(base, fiche["_id"]) if relance else "intro"
            c.maj_prospect(base, fiche["_id"], statut="envoye", date_envoi=c.maintenant())
            c.enregistrer_evenement(
                base,
                fiche["_id"],
                type_evenement,
                objet=str(fiche.get("objet_retenu") or ""),
                variante=fiche.get("variante") or None,
                detail="envoi smtp direct",
            )
            if pause and position < len(a_envoyer):
                time.sleep(pause)
    except smtplib.SMTPAuthenticationError:
        # Le serveur a refusé les identifiants : la campagne s'arrête ici, les
        # messages déjà partis sont enregistrés et le rapport le dit.
        pass
    finally:
        if serveur is not None:
            try:
                serveur.quit()
            except (smtplib.SMTPException, OSError):
                pass  # la session est de toute façon terminée
    return rapport


def construire_arguments() -> argparse.ArgumentParser:
    """Ligne de commande de l'envoi SMTP."""
    analyseur = argparse.ArgumentParser(
        description="Envoie une campagne en SMTP direct (simulation par défaut).",
        epilog=(
            "Exemple : python envoyer_smtp.py --campagne data/campagne_20261010.csv "
            "--confirmer --limite 20"
        ),
    )
    analyseur.add_argument("--campagne", required=True, help="CSV de campagne (corps rendu)")
    analyseur.add_argument(
        "--limite",
        "--limit",
        dest="limite",
        type=int,
        help="nombre maximal d'envois",
    )
    analyseur.add_argument(
        "--confirmer",
        action="store_true",
        help="envoie réellement (sans ce drapeau : simulation, aucune connexion)",
    )
    analyseur.add_argument(
        "--relance", action="store_true", help="envoie la relance aux prospects sans réponse"
    )
    analyseur.add_argument("--db", help=f"base SQLite (défaut : {c.CHEMIN_DB})")
    analyseur.add_argument("--pause", type=float, help="secondes entre deux messages (défaut : 5)")
    analyseur.add_argument(
        "--accepter-domaine-public",
        action="store_true",
        help="autorise un expéditeur gmail/orange/… (déconseillé)",
    )
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : 0 réussi, 1 envoi impossible, 2 usage invalide."""
    args = construire_arguments().parse_args(argv)
    c.securiser_sortie()
    c.titre("Envoi SMTP — Address Validator (simulation par défaut)")

    try:
        reglages = c.charger_reglages()
    except (FileNotFoundError, ValueError) as erreur:
        print(f"ENVOI FAIL {erreur}")
        return 1

    try:
        lignes = export.lire_campagne(args.campagne)
    except (FileNotFoundError, ValueError) as erreur:
        print(f"ENVOI FAIL {erreur}")
        return 1
    if not lignes:
        print(f"ENVOI FAIL campagne vide : {args.campagne}")
        return 1

    try:
        base = c.connexion(args.db, creer=False)
    except FileNotFoundError as erreur:
        print(f"ENVOI FAIL {erreur}")
        return 1

    rapport: dict = {"envoyes": 0, "echecs": [], "messages": []}
    try:
        a_envoyer, ecartes = selectionner(base, lignes, args.relance)
        plafond = int((reglages.get("prospection") or {}).get("envois_max_par_jour") or 0)
        limite = args.limite or plafond or len(a_envoyer)
        a_envoyer = a_envoyer[:limite]
        print(
            c.tableau(
                ["indicateur", "valeur"],
                [
                    ("prospects dans le fichier", len(lignes)),
                    ("à envoyer", len(a_envoyer)),
                    ("écartés", len(ecartes)),
                    ("plafond quotidien", plafond or "non défini"),
                    ("limite appliquée", limite),
                    ("mode", "relance" if args.relance else "introduction"),
                ],
            )
        )
        motifs: dict[str, int] = {}
        for ecarte in ecartes:
            motifs[ecarte["motif"]] = motifs.get(ecarte["motif"], 0) + 1
        for motif, nombre in sorted(motifs.items(), key=lambda item: -item[1]):
            c.etape(f"écartés — {motif} : {nombre}")
        if not a_envoyer:
            print("ENVOI FAIL aucun destinataire éligible.")
            return 1

        if not args.confirmer:
            for fiche in a_envoyer[:5]:
                c.etape(f"enverrait à {fiche['email']} : « {fiche.get('objet_retenu')} »")
            c.alerte(
                "simulation : aucun message envoyé. Ajouter --confirmer pour envoyer, après "
                "avoir posé les variables PROSPECTION_SMTP_* dans l'environnement."
            )
            return 0

        identifiants = lire_identifiants()
        verifier_expediteur(identifiants, args.accepter_domaine_public)
        c.resultat(
            f"expéditeur {identifiants['expediteur']} via {identifiants['hote']}:"
            f"{identifiants['port']}"
        )
        rapport = envoyer(
            base,
            a_envoyer,
            identifiants,
            args.relance,
            pause=float(args.pause if args.pause is not None else 5.0),
            dry_run=False,
        )
    except ValueError as erreur:
        print(f"ENVOI FAIL {erreur}")
        return 1
    finally:
        base.close()

    print(
        c.tableau(
            ["résultat", "valeur"],
            [("messages envoyés", rapport["envoyes"]), ("échecs", len(rapport["echecs"]))],
        )
    )
    for echec in rapport["echecs"][:10]:
        c.alerte(f"{echec['email']} : {echec['motif']}")
    if rapport["envoyes"]:
        c.journaliser(
            None,
            "envoyer_smtp",
            "envoi",
            {
                "campagne": str(args.campagne),
                "envoyes": rapport["envoyes"],
                "echecs": len(rapport["echecs"]),
                "relance": args.relance,
            },
        )
    if rapport["envoyes"] and not rapport["echecs"]:
        print(f"ENVOI OK envoyes={rapport['envoyes']}")
        return 0
    if rapport["envoyes"]:
        print(f"ENVOI PARTIEL envoyes={rapport['envoyes']} echecs={len(rapport['echecs'])}")
        return 0
    print("ENVOI FAIL aucun message parti.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
