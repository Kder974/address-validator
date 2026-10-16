#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Teste la connexion SMTP **depuis le serveur**, et l'envoi d'un vrai message.

QUI   : l'exploitant, sur le VPS, apres avoir pose les identifiants SMTP du service
        (voir DEPLOIEMENT_VPS.md, section 12).
QUOI  : ouvre une session sur le relais (STARTTLS en 587, TLS implicite en 465),
        s'authentifie, verifie la session par un `noop()`, et — seulement si on le
        demande — envoie un message de test. C'est le pendant cote serveur de
        `scripts/test_smtp_connection.py`, qui teste le poste.
COMMENT : `smtplib` et `ssl` uniquement. Le mot de passe n'est jamais affiche, jamais
        ecrit, jamais passe en argument de ligne de commande : il est lu dans un
        fichier deja depose sur le serveur.
QUAND : apres avoir modifie le `.env` du service, apres tout changement de cle Brevo, et
        chaque fois qu'un client signale qu'il ne recoit pas son mail.
OU    : `address-validator/scripts/test_smtp_vps.py`, copie sur le serveur puis lance
        dans le venv du service :

        scp -i ~/.ssh/address_validator_vps .env.smtp root@87.76.197.38:/tmp/smtp_brevo.env
        scp -i ~/.ssh/address_validator_vps scripts/test_smtp_vps.py root@87.76.197.38:/tmp/
        ssh -i ~/.ssh/address_validator_vps root@87.76.197.38 '
          chmod 600 /tmp/smtp_brevo.env
          /opt/address-validator/venv/bin/python /tmp/test_smtp_vps.py --fichier /tmp/smtp_brevo.env --envoi
          rm -f /tmp/smtp_brevo.env /tmp/test_smtp_vps.py'

FAIT QUOI EXACTEMENT : affiche fichier, serveur, chiffrement, utilisateur, presence du
        mot de passe et expediteur, puis

        OK : session ouverte et authentifiee (noop=250)
        OK : message de test accepte par le relais pour <destinataire>

        ou un echec en clair. Un `525 5.7.1 Unauthorized IP address` designe l'adresse
        IP de la machine, pas la cle (voir docs/commercial/GUIDE-BREVO.md, piege 10) :
        ce script le dit explicitement.

Codes de retour : 0 reussi, 1 echec (fichier absent, configuration incomplete, serveur
        ou authentification refuses).
"""

from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

#: Adresse qui recoit les messages de test (l'expediteur Brevo, lue dans Zoho).
DESTINATAIRE = "address-validator@kder-ai.com"
#: Fichier depose sur le serveur, au meme format que le `.env.smtp` du poste.
FICHIER_DEFAUT = "/tmp/smtp_brevo.env"


def lire_identifiants(chemin: Path) -> dict:
    """Rend les valeurs du fichier (les `\r` sont retires), sans rien journaliser."""
    valeurs: dict = {}
    for brute in chemin.read_text(encoding="utf-8", errors="replace").splitlines():
        ligne = brute.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        valeurs[cle.strip().upper()] = valeur.strip()
    return valeurs


def texte_serveur(erreur: Exception) -> str:
    """Le texte brut renvoye par le serveur, tel quel (indispensable pour trancher)."""
    detail = getattr(erreur, "smtp_error", b"")
    if isinstance(detail, bytes):
        return detail.decode("utf-8", "replace")
    return str(detail or erreur)


def main() -> int:
    parseur = argparse.ArgumentParser(
        description="Teste la connexion SMTP du serveur, et l'envoi d'un message de test."
    )
    parseur.add_argument("--fichier", default=FICHIER_DEFAUT, help="fichier d'identifiants")
    parseur.add_argument("--envoi", action="store_true", help="envoie un vrai message de test")
    parseur.add_argument("--delai", type=float, default=20.0, help="delai de connexion, en secondes")
    args = parseur.parse_args()

    chemin = Path(args.fichier)
    if not chemin.exists():
        print(f"ECHEC : {chemin} absent — deposer le .env.smtp du poste a cet endroit.")
        return 1

    valeurs = lire_identifiants(chemin)
    hote = valeurs.get("SMTP_HOST", "")
    port = int(valeurs.get("SMTP_PORT") or 587)
    utilisateur = valeurs.get("SMTP_USER", "")
    secret = valeurs.get("SMTP_PASSWORD", "")
    expediteur = valeurs.get("SMTP_FROM") or valeurs.get("CONTACT_EMAIL", "")

    print(f"fichier      : {chemin}")
    print(f"serveur      : {hote}:{port} ({'TLS implicite' if port == 465 else 'STARTTLS'})")
    print(f"utilisateur  : {utilisateur}")
    print(f"mot de passe : {'present, ' + str(len(secret)) + ' caracteres (jamais affiche)' if secret else 'ABSENT'}")
    print(f"expediteur   : {expediteur}")

    if not hote or not utilisateur or not secret:
        print("ECHEC : configuration incomplete (SMTP_HOST, SMTP_USER et SMTP_PASSWORD sont requis).")
        return 1

    contexte = ssl.create_default_context()
    try:
        if port == 465:  # TLS implicite, pas de STARTTLS
            serveur = smtplib.SMTP_SSL(hote, port, context=contexte, timeout=args.delai)
        else:
            serveur = smtplib.SMTP(hote, port, timeout=args.delai)
            serveur.starttls(context=contexte)
        with serveur:
            serveur.login(utilisateur, secret)
            # `noop()` est la preuve que la session reste utilisable apres l'authentification.
            code, _ = serveur.noop()
            print(f"OK : session ouverte et authentifiee (noop={code})")
            if args.envoi:
                message = EmailMessage()
                message["From"] = expediteur
                message["To"] = DESTINATAIRE
                message["Subject"] = "Test SMTP depuis le VPS - visite du 16/10/2026"
                message.set_content(
                    "Message de test envoye depuis la machine 87.76.197.38 avec les\n"
                    "identifiants SMTP Brevo du compte Address Validator.\n\n"
                    "Si vous lisez ceci dans la boite Zoho, la chaine SMTP du VPS\n"
                    "fonctionne : Brevo accepte la cle ET l'adresse IP du serveur.\n"
                )
                serveur.send_message(message)
                print(f"OK : message de test accepte par le relais pour {DESTINATAIRE}")
    except smtplib.SMTPAuthenticationError as erreur:
        detail = texte_serveur(erreur)
        print(f"ECHEC : authentification refusee (code {erreur.smtp_code}) — {detail}")
        if "unauthorized ip" in detail.lower():
            print("  -> 525 : Brevo connait la cle mais refuse CETTE adresse IP.")
            print("     app.brevo.com -> Settings -> Security -> Authorized IP addresses :")
            print("     y ajouter 87.76.197.38, ou retirer la restriction.")
        else:
            print("  -> verifier le couple login/cle : onglet SMTP de Brevo (xsmtpsib-...),")
            print("     et non la cle API (xkeysib-...).")
        return 1
    except (smtplib.SMTPException, OSError, ssl.SSLError) as erreur:
        print(f"ECHEC : {type(erreur).__name__} : {erreur}")
        return 1

    print("FIN : test termine sans erreur")
    return 0


if __name__ == "__main__":
    sys.exit(main())
