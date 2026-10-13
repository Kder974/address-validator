#!/usr/bin/env python
"""Envoi de l'email de bienvenue a Tiitoune Art (campagne D45-D50).

QUI : l'exploitant, a la main, une fois - apres avoir verifie les 5 liens.
QUOI : envoie l'email de bienvenue dont l'objet, le pre-en-tete et le corps sont
       lus dans la checklist `docs/commercial/checklist-tiitoune-art.md` (section 2),
       puis marque le prospect comme contacte dans le CRM de prospection.
COMMENT : SMTP direct (`smtplib`, TLS implicite sur 465, STARTTLS sinon). Les
       identifiants viennent de `address-validator/.env.smtp` (fichier ignore par
       Git, charge par `scripts/charger_env_smtp.py`), ou, a defaut, de
       l'environnement (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
       `SMTP_FROM_NAME`, plus `SMTP_FROM` et `SMTP_REPLY_TO` facultatifs). Le mot
       de passe n'est jamais affiche, jamais ecrit sur disque, jamais passe en
       argument de ligne de commande.
QUAND : le jour J, apres le controle des 5 liens (checklist section 1) ; une fois.
       Rejouer `--send` sur un prospect deja contacte est refuse, sauf `--renvoyer`.
OU : ce fichier (`scripts/`), `.env.smtp` a la racine, la checklist citee, et la
       base de prospection (`prospection/data/prospects.db`, fiche du prospect).

    .venv\\Scripts\\python.exe scripts\\envoyer_email_tiitoune.py                     # simulation
    .venv\\Scripts\\python.exe scripts\\envoyer_email_tiitoune.py --verifier-liens
    .venv\\Scripts\\python.exe scripts\\envoyer_email_tiitoune.py --test --vers moi@kder-ai.com
    .venv\\Scripts\\python.exe scripts\\envoyer_email_tiitoune.py --send

    .venv\\Scripts\\python.exe scripts\\test_smtp_connection.py    # controle de la connexion, aucun email

Codes de retour : 0 = message parti (ou simulation reussie), 1 = envoi impossible
(configuration, base ou serveur SMTP), 2 = usage invalide.
"""

from __future__ import annotations

import argparse
import os
import re
import smtplib
import ssl
import sqlite3
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:  # `scripts/` n'est pas un paquet : import direct
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT / "prospection" / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "prospection" / "scripts"))

try:  # regles et acces du module de prospection (base, CRM, horodatage)
    import common as prospection  # noqa: E402
except ImportError:  # pragma: no cover - depend de l'installation du module
    prospection = None  # type: ignore[assignment]

try:  # la liste des domaines d'expediteur refuses ne vit qu'a un seul endroit
    from envoyer_smtp import DOMAINES_EXPEDITEUR_REFUSES  # noqa: E402
except ImportError:  # pragma: no cover - repli explicite, jamais silencieux
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

try:  # identifiants SMTP du poste : fichier ignore par Git, quand il existe
    from charger_env_smtp import (
        FICHIER_DEFAUT as FICHIER_ENV_SMTP,
        MARQUEUR_A_REMPLACER,
        charger_env_smtp,
    )
except ImportError:  # pragma: no cover - repli explicite, jamais silencieux
    FICHIER_ENV_SMTP = ROOT / ".env.smtp"
    MARQUEUR_A_REMPLACER = "REMPLACER"
    charger_env_smtp = None  # type: ignore[assignment]

#: Checklist d'envoi : source unique de l'objet, du pre-en-tete et du corps.
CHECKLIST = ROOT / "docs" / "commercial" / "checklist-tiitoune-art.md"
#: Exemplaire de reference, compare au texte de la checklist (garde-fou D49).
BIENVENUE = ROOT / "EMAIL_BIENVENUE.md"
#: Base du CRM de prospection (le fichier n'est jamais modifie par la simulation).
DB_DEFAUT = ROOT / "prospection" / "data" / "prospects.db"

#: Boutique dont on envoie l'email de bienvenue (celle du prospect, pas du client).
BOUTIQUE = "Tiitoune Art"

