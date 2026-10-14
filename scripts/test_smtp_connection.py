#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Teste la connexion SMTP Brevo, sans envoyer d'email.

QUI   : l'exploitant, a la main, juste apres avoir rempli `address-validator/.env.smtp`.
QUOI  : verifie que le fichier porte les six variables attendues, que la cle SMTP
        n'est plus un texte a remplacer, puis ouvre une session sur le relais
        Brevo, negocie STARTTLS et s'authentifie. Aucun message n'est envoye.
COMMENT : `smtplib` (STARTTLS en 587, TLS implicite en 465) et l'analyseur maison
        `scripts/charger_env_smtp.py` : rien a installer. La cle SMTP n'est jamais
        affichee, ni ecrite sur disque, ni passee en argument de ligne de commande.
QUAND : des que la cle SMTP est posee dans `.env.smtp`, avant la premiere campagne
        (envoyer_email_tiitoune.py --test), et apres tout changement de cle Brevo.
OU    : scripts/test_smtp_connection.py, lance depuis la racine du depot :
        .venv\\Scripts\\python.exe scripts\\test_smtp_connection.py
        .venv\\Scripts\\python.exe scripts\\test_smtp_connection.py --fichier .env.smtp
FAIT QUOI EXACTEMENT : affiche une ligne de contexte (fichier, serveur, chiffrement,
        utilisateur, mot de passe present ou non), puis

        ✅ Configuration SMTP valide   + login, hote, port
        ✅ Pret a envoyer des emails

        ou

        ❌ Erreur : <ce qui bloque, en clair>, suivi du texte du serveur tel quel :
        le code 5xx seul ne suffit pas a diagnostiquer (un `525 5.7.1 Unauthorized
        IP address` designe l'adresse IP de la machine, pas la cle SMTP : voir le
        rappel affiche dans ce cas).

