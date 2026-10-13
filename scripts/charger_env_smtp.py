#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Charge les identifiants SMTP de `address-validator/.env.smtp` (Brevo).

QUI   : `scripts/test_smtp_connection.py` (controle de la connexion) et
        `scripts/envoyer_email_tiitoune.py` (envoi de l'email de bienvenue).
QUOI  : lit le fichier `.env.smtp` de la racine du depot, verifie qu'il porte les
        six variables attendues, et les fait vivre dans `os.environ` pour le
        script appelant.
COMMENT : analyseur minimal, ligne par ligne : lignes vides et commentaires (`#`)
        ignores, `CLE=VALEUR` decoupe au premier `=`, guillemets retirees, `export `
        de shell tolere. Aucune dependance externe : `python-dotenv` est dans
        `requirements.txt`, mais le fichier est connu et trois lignes suffisent —
        autant ne pas dependre du nom de son API.
QUAND : au demarrage des deux scripts ci-dessus, une fois par execution.
OU    : `address-validator/.env.smtp` (ignore par `.gitignore`), lu depuis
        `address-validator/scripts/charger_env_smtp.py`.
FAIT QUOI EXACTEMENT : rend un rapport exploitable par l'appelant

        {"fichier", "trouve", "valeurs", "ecrasees", "problemes"}

        * `trouve`    : le fichier existe et a ete lu ;
        * `valeurs`   : les couples lus dans le fichier ;
        * `ecrasees`  : noms des variables dont la valeur d'environnement a ete
                        remplacee par celle du fichier — rien n'est fait en
                        silence, l'appelant peut le dire a l'exploitant ;
        * `problemes` : variable attendue absente, vide, ou encore marquee
                        `REMPLACER`. Un marqueur n'est **jamais** pose dans
                        l'environnement : ecrire un texte a remplacer a la place
                        d'une cle ferait echouer l'authentification avec un message
                        incomprehensible.

Le fichier porte un secret : ce module ne l'affiche jamais et n'ecrit rien sur
disque. Il ne prend pas la main tout seul (pas de `main()`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

#: Racine du depot (`address-validator/`), deduite de l'emplacement de ce module.
ROOT = Path(__file__).resolve().parents[1]

#: Fichier des identifiants SMTP : ignore par Git, jamais committe.
FICHIER_DEFAUT = ROOT / ".env.smtp"

#: Les six variables que le fichier doit porter (dans l'ordre du modele).
VARIABLES = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_FROM_NAME",
)

#: Fragment d'une valeur encore a remplir par l'exploitant (voir `.env.smtp`).
MARQUEUR_A_REMPLACER = "REMPLACER"


def est_un_marqueur(valeur: str) -> bool:
    """Vrai si la valeur est encore un texte a remplacer, pas un identifiant."""
    return MARQUEUR_A_REMPLACER in str(valeur).upper()


def lire_fichier_env(chemin: Path | str = FICHIER_DEFAUT) -> dict[str, str]:
    """Analyse un fichier `CLE=VALEUR` et rend les couples trouves.

    Args:
        chemin: fichier a lire (defaut : `.env.smtp`, a la racine du depot).

    Returns:
        Dictionnaire des couples, dans l'ordre du fichier (le dernier gagne).

    Raises:
        FileNotFoundError: fichier absent.
        ValueError: fichier illisible, ou ligne sans `CLE=VALEUR`.
    """
    fichier = Path(chemin)
    texte = None
    for encodage in ("utf-8-sig", "cp1252"):
        try:
            texte = fichier.read_text(encoding=encodage)
            break
        except UnicodeDecodeError:
            continue  # reenregistrement par le Bloc-notes en ANSI : on retente
    if texte is None:
        raise ValueError(f"{fichier.name} illisible : reenregistrer le fichier en UTF-8")

    valeurs: dict[str, str] = {}
    for numero, brute in enumerate(texte.splitlines(), start=1):
        ligne = brute.strip()
        if not ligne or ligne.startswith("#"):
            continue
        if ligne.startswith("export "):
            ligne = ligne[len("export ") :].lstrip()
        nom, separateur, valeur = ligne.partition("=")
        nom = nom.strip()
        if not separateur or not nom:
            raise ValueError(
                f"{fichier.name} ligne {numero} : ligne sans « CLE=VALEUR » — {brute.strip()!r}"
            )
        valeur = valeur.strip()
        if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
            valeur = valeur[1:-1]  # guillemets facultatifs autour de la valeur
        valeurs[nom] = valeur
    return valeurs


def problemes_variables(
    valeurs: dict[str, str], variables: Sequence[str] = VARIABLES
) -> list[str]:
    """Ce qui empeche d'utiliser la configuration : absente, vide, ou a remplacer.

    Le detail ne reprend jamais la valeur trouvee : un message d'erreur ne doit pas
    recopier un secret dans un journal ou une capture d'ecran.
    """
    constats: list[str] = []
    for nom in variables:
        valeur = str(valeurs.get(nom) or "").strip()
        if not valeur:
            constats.append(f"{nom} absente ou vide")
        elif est_un_marqueur(valeur):
            constats.append(
                f"{nom} contient encore un texte a remplacer "
                f"(marqueur {MARQUEUR_A_REMPLACER})"
            )
    return constats


def charger_env_smtp(
    chemin: Path | str = FICHIER_DEFAUT,
    environ: dict | None = None,
    ecraser: bool = True,
) -> dict:
    """Charge `.env.smtp` dans l'environnement, et rend ce qu'il faut en dire.

    Args:
        chemin: fichier des identifiants (defaut : `.env.smtp`).
        environ: mapping a remplir ; par defaut, `os.environ`.
        ecraser: `True` (defaut) la valeur du fichier fait foi — c'est la
            configuration declaree de cet expediteur. `False` : une variable deja
            posee dans l'environnement est conservee.

    Returns:
        `{"fichier", "trouve", "valeurs", "ecrasees", "problemes"}` (voir la
        docstring du module). Fichier absent : `trouve=False` et des `valeurs`
        vides, sans exception — l'appelant peut alors se rabattre sur
        l'environnement seul.

    Raises:
        ValueError: fichier illisible ou ligne malformee.
    """
    cible = os.environ if environ is None else environ
    fichier = Path(chemin)
    rapport = {
        "fichier": fichier,
        "trouve": False,
        "valeurs": {},
        "ecrasees": [],
        "problemes": [],
    }
    if not fichier.is_file():
        rapport["problemes"] = [f"{fichier.name} absent"]
        return rapport

    valeurs = lire_fichier_env(fichier)
    rapport["trouve"] = True
    rapport["valeurs"] = valeurs
    rapport["problemes"] = problemes_variables(valeurs)
    ecrasees: list[str] = []
    for nom, valeur in valeurs.items():
        if est_un_marqueur(valeur):
            continue  # jamais pose dans l'environnement (voir docstring du module)
        if nom in cible and cible[nom] != valeur:
            ecrasees.append(nom)
        if ecraser or nom not in cible:
            cible[nom] = valeur
    rapport["ecrasees"] = sorted(ecrasees)
    return rapport