#: Variables exigees au moment d'envoyer (une simulation n'en a pas besoin).
VARIABLES_OBLIGATOIRES = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD")
#: Poser `SMTP_USERNAME` au lieu de `SMTP_USER` est tolere : c'est le nom deja
#: utilise sur ce poste, et refuser un envoi pour un « s » de trop serait absurde.
REPLI_UTILISATEUR = "SMTP_USERNAME"
VARIABLES_FACULTATIVES = ("SMTP_PORT", "SMTP_FROM", "SMTP_FROM_NAME", "SMTP_REPLY_TO")

PORT_DEFAUT = 587
NOM_EXPEDITEUR_DEFAUT = "Kder - Address Validator"

#: Liens dont depend l'email (checklist section 1). L'email ne contient aucune
#: piece jointe : si une page est absente, le client clique dans le vide.
LIENS_A_VERIFIER = (
    ("/pricing", 200),
    ("/signup", 200),
    ("/compte", 401),
    ("/validate-form", 200),
    ("/guide", 200),
)
BASE_LIENS = "https://kder-ai.com"


# --- L'email a envoyer (lu dans le document, jamais recopie) ----------------


def _entre_backticks(texte: str, etiquette: str) -> str:
    """Valeur entre backticks d'une ligne `Etiquette : `valeur`` (sinon chaine vide)."""
    trouve = re.search(rf"^{etiquette}\s*:\s*`([^`]+)`", texte, re.MULTILINE)
    return trouve.group(1).strip() if trouve else ""


def _blocs_texte(texte: str) -> list[str]:
    """Contenus des blocs ```text ... ``` d'un document, fins de ligne normalisees."""
    blocs = re.findall(r"```text\r?\n(.*?)```", texte, re.DOTALL)
    return [bloc.replace("\r\n", "\n").strip("\n") for bloc in blocs]


def corps_de_reference(chemin: Path | str = BIENVENUE) -> str:
    """Bloc ```text de l'exemplaire rempli (`EMAIL_BIENVENUE.md`), sinon chaine vide.

    Le gabarit d'envoi contient des variables de fusion (`{{prenom}}`) : c'est donc
    le bloc qui n'en contient aucune qui sert de reference.
    """
    fichier = Path(chemin)
    if not fichier.is_file():
        return ""
    for bloc in _blocs_texte(fichier.read_text(encoding="utf-8")):
        if "{{" not in bloc:
            return bloc
    return ""


def lire_email_exemplaire(chemin: Path | str = CHECKLIST) -> dict:
    """Lit l'email a envoyer dans la checklist (section 2).

    Le script n'ecrit pas le texte : il le relit. Une correction faite dans le
    document d'envoi change donc le message envoye, et le texte relu par
    l'exploitant ne peut pas differer de celui qui part.

    Args:
        chemin: document portant `Objet :`, `Pre-en-tete :` et un bloc ```text.

    Returns:
        `{"objet", "pre_en_tete", "corps", "source", "avertissements"}`.

    Raises:
        FileNotFoundError: document absent.
        ValueError: objet, pre-en-tete ou corps introuvable, ou corps contenant
            encore un `{placeholder}` (rien n'est devine ni complete).
    """
    fichier = Path(chemin)
    if not fichier.is_file():
        raise FileNotFoundError(f"document d'envoi absent : {fichier}")
    texte = fichier.read_text(encoding="utf-8")
    objet = _entre_backticks(texte, "Objet")
    pre_en_tete = _entre_backticks(texte, r"Pr(?:e|\u00e9)-en-t(?:e|\u00ea)te")
    blocs = _blocs_texte(texte)
    corps = blocs[0] if blocs else ""
    if not objet:
        raise ValueError(f"aucune ligne `Objet : ...` dans {fichier.name}")
    if not pre_en_tete:
        raise ValueError(f"aucune ligne `Pre-en-tete : ...` dans {fichier.name}")
    if not corps:
        raise ValueError(f"aucun bloc ```text ... ``` dans {fichier.name}")
    if re.search(r"\{\{[^}]+\}\}", corps):
        raise ValueError(f"le corps de {fichier.name} contient encore un placeholder")
    avertissements: list[str] = []
    reference = corps_de_reference()
    if reference and reference != corps:
        avertissements.append("corps different de EMAIL_BIENVENUE.md (section 2)")
    return {
        "objet": objet,
        "pre_en_tete": pre_en_tete,
        "corps": corps,
        "source": str(fichier),
        "avertissements": avertissements,
    }