Codes de retour : 0 configuration valide et serveur joignable, 1 echec (fichier
absent, variable a remplacer, serveur ou authentification refuses), 2 usage invalide.
"""

from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
from pathlib import Path
from typing import Sequence

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:  # `scripts/` n'est pas un paquet : import direct
    sys.path.insert(0, str(SCRIPTS))

from charger_env_smtp import (  # noqa: E402
    FICHIER_DEFAUT,
    VARIABLES,
    charger_env_smtp,
)

#: Relais d'envoi Brevo, tel que la documentation officielle le publie.
HOTE_BREVO = "smtp-relay.brevo.com"
#: 587 = STARTTLS, 465 = TLS implicite (docs/commercial/GUIDE-BREVO.md, section 5).
PORT_BREVO = 587
#: Delai de connexion, en secondes.
DELAI_DEFAUT = 20.0


def _forcer_utf8() -> None:
    """Affiche les marques ✅/❌ meme dans une console Windows restee en cp1252."""
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")


def tester_connexion(identifiants: dict, delai: float = DELAI_DEFAUT) -> None:
    """Ouvre la session, s'authentifie, puis ferme : aucun message n'est envoye.

    Le `noop()` final est la preuve que la session reste utilisable **apres**
    l'authentification : un compte suspendu ou un envoi bloque s'y voient.

    Args:
        identifiants: `{"hote", "port", "utilisateur", "mot_de_passe"}`.
        delai: delai de connexion, en secondes.

    Raises:
        smtplib.SMTPAuthenticationError: identifiants refuses par Brevo.
        smtplib.SMTPException, OSError, ssl.SSLError: serveur injoignable, TLS refuse.
    """
    contexte = ssl.create_default_context()
    if identifiants["port"] == 465:  # TLS implicite, pas de STARTTLS
        serveur = smtplib.SMTP_SSL(
            identifiants["hote"], identifiants["port"], timeout=delai, context=contexte
        )
    else:
        serveur = smtplib.SMTP(identifiants["hote"], identifiants["port"], timeout=delai)
    with serveur:
        if identifiants["port"] != 465:
            serveur.starttls(context=contexte)
            serveur.ehlo()  # apres STARTTLS, la session se represente
        serveur.login(identifiants["utilisateur"], identifiants["mot_de_passe"])
        serveur.noop()


def port_depuis(valeur: str) -> int:
    """Port SMTP lu dans le fichier ; `ValueError` lisible si ce n'est pas un nombre.

    Raises:
        ValueError: valeur absente ou non numerique.
    """
    try:
        return int(str(valeur).strip())
    except (TypeError, ValueError) as erreur:
        raise ValueError(f"SMTP_PORT illisible : {valeur!r}") from erreur


def texte_serveur(erreur: smtplib.SMTPAuthenticationError) -> str:
    """Texte joint par le serveur au refus, tel quel (`5.7.1 Unauthorized IP address`).

    `smtplib` le conserve dans `smtp_error` : l'afficher est le seul moyen de
    distinguer une cle ou un login refuses (`535 5.7.8 Authentication failed`)
    d'une **adresse IP non autorisee** (`525 5.7.1 Unauthorized IP address`). Ces
    deux refus arrivent avec un code 5xx et un conseil different.
    """
    brut = erreur.smtp_error
    if isinstance(brut, (bytes, bytearray)):
        brut = brut.decode("utf-8", "replace")
    return str(brut).strip()


def parseur() -> argparse.ArgumentParser:
    """Ligne de commande : quel fichier d'identifiants, quel delai."""
    analyseur = argparse.ArgumentParser(
        description="Teste la connexion SMTP Brevo, sans envoyer d'email."
    )
    analyseur.add_argument(
        "--fichier",
        default=str(FICHIER_DEFAUT),
        help=f"fichier des identifiants SMTP (defaut : {FICHIER_DEFAUT})",
    )
    analyseur.add_argument(
        "--delai",
        type=float,
        default=DELAI_DEFAUT,
        help=f"delai de connexion, en secondes (defaut : {DELAI_DEFAUT:g})",
    )
    return analyseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entree : 0 configuration valide, 1 echec, 2 usage invalide."""
    _forcer_utf8()
    args = parseur().parse_args(argv)
    print("=" * 72)
    print("Test de la connexion SMTP Brevo (aucun email envoye)")
    print("=" * 72)

    try:
        rapport = charger_env_smtp(args.fichier)
    except ValueError as erreur:
        print(f"❌ Erreur : {erreur}")
        return 1

    print(f"fichier           : {rapport['fichier']}")
    if not rapport["trouve"]:
        print(f"❌ Erreur : {rapport['fichier']} introuvable — creez .env.smtp avec :")
        for nom in VARIABLES:
            print(f"     {nom}=...")
        print("   modele et explications : docs/commercial/GUIDE-BREVO.md, section 4")
        return 1
    for nom in rapport["ecrasees"]:
        print(f"  note : {nom} existait dans l'environnement, la valeur du fichier fait foi")
    if rapport["problemes"]:
        for probleme in rapport["problemes"]:
            print(f"❌ Erreur : {probleme}")
        print(f"   ouvrir {rapport['fichier']} et remplacer la valeur marquee, puis relancer.")
        return 1

    valeurs = rapport["valeurs"]
    try:
        port = port_depuis(valeurs["SMTP_PORT"])
    except ValueError as erreur:
        print(f"❌ Erreur : {erreur}")
        return 1
    identifiants = {
        "hote": valeurs["SMTP_HOST"].strip(),
        "port": port,
        "utilisateur": valeurs["SMTP_USER"].strip(),
        "mot_de_passe": valeurs["SMTP_PASSWORD"],
    }
    if identifiants["hote"] != HOTE_BREVO or port != PORT_BREVO:
        print(
            f"⚠️  le fichier ne vise pas le relais Brevo attendu "
            f"({HOTE_BREVO}:{PORT_BREVO}) : c'est lui qui fait foi, on continue."
        )
    print(f"serveur           : {identifiants['hote']}:{port}")
    print(f"chiffrement       : {'TLS implicite' if port == 465 else 'STARTTLS'}")
    print(f"utilisateur       : {identifiants['utilisateur']}")
    print(f"mot de passe      : {'present (jamais affiche)'}")

    try:
        tester_connexion(identifiants, args.delai)
    except smtplib.SMTPAuthenticationError as erreur:
        detail = texte_serveur(erreur)
        print(
            f"❌ Erreur : authentification refusee par {identifiants['hote']} "
            f"(code {erreur.smtp_code}{' — ' + detail if detail else ''})"
        )
        if "unauthorized ip" in detail.lower():
            print("   Brevo reconnait la cle mais refuse l'adresse IP de cette machine :")
            print("   app.brevo.com -> Settings -> Security -> Authorized IP addresses.")
            print("   y ajouter l'IP publique de la machine (https://api.ipify.org), ou")
            print("   retirer la restriction si l'acces part d'une IP dynamique.")
        else:
            print("   verifier la cle SMTP : onglet SMTP de Brevo, cle xsmtpsib-... —")
            print("   la cle API xkeysib-... ne fonctionne pas en SMTP. Le login est")
            print(
                f"   {identifiants['utilisateur']} (...@smtp-brevo.com), "
                "pas l'adresse du domaine."
            )
        return 1
    except (smtplib.SMTPException, OSError, ssl.SSLError) as erreur:
        print(f"❌ Erreur : {type(erreur).__name__} : {erreur}")
        return 1

    print("✅ Configuration SMTP valide")
    print(f"     login : {identifiants['utilisateur']}")
    print(f"     hote  : {identifiants['hote']}")
    print(f"     port  : {port}")
    print("✅ Prêt à envoyer des emails")
    print(
        "   essai reel : .venv\\Scripts\\python.exe scripts\\envoyer_email_tiitoune.py "
        "--test --vers address-validator@kder-ai.com"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