# --- Le destinataire (CRM de prospection) -----------------------------------


def lire_fiche(
    destinataire: str | None = None,
    chemin_db: Path | str = DB_DEFAUT,
    boutique: str = BOUTIQUE,
) -> dict:
    """Fiche du prospect : lue dans le CRM, ou imposee par `--destinataire`.

    La base est ouverte en **lecture seule** (`mode=ro`) : afficher ce qui serait
    envoye ne doit pas modifier le fichier qu'on lit.

    Raises:
        FileNotFoundError: base absente et aucun destinataire impose.
        ValueError: aucune fiche, ou plusieurs fiches, pour cette boutique.
    """
    if destinataire:
        return {
            "id": None,
            "email": destinataire.strip().lower(),
            "nom_boutique": boutique,
            "statut": "hors_crm",
            "date_envoi": None,
            "plateforme": "",
            "site_web": "",
            "source": "option --destinataire",
        }
    fichier = Path(chemin_db)
    if not fichier.is_file():
        raise FileNotFoundError(
            f"base absente : {fichier} - importer la fiche d'abord, ou passer --destinataire"
        )
    base = sqlite3.connect(f"file:{fichier.as_posix()}?mode=ro", uri=True)
    base.row_factory = sqlite3.Row
    try:
        lignes = base.execute(
            "SELECT id, email, nom_boutique, statut, date_envoi, plateforme, site_web, source"
            " FROM prospects WHERE nom_boutique LIKE ? ORDER BY id",
            (f"{boutique}%",),
        ).fetchall()
    finally:
        base.close()
    if not lignes:
        raise ValueError(f"aucune fiche « {boutique} » dans {fichier.name} : importer avant d'envoyer")
    if len(lignes) > 1:
        identifiants = ", ".join(str(ligne["id"]) for ligne in lignes)
        raise ValueError(f"{len(lignes)} fiches « {boutique} » en base (id {identifiants}) : trancher a la main")
    return dict(lignes[0])


# --- Identifiants SMTP (.env.smtp, sinon environnement) ---------------------


def preparer_environnement(chemin: Path | str = FICHIER_ENV_SMTP) -> dict:
    """Fait vivre `.env.smtp` dans l'environnement, et dit ce qu'il faut en dire.

    Le fichier n'est pas obligatoire : sans lui, la suite lit l'environnement
    exactement comme avant (docs/commercial/GUIDE-BREVO.md, section 4). S'il est
    la, sa valeur fait foi ; un texte encore marque « REMPLACER » n'est jamais pose
    dans l'environnement et bloque l'envoi reel.

    Args:
        chemin: fichier des identifiants (defaut : `.env.smtp`, a la racine).

    Returns:
        `{"trouve", "chemin", "messages", "a_remplacer"}` : lignes a afficher, et
        noms des variables encore a remplacer (liste vide = rien ne bloque).
    """
    if charger_env_smtp is None:  # pragma: no cover - module versionne avec ce script
        return {
            "trouve": False,
            "chemin": Path(chemin),
            "messages": [
                "charger_env_smtp.py absent : identifiants a poser dans l'environnement"
            ],
            "a_remplacer": [],
        }
    try:
        rapport = charger_env_smtp(chemin)
    except (OSError, ValueError) as erreur:
        return {
            "trouve": False,
            "chemin": Path(chemin),
            "messages": [f"{Path(chemin).name} illisible : {erreur}"],
            "a_remplacer": [],
        }

    messages: list[str] = []
    if not rapport["trouve"]:
        messages.append(
            f"{Path(chemin).name} absent - Creez .env.smtp avec vos identifiants "
            "(modele : docs/commercial/GUIDE-BREVO.md, section 4) ; les variables "
            "SMTP_* de l'environnement restent utilisees"
        )
    else:
        messages.append(f"{Path(chemin).name} charge ({len(rapport['valeurs'])} variables)")
        if rapport["ecrasees"]:
            messages.append(
                "fichier prioritaire sur l'environnement : " + ", ".join(rapport["ecrasees"])
            )
        messages.extend(rapport["problemes"])
    a_remplacer = [
        nom
        for nom, valeur in rapport["valeurs"].items()
        if MARQUEUR_A_REMPLACER in str(valeur).upper()
    ]
    return {
        "trouve": rapport["trouve"],
        "chemin": Path(chemin),
        "messages": messages,
        "a_remplacer": a_remplacer,
    }


def lire_identifiants(environ: dict | None = None) -> dict:
    """Lit la configuration SMTP dans l'environnement, et la valide.

    Le mot de passe ne peut venir que d'une variable d'environnement : un argument
    de ligne de commande finit dans l'historique du terminal, un fichier du depot
    finit dans Git.

    Args:
        environ: mapping a interroger ; par defaut, `os.environ`.

    Returns:
        `{"hote", "port", "utilisateur", "mot_de_passe", "expediteur",
        "expediteur_nom", "repondre_a", "securise"}`.

    Raises:
        ValueError: variable exigee absente, ou `SMTP_PORT` illisible.
    """
    source = os.environ if environ is None else environ
    utilisateur = str(source.get("SMTP_USER") or source.get(REPLI_UTILISATEUR) or "").strip()
    valeurs = {
        "hote": str(source.get("SMTP_HOST") or "").strip(),
        "utilisateur": utilisateur,
        "mot_de_passe": str(source.get("SMTP_PASSWORD") or ""),
    }
    noms = {
        "hote": "SMTP_HOST",
        "utilisateur": "SMTP_USER (ou SMTP_USERNAME)",
        "mot_de_passe": "SMTP_PASSWORD",
    }
    manquantes = [noms[cle] for cle, valeur in valeurs.items() if not valeur.strip()]
    if manquantes:
        raise ValueError("configuration SMTP absente de l'environnement : " + ", ".join(manquantes))
    brut = str(source.get("SMTP_PORT") or PORT_DEFAUT).strip()
    try:
        port = int(brut)
    except ValueError as erreur:
        raise ValueError(f"SMTP_PORT illisible : {brut!r}") from erreur
    expediteur = str(source.get("SMTP_FROM") or utilisateur).strip()
    return {
        "hote": valeurs["hote"],
        "port": port,
        "utilisateur": utilisateur,
        "mot_de_passe": valeurs["mot_de_passe"],
        "expediteur": expediteur,
        "expediteur_nom": str(source.get("SMTP_FROM_NAME") or NOM_EXPEDITEUR_DEFAUT).strip(),
        "repondre_a": str(source.get("SMTP_REPLY_TO") or "").strip(),
        "securise": port == 465,
    }


def resume_identifiants(identifiants: dict) -> list[tuple[str, str]]:
    """Ce qu'on peut montrer d'une configuration SMTP sans rien exposer.

    Le mot de passe n'est jamais rendu, meme masque : la seule chose utile a
    savoir avant de cliquer est qu'il est present.
    """
    return [
        ("serveur", f"{identifiants['hote']}:{identifiants['port']}"),
        ("chiffrement", "TLS implicite (465)" if identifiants["securise"] else "STARTTLS"),
        ("utilisateur", identifiants["utilisateur"]),
        ("expediteur", f"{identifiants['expediteur_nom']} <{identifiants['expediteur']}>"),
        ("repondre a", identifiants["repondre_a"] or "(= expediteur)"),
        ("mot de passe", "present (jamais affiche)" if identifiants["mot_de_passe"] else "ABSENT"),
    ]


def verifier_expediteur(identifiants: dict, accepter_domaine_public: bool) -> None:
    """Refuse un expediteur grand public : la delivrabilite du domaine y perdrait.

    Raises:
        ValueError: domaine de l'expediteur dans `DOMAINES_EXPEDITEUR_REFUSES`.
    """
    domaine = identifiants["expediteur"].split("@")[-1].lower()
    if domaine in DOMAINES_EXPEDITEUR_REFUSES and not accepter_domaine_public:
        raise ValueError(
            f"expediteur grand public refuse ({domaine}) : envoyer depuis une boite "
            "personnelle abime la delivrabilite du domaine. Poser SMTP_FROM (et "
            "SMTP_USER) sur une adresse du domaine, ou assumer avec "
            "--accepter-domaine-public"
        )


# --- Le message et sa remise ------------------------------------------------


def construire_message(
    objet: str,
    corps: str,
    pre_en_tete: str,
    identifiants: dict,
    destinataire: str,
    pre_en_tete_dans_le_corps: bool = False,
) -> EmailMessage:
    """Assemble un message RFC 5322 complet, en texte brut UTF-8 uniquement.

    Le pre-en-tete est pose dans l'en-tete `X-Pre-Header` ; il ne rejoint le corps
    que si on le demande (`--pre-en-tete-corps`), pour que le texte envoye reste,
    par defaut, mot pour mot celui que l'exploitant a relu dans la checklist.
    """
    message = EmailMessage()
    message["From"] = formataddr((identifiants["expediteur_nom"], identifiants["expediteur"]))
    message["To"] = destinataire
    message["Subject"] = objet
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=identifiants["expediteur"].split("@")[-1])
    if identifiants["repondre_a"]:
        message["Reply-To"] = identifiants["repondre_a"]
    adresse_retrait = identifiants["repondre_a"] or identifiants["expediteur"]
    message["List-Unsubscribe"] = f"<mailto:{adresse_retrait}?subject=stop>"
    message["X-Pre-Header"] = pre_en_tete
    contenu = f"{pre_en_tete}\n\n{corps}" if pre_en_tete_dans_le_corps else corps
    message.set_content(contenu, charset="utf-8")
    return message


def envoyer_message(identifiants: dict, message: EmailMessage) -> None:
    """Remet le message au serveur SMTP, puis ferme la session.

    Raises:
        smtplib.SMTPAuthenticationError: identifiants refuses par le serveur.
        smtplib.SMTPException, OSError, ssl.SSLError: echec de remise ou reseau.
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
    try:
        serveur.login(identifiants["utilisateur"], identifiants["mot_de_passe"])
        serveur.send_message(message)
    finally:
        try:
            serveur.quit()
        except (smtplib.SMTPException, OSError):
            pass  # la session est de toute facon terminee


def verifier_liens(
    base: str = BASE_LIENS, delai: int = 15
) -> tuple[list[tuple[str, int, int]], list[str]]:
    """Mesure les pages dont l'email depend (checklist section 1).

    L'email ne contient aucune piece jointe : si une page manque, le destinataire
    clique dans le vide. Le controle est donc refait au moment de l'envoi, et non
    « une fois pour toutes ».

    Returns:
        `(mesures, problemes)` : `mesures` liste `(chemin, code, taille)`, et
        `problemes` les ecarts au code attendu ou les pages injoignables.
    """
    mesures: list[tuple[str, int, int]] = []
    problemes: list[str] = []
    for chemin, attendu in LIENS_A_VERIFIER:
        requete = urllib.request.Request(
            base + chemin, method="GET", headers={"User-Agent": "Kder-check/1.0"}
        )
        try:
            with urllib.request.urlopen(requete, timeout=delai) as reponse:
                code, taille = int(reponse.status), len(reponse.read())
        except urllib.error.HTTPError as erreur:
            code, taille = int(erreur.code), len(erreur.read() or b"")
        except (urllib.error.URLError, OSError) as erreur:
            problemes.append(f"{chemin} : injoignable ({erreur})")
            continue
        mesures.append((chemin, code, taille))
        if code != attendu:
            problemes.append(f"{chemin} : {code} au lieu de {attendu}")
    return mesures, problemes


def marquer_crm(chemin_db: Path | str, fiche: dict, objet: str) -> str:
    """Marque le prospect comme contacte, et consigne l'envoi dans le CRM.

    L'email est deja parti quand cette fonction tourne : un echec du CRM est
    annonce, jamais tu (c'est l'horodatage `date_envoi`, et non un souvenir, qui
    declenche la relance J+3).

    Returns:
        Un compte rendu d'une ligne, a afficher tel quel.
    """
    if prospection is None:
        return "CRM non mis a jour : module de prospection introuvable"
    if not fiche.get("id"):
        return "CRM non mis a jour : aucune fiche identifiee (pas d'id)"
    try:
        base = prospection.connexion(str(chemin_db), creer=False)
    except (FileNotFoundError, OSError) as erreur:
        return f"CRM non mis a jour : {erreur}"
    try:
        prospection.maj_prospect(
            base, int(fiche["id"]), statut="envoye", date_envoi=prospection.maintenant()
        )
        prospection.enregistrer_evenement(
            base,
            int(fiche["id"]),
            "intro",
            objet=objet,
            detail="smtp direct (envoyer_email_tiitoune.py)",
        )
        prospection.journaliser(
            base,
            "envoyer_email_tiitoune",
            "envoi",
            {"prospect": int(fiche["id"]), "email": fiche["email"], "objet": objet},
        )
    except (KeyError, ValueError, sqlite3.Error) as erreur:
        return f"CRM non mis a jour : {type(erreur).__name__}: {erreur}"
    finally:
        base.close()
    return f"CRM a jour : prospect {fiche['id']} passe en statut « envoye »"


# --- Ligne de commande ------------------------------------------------------


def parseur() -> argparse.ArgumentParser:
    """Les options du script : mode, destinataire, sources."""
    analyseur = argparse.ArgumentParser(
        description="Envoie l'email de bienvenue a Tiitoune Art (simulation par defaut).",
        epilog=(
            "Codes de retour : 0 reussi (ou simulation), 1 envoi impossible, "
            "2 usage invalide."
        ),
    )
    mode = analyseur.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="affiche le message sans rien envoyer, sans ouvrir de connexion (defaut)",
    )
    mode.add_argument(
        "--test",
        action="store_true",
        help="envoie une seule copie de test a --vers, sans toucher au CRM",
    )
    mode.add_argument(
        "--send",
        action="store_true",
        help="envoie au destinataire de la fiche et met le CRM a jour",
    )
    analyseur.add_argument("--vers", help="adresse de l'essai (mode --test ; defaut : SMTP_USER)")
    analyseur.add_argument(
        "--destinataire",
        help="adresse imposee, hors CRM (a utiliser avec --dry-run seulement)",
    )
    analyseur.add_argument("--db", default=str(DB_DEFAUT), help=f"base du CRM (defaut : {DB_DEFAUT})")
    analyseur.add_argument(
        "--checklist",
        default=str(CHECKLIST),
        help="document d'envoi (objet, pre-en-tete, corps)",
    )
    analyseur.add_argument(
        "--verifier-liens",
        action="store_true",
        help="mesure les 5 pages dont l'email depend avant d'envoyer",
    )
    analyseur.add_argument(
        "--pre-en-tete-corps",
        action="store_true",
        help="insere le pre-en-tete en premiere ligne du corps",
    )
    analyseur.add_argument(
        "--renvoyer",
        action="store_true",
        help="autorise l'envoi a un prospect deja contacte",
    )
    analyseur.add_argument(
        "--accepter-domaine-public",
        action="store_true",
        help="autorise un expediteur gmail/... (deconseille : delivrabilite)",
    )
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entree : 0 reussi (ou simulation), 1 envoi impossible, 2 usage invalide."""
    args = parseur().parse_args(argv)
    if not (args.dry_run or args.test or args.send):
        args.dry_run = True  # la simulation est le mode par defaut
    if args.vers and not args.test:
        print("USAGE FAIL --vers ne sert qu'avec --test")
        return 2
    if args.destinataire and not args.dry_run:
        print("USAGE FAIL --destinataire ne sert qu'avec --dry-run : --test prend --vers, --send passe par le CRM")
        return 2

    print("=" * 72)
    print(f"Envoi de l'email de bienvenue - {BOUTIQUE}")
    print("=" * 72)

    environnement = preparer_environnement()
    for message in environnement["messages"]:
        print(f"{'env SMTP':<18}: {message}")

    try:
        email = lire_email_exemplaire(args.checklist)
    except (FileNotFoundError, ValueError) as erreur:
        print(f"ENVOI FAIL {erreur}")
        return 1
    try:
        fiche = lire_fiche(args.destinataire, args.db)
    except (FileNotFoundError, ValueError) as erreur:
        if not args.test:  # un essai vers soi-meme reste possible sans CRM
            print(f"ENVOI FAIL {erreur}")
            return 1
        print(f"AVERTISSEMENT     fiche CRM illisible ({erreur}) - essai possible quand meme")
        fiche = {
            "id": None,
            "email": "(essai)",
            "nom_boutique": BOUTIQUE,
            "statut": "inconnu",
            "date_envoi": None,
            "plateforme": "",
            "site_web": "",
            "source": "aucune",
        }

    print(f"texte de l'email  : {email['source']}")
    print(f"destinataire      : {fiche['email']}  (boutique « {fiche['nom_boutique']} », fiche {fiche['id']})")
    print(f"statut en base    : {fiche['statut']} / date_envoi={fiche['date_envoi'] or 'aucune'}")
    print(f"objet             : {email['objet']}")
    print(f"pre-en-tete       : {email['pre_en_tete']}")
    print(f"corps             : {len(email['corps'].splitlines())} lignes, {len(email['corps'])} caracteres")
    for avertissement in email["avertissements"]:
        print(f"AVERTISSEMENT     : {avertissement}")

    if args.verifier_liens:
        mesures, problemes = verifier_liens()
        print("les 5 liens de l'email :")
        for chemin, code, taille in mesures:
            print(f"  {chemin:<15} {code}  {taille} o")
        for probleme in problemes:
            print(f"  PROBLEME {probleme}")
        if problemes and not args.dry_run:
            print("ENVOI FAIL les pages de l'email ne repondent pas comme attendu")
            return 1

    if args.dry_run:
        print("-" * 72)
        print(email["corps"])
        print("-" * 72)
        print("SIMULATION : aucun message envoye, aucune connexion ouverte, CRM inchange.")
        print("  essai reel vers soi-meme : --test --vers <adresse>")
        print("  envoi au prospect        : --send")
        return 0

    # A partir d'ici, un message part reellement.
    if environnement["a_remplacer"]:
        print(
            "ENVOI FAIL .env.smtp contient encore un texte a remplacer ("
            + ", ".join(environnement["a_remplacer"])
            + ") : poser la vraie cle SMTP Brevo, puis relancer"
        )
        return 1
    try:
        identifiants = lire_identifiants()
        verifier_expediteur(identifiants, args.accepter_domaine_public)
    except ValueError as erreur:
        print(f"ENVOI FAIL {erreur}")
        return 1
    print("configuration SMTP :")
    for etiquette, valeur in resume_identifiants(identifiants):
        print(f"  {etiquette:<15} {valeur}")

    if args.test:
        destinataire = args.vers or identifiants["utilisateur"]
        objet = f"[TEST] {email['objet']}"
        print(f"ESSAI : une seule copie a {destinataire} - le CRM ne sera pas modifie")
    else:
        destinataire = fiche["email"]
        objet = email["objet"]
        if fiche["date_envoi"] and not args.renvoyer:
            print(
                f"ENVOI FAIL {destinataire} a deja recu un email le {fiche['date_envoi']} "
                "(--renvoyer pour passer outre, en connaissance de cause)"
            )
            return 1

    message = construire_message(
        objet=objet,
        corps=email["corps"],
        pre_en_tete=email["pre_en_tete"],
        identifiants=identifiants,
        destinataire=destinataire,
        pre_en_tete_dans_le_corps=args.pre_en_tete_corps,
    )
    try:
        envoyer_message(identifiants, message)
    except smtplib.SMTPAuthenticationError as erreur:
        print(
            f"ENVOI FAIL authentification refusee par {identifiants['hote']} "
            f"(code {erreur.smtp_code})"
        )
        return 1
    except (smtplib.SMTPException, OSError, ssl.SSLError) as erreur:
        print(f"ENVOI FAIL {type(erreur).__name__}: {erreur}")
        return 1

    print(f'ENVOI OK destinataire={destinataire} objet="{objet}"')
    if args.test:
        print("  essai : le CRM n'est pas modifie (aucun statut, aucun horodatage).")
        return 0
    print("  " + marquer_crm(args.db, fiche, objet))
    print("  suite : J+3 relance si aucune reponse, J+7 appel (checklist section 5).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
