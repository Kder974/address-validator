#!/usr/bin/env python
"""Outils communs à la chaîne de prospection de l'API Address Validator.

Ce module n'est pas un script : c'est le socle partagé par
`import_prospects.py`, `generate_emails.py`, `track_responses.py`,
`analyze_results.py`, `export_campaign.py`, `sourcer_prospects.py` et
`verifier_prospection.py`. Il tient trois choses, et rien d'autre :

1. **Les chemins et les réglages.** `config/settings.yaml` est la seule source
   de vérité ; aucun script ne code une valeur métier en dur.
2. **La base SQLite** (`data/prospects.db`) : schéma, ouverture, écriture
   idempotente. Trois tables, un seul rôle chacune : `prospects` (l'état courant
   d'un contact), `envois` (le journal des événements, jamais réécrit) et
   `journal` (ce que chaque script a fait).
3. **Les fonctions sans effet de bord** : normalisation, validation d'adresse
   électronique, détection de plateforme, lecture d'une page publique.

Trois principes, hérités du reste du dépôt :

* **Hors ligne par défaut.** Aucune fonction de ce module ne fait de requête
  réseau si on ne le lui demande pas explicitement. `requete()` est la seule
  porte de sortie : elle s'identifie, lit `robots.txt` avant de visiter, et
  refuse les adresses privées (une liste de prospects n'est pas un mandat pour
  sonder le réseau local).
* **Idempotent.** Rejouer un import ou un suivi ne duplique jamais une ligne :
  `ON CONFLICT(email) DO UPDATE` pour les contacts, et un journal d'événements
  complété, jamais repris à zéro.
* **Honnête.** Une valeur inconnue reste vide (ou `Inconnue`) : le code ne
  devine pas une plateforme, un produit ou une ville pour faire joli.

Codes de retour conventionnels des scripts qui l'utilisent : `0` réussi,
`1` échec de vérification, `2` usage ou argument invalide.
"""

from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

# --- Chemins ---------------------------------------------------------------

#: `config/` et `data/` sont résolus depuis la position de ce fichier, jamais
#: depuis le répertoire courant : un script lancé depuis la racine du dépôt ou
#: depuis `scripts/` vise la même base.
RACINE = Path(__file__).resolve().parents[1]
CHEMIN_REGLAGES = RACINE / "config" / "settings.yaml"
DOSSIER_PROMPTS = RACINE / "config" / "prompts"
DOSSIER_DONNEES = RACINE / "data"
DOSSIER_RAPPORTS = RACINE / "reports"
CHEMIN_DB = DOSSIER_DONNEES / "prospects.db"
CHEMIN_TRACKING = DOSSIER_DONNEES / "tracking.json"

#: Statuts d'un prospect, dans l'ordre du cycle de vie. `nouveau` = jamais
#: contacté ; `envoye`/`ouvert`/`repondu` = suivi d'un envoi ; `converti`,
#: `refuse`, `desinscrit` = états terminaux, jamais recontactés.
STATUTS = (
    "nouveau",
    "envoye",
    "ouvert",
    "repondu",
    "converti",
    "refuse",
    "desinscrit",
)

#: Un statut terminal interdit toute nouvelle campagne (RGPD : un refus vaut
#: pour toujours, et un client n'a plus à être démarché).
STATUTS_TERMINAUX = ("converti", "refuse", "desinscrit")

#: Types d'événement consignés dans la table `envois`.
TYPES_EVENEMENT = (
    "intro",
    "relance_1",
    "relance_2",
    "ouverture",
    "reponse",
    "refus",
    "conversion",
    "desinscription",
    "bounce",
)

# --- Schéma de la base -----------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    nom_boutique TEXT NOT NULL,
    site_web TEXT NOT NULL DEFAULT '',
    domaine TEXT NOT NULL DEFAULT '',
    prenom TEXT NOT NULL DEFAULT '',
    nom TEXT NOT NULL DEFAULT '',
    secteur TEXT NOT NULL DEFAULT '',
    plateforme TEXT NOT NULL DEFAULT 'Inconnue',
    taille TEXT NOT NULL DEFAULT '',
    ville TEXT NOT NULL DEFAULT '',
    pays TEXT NOT NULL DEFAULT 'FR',
    siren TEXT NOT NULL DEFAULT '',
    statut TEXT NOT NULL DEFAULT 'nouveau',
    source TEXT NOT NULL DEFAULT 'manuel',
    campagne TEXT NOT NULL DEFAULT '',
    variante INTEGER,
    objet_retenu TEXT NOT NULL DEFAULT '',
    produit TEXT NOT NULL DEFAULT '',
    accroche TEXT NOT NULL DEFAULT '',
    revenu_usdc REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    date_import TEXT NOT NULL,
    date_envoi TEXT,
    date_ouverture TEXT,
    date_reponse TEXT,
    date_derniere_maj TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prospects_secteur ON prospects(secteur);
CREATE INDEX IF NOT EXISTS idx_prospects_statut ON prospects(statut);
CREATE INDEX IF NOT EXISTS idx_prospects_envoi ON prospects(date_envoi);

CREATE TABLE IF NOT EXISTS envois (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
    date_evenement TEXT NOT NULL,
    type TEXT NOT NULL,
    objet TEXT NOT NULL DEFAULT '',
    variante INTEGER,
    canal TEXT NOT NULL DEFAULT 'email',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_envois_prospect ON envois(prospect_id);
CREATE INDEX IF NOT EXISTS idx_envois_date ON envois(date_evenement);

CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date_evenement TEXT NOT NULL,
    script TEXT NOT NULL,
    action TEXT NOT NULL,
    chiffres TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_journal_date ON journal(date_evenement);
"""

# --- Temps -----------------------------------------------------------------


def maintenant() -> str:
    """Horodatage ISO 8601 en UTC, seconde près (même format que le registre)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def horodatage_jour() -> str:
    """Date du jour `AAAAMMJJ`, pour nommer une campagne."""
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def horodatage_complet() -> str:
    """Date et heure `AAAAMMJJ-HHMMSS`, pour nommer un rapport d'exécution."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def date_seule(valeur: str | None) -> str:
    """Ramène un horodatage ISO à sa date (`AAAA-MM-JJ`) ; vide si illisible."""
    return "" if not valeur else str(valeur)[:10]


# --- Réglages --------------------------------------------------------------


def charger_reglages(chemin: Path | str | None = None) -> dict[str, Any]:
    """Lit `config/settings.yaml` et vérifie les sections indispensables.

    Le fichier est la seule source de vérité métier (secteurs, seuils, objets
    d'A/B testing, cadence des relances). Il est relu à chaque exécution :
    corriger un seuil ne demande ni redémarrage, ni modification de code.

    Args:
        chemin: chemin explicite (les vérifications fabriquent leurs propres
            réglages) ; par défaut `config/settings.yaml`.

    Returns:
        Le dictionnaire des réglages.

    Raises:
        FileNotFoundError: le fichier de réglages n'existe pas.
        ValueError: une section obligatoire manque, le YAML est illisible, ou
            PyYAML n'est pas installé.
    """
    fichier = Path(chemin) if chemin else CHEMIN_REGLAGES
    if not fichier.is_file():
        raise FileNotFoundError(f"réglages absents : {fichier}")
    try:
        import yaml
    except ImportError as erreur:  # dépendance déclarée dans requirements.txt
        raise ValueError(
            "PyYAML est requis pour lire settings.yaml. Installez-le : "
            "..\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
        ) from erreur
    try:
        with fichier.open(encoding="utf-8") as flux:
            reglages = yaml.safe_load(flux) or {}
    except Exception as erreur:  # noqa: BLE001 - un YAML illisible doit se dire
        raise ValueError(f"settings.yaml illisible : {erreur}") from erreur
    for section in ("prospection", "secteurs", "emails", "rgpd"):
        if section not in reglages:
            raise ValueError(f"section obligatoire absente de {fichier.name} : {section}")
    if not reglages["secteurs"]:
        raise ValueError("aucun secteur déclaré dans settings.yaml")
    return reglages


def liste_secteurs(reglages: dict[str, Any]) -> list[dict[str, Any]]:
    """Secteurs déclarés, dans l'ordre du fichier de réglages."""
    return list(reglages.get("secteurs") or [])


def trouver_secteur(valeur: str | None, reglages: dict[str, Any]) -> dict[str, Any] | None:
    """Retrouve un secteur par son `code` (mode) ou par son `nom`.

    La comparaison ignore la casse, les accents et les tirets : `--secteur
    decoration` trouve « Décoration et maison ». Renvoie `None` si rien ne
    correspond — un secteur inconnu n'est jamais remplacé par un autre.
    """
    if not valeur:
        return None
    cherche = normaliser(valeur).replace("_", "-").replace(" ", "-")
    for secteur in liste_secteurs(reglages):
        candidats = {
            normaliser(str(secteur.get("code", ""))).replace("_", "-"),
            normaliser(str(secteur.get("nom", ""))).replace(" ", "-"),
        }
        if cherche in candidats:
            return secteur
    return None


def produit_par_defaut(secteur: dict[str, Any] | None) -> str:
    """Produit générique d'un secteur, pour ne jamais laisser un trou `{produit}`."""
    if not secteur:
        return "produits en ligne"
    return str(secteur.get("produit_par_defaut") or "produits en ligne")


# --- Normalisation de texte ------------------------------------------------


def sans_accents(texte: str) -> str:
    """Retire les diacritiques (`é` → `e`) sans toucher au reste."""
    decompose = unicodedata.normalize("NFKD", str(texte))
    return "".join(c for c in decompose if not unicodedata.combining(c))


def normaliser(texte: str) -> str:
    """Minuscule, sans accents, espaces compactés : la forme de comparaison."""
    return re.sub(r"\s+", " ", sans_accents(str(texte or "")).lower()).strip()


def slug(texte: str) -> str:
    """Identifiant lisible : `Le Slip Français` → `le-slip-francais`."""
    return re.sub(r"[^a-z0-9]+", "-", normaliser(texte)).strip("-") or "prospect"


def pourcent(numerateur: float, denominateur: float) -> str:
    """Taux lisible ; `n/a` quand le dénominateur est nul (jamais 0 % trompeur)."""
    if not denominateur:
        return "n/a"
    return f"{100.0 * numerateur / denominateur:.1f}%"


# --- Adresses électroniques, URL, plateformes -------------------------------

#: Syntaxe volontairement stricte (RFC 5322 abrégée). Mieux vaut refuser une
#: adresse douteuse que l'importer : un rebond dur abîme la réputation du
#: domaine expéditeur, et donc la délivrabilité de toute la campagne (README §7).
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.\-]+@[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9\-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,63}$"
)

#: Boîtes jetables : l'adresse existe mais n'est jamais lue sérieusement. Les
#: importer gonflerait les chiffres et pourrirait la mesure.
DOMAINES_JETABLES = frozenset(
    {
        "yopmail.com", "yopmail.fr", "jetable.org", "mailinator.com", "tempmail.com",
        "trashmail.com", "sharklasers.com", "guerrillamail.com", "10minutemail.com",
        "throwawaymail.com", "maildrop.cc", "getnada.com", "dispostable.com",
    }
)

#: Grands fournisseurs grand public. Ces adresses ne sont pas refusées (beaucoup
#: de très petites boutiques n'en ont pas d'autre) mais signalées : en B2B, la
#: CNIL attend qu'on écrive à une adresse professionnelle (README §7).
DOMAINES_PERSONNELS = frozenset(
    {
        "gmail.com", "googlemail.com", "orange.fr", "wanadoo.fr", "hotmail.fr",
        "hotmail.com", "outlook.fr", "outlook.com", "live.fr", "free.fr", "sfr.fr",
        "laposte.net", "yahoo.fr", "yahoo.com", "icloud.com", "me.com", "protonmail.com",
        "proton.me", "gmx.fr", "bbox.fr", "numericable.fr", "aliceadsl.fr",
    }
)

MAILTO_RE = re.compile(
    r"mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63})", re.IGNORECASE
)

#: Fragments qui trahissent une adresse technique ou un exemple de gabarit,
#: jamais un contact commercial réel.
FRAGMENTS_EMAIL_A_IGNORER = (
    "sentry", "wixpress", "@example", "@domain.", "@email.", "@mail.", "no-reply",
    "noreply", "postmaster@", "abuse@", "@sentry.io", "u003", "cdn.", "@2x.",
    "@localhost", "yourdomain", "@test.",
)


def valider_email(email: str) -> tuple[bool, str, list[str]]:
    """Valide une adresse électronique et signale ce qui mérite une relecture.

    Args:
        email: adresse brute, éventuellement entourée d'espaces ou précédée de
            `mailto:`.

    Returns:
        `(valide, motif, avertissements)`. `motif` est vide quand l'adresse est
        acceptée ; sinon il nomme le refus (`email_absent`, `format_invalide`,
        `domaine_jetable`, `point_double`). Les avertissements ne bloquent pas :
        `boite_personnelle` (adresse grand public), `role_generique` (contact@,
        info@ : utile à défaut de mieux, mais répond moins souvent).
    """
    valeur = str(email or "").strip()
    if valeur.lower().startswith("mailto:"):
        valeur = valeur[7:]
    if not valeur:
        return False, "email_absent", []
    if valeur.count("@") != 1:
        return False, "format_invalide", []
    local, _, domaine = valeur.partition("@")
    domaine = domaine.lower()
    if ".." in local or ".." in domaine or local.startswith(".") or local.endswith("."):
        return False, "point_double", []
    if not EMAIL_RE.match(valeur):
        return False, "format_invalide", []
    if domaine in DOMAINES_JETABLES:
        return False, "domaine_jetable", []
    avertissements: list[str] = []
    if domaine in DOMAINES_PERSONNELS:
        avertissements.append("boite_personnelle")
    if normaliser(local) in {"contact", "info", "hello", "bonjour", "commercial", "support",
                             "service", "administration", "accueil", "vente", "ventes",
                             "commandes", "boutique", "shop", "sales"}:
        avertissements.append("role_generique")
    return True, "", avertissements


def extraire_emails(html: str) -> list[str]:
    """Adresses de contact publiées dans une page (`mailto:`), sans doublon.

    L'extraction ne lit que ce que le site publie : aucun formulaire n'est
    rempli, aucun chemin deviné, aucune adresse reconstruite à partir d'un nom.
    """
    trouves: list[str] = []
    for brut in MAILTO_RE.findall(html or ""):
        adresse = brut.strip().rstrip(".,;:")
        if any(fragment in adresse.lower() for fragment in FRAGMENTS_EMAIL_A_IGNORER):
            continue
        if valider_email(adresse)[0] and adresse not in trouves:
            trouves.append(adresse)
    return trouves


def normaliser_url(url: str) -> str:
    """Complète un site écrit sans schéma (`exemple.fr` → `https://exemple.fr`)."""
    valeur = str(url or "").strip()
    if not valeur:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.\-]*://", valeur, re.IGNORECASE):
        valeur = "https://" + valeur.lstrip("/")
    return valeur


def domaine_depuis_url(url: str) -> str:
    """Domaine nu d'une URL, sans `www.` ; vide si l'URL est inexploitable."""
    try:
        hote = urllib.parse.urlparse(normaliser_url(url)).hostname or ""
    except ValueError:
        return ""
    return hote.lower().removeprefix("www.")


def hote_public(hote: str) -> bool:
    """Refuse `localhost`, les domaines internes et les adresses IP privées.

    Une liste de prospects reçue par courriel ne doit pas permettre de sonder le
    réseau de la machine qui l'exécute (SSRF) : seuls les noms publics passent.
    """
    nom = (hote or "").strip().lower().strip("[]")
    if not nom or nom in {"localhost", "localhost.localdomain", "ip6-localhost"}:
        return False
    if nom.endswith((".local", ".internal", ".lan", ".home", ".test", ".invalid", ".example")):
        return False
    try:
        return ipaddress.ip_address(nom).is_global
    except ValueError:
        return True  # nom de domaine : la résolution appartient au résolveur système


def detecter_plateforme(
    url: str = "", html: str = "", reglages: dict[str, Any] | None = None
) -> tuple[str, str]:
    """Nomme la plateforme d'une boutique — et dit sur quoi elle se fonde.

    L'ordre des vérifications est celui de la fiabilité : un motif dans l'URL
    (`myshopify.com`) est l'équivalent d'une preuve ; un indice dans le HTML
    servi (`cdn.shopify.com`) est une forte présomption. En l'absence des deux,
    la réponse est `Inconnue` : une plateforme devinée par élimination ferait
    écrire une phrase fausse dans le premier paragraphe de l'email.

    Args:
        url: adresse de la boutique (avec ou sans schéma).
        html: page d'accueil déjà récupérée, si `--enrichir` a visité le site.
        reglages: réglages complets ; `plateformes` en est lu.

    Returns:
        `(plateforme, preuve)` où `preuve` vaut `url:<motif>`, `html:<indice>`
        ou chaîne vide pour `Inconnue`.
    """
    plateformes = (reglages or {}).get("plateformes") or []
    url_nette = normaliser_url(url).lower()
    html_net = (html or "").lower()
    for plateforme in plateformes:
        motif = str(plateforme.get("url_pattern") or "").lower()
        if motif and motif in url_nette:
            return str(plateforme.get("nom") or "Inconnue"), f"url:{motif}"
    for plateforme in plateformes:
        indice = str(plateforme.get("indice_site") or "").lower()
        if indice and indice in html_net:
            return str(plateforme.get("nom") or "Inconnue"), f"html:{indice}"
    return "Inconnue", ""


# --- Gabarits de messages ---------------------------------------------------


def lire_prompt(
    nom: str, reglages: dict[str, Any] | None = None, objet_obligatoire: bool = True
) -> tuple[str, str]:
    """Lit un gabarit de `config/prompts/` et en sépare l'objet du corps.

    Convention des fichiers : la première ligne non vide est `Objet : ...`, le
    reste est le corps. Un gabarit d'email sans objet est refusé ; un message
    LinkedIn, qui n'a pas d'objet, se lit avec `objet_obligatoire=False`.

    Args:
        nom: `email_intro`, `email_followup` ou `linkedin_message`.
        reglages: réglages (le champ `prompt_template` sert de repli si le
            fichier n'existe pas, ce qui permet de tout tenir dans le YAML).
        objet_obligatoire: exige une ligne `Objet :` (défaut) ou accepte un
            gabarit sans objet.

    Returns:
        `(objet, corps)` — deux chaînes, placeholders `{...}` intacts.

    Raises:
        FileNotFoundError: le gabarit n'existe ni en fichier ni dans le YAML.
        ValueError: le fichier ne porte pas de ligne `Objet :` alors qu'elle est
            exigée.
    """
    fichier = DOSSIER_PROMPTS / f"{nom}.txt"
    if fichier.is_file():
        contenu = fichier.read_text(encoding="utf-8").strip("\n")
    else:
        repli = ((reglages or {}).get("prompt_template") or {}).get(nom)
        if not repli:
            raise FileNotFoundError(f"gabarit introuvable : {fichier}")
        contenu = str(repli).strip("\n")
    lignes = contenu.splitlines()
    premier = next((i for i, ligne in enumerate(lignes) if ligne.strip()), None)
    if premier is None:
        raise ValueError(f"gabarit vide : {fichier}")
    entete = lignes[premier].strip()
    if not entete.lower().startswith("objet"):
        if not objet_obligatoire:
            return "", contenu
        raise ValueError(f"gabarit sans ligne « Objet : » : {fichier}")
    objet = entete.split(":", 1)[1].strip() if ":" in entete else ""
    corps = "\n".join(lignes[premier + 1 :]).strip("\n")
    return objet, corps


def lire_signature(reglages: dict[str, Any]) -> str:
    """Bloc de signature, lu depuis `config/prompts/signature.txt`.

    Un seul endroit à corriger quand le numéro de téléphone ou l'adresse change,
    et c'est un fichier, pas une chaîne codée dans trois scripts.
    """
    chemin = str((reglages.get("prospection") or {}).get("signature_fichier") or "")
    fichier = (RACINE / chemin) if chemin else (DOSSIER_PROMPTS / "signature.txt")
    if not fichier.is_file():
        return ""
    return fichier.read_text(encoding="utf-8").strip("\n")


# --- Accès réseau (opt-in, identifié, poli) ---------------------------------

#: Taille maximale lue sur une page : de quoi trouver une plateforme ou une
#: adresse de contact, pas de quoi télécharger une boutique entière.
TAILLE_MAX_PAGE = 400_000


def robots_autorise(url: str, user_agent: str) -> bool:
    """Lit le `robots.txt` du site visé et dit si `url` peut être lue.

    Un `robots.txt` injoignable ou absent ne bloque pas : l'absence de fichier
    vaut autorisation, et la requête reste unique, identifiée et espacée. Un
    `Disallow` explicite, lui, arrête tout — c'est la règle que le site a écrite.
    """
    parties = urllib.parse.urlparse(normaliser_url(url))
    robots = f"{parties.scheme}://{parties.netloc}/robots.txt"
    lecteur = urllib.robotparser.RobotFileParser()
    try:
        lecteur.set_url(robots)
        lecteur.read()
    except Exception:  # noqa: BLE001 - robots.txt absent ou illisible : on continue
        return True
    try:
        return lecteur.can_fetch(user_agent, url)
    except Exception:  # noqa: BLE001
        return True


def requete(
    url: str,
    reglages: dict[str, Any] | None = None,
    timeout: float | None = None,
    respecter_robots: bool | None = None,
) -> tuple[int, str, str]:
    """Lit une page publique. **Seule** fonction de ce module qui sort du poste.

    Trois garde-fous, dans cet ordre : le schéma doit être `http` ou `https`,
    l'hôte doit être public (`hote_public`, anti-SSRF), et `robots.txt` doit
    autoriser la lecture quand le réglage `respecter_robots_txt` est actif
    (défaut : oui). L'agent s'identifie dans le `User-Agent`, comme le géocodeur
    de l'API le fait pour Nominatim.

    Args:
        url: adresse de la page ; le schéma est complété si absent.
        reglages: réglages complets ; la section `enrichissement` est lue
            (`user_agent`, `timeout_s`, `respecter_robots_txt`).
        timeout: délai explicite, prioritaire sur le réglage.
        respecter_robots: force ou désactive la lecture de `robots.txt`.

    Returns:
        `(code, corps, note)`. `code` vaut `0` quand aucune requête n'a été
        envoyée (schéma refusé, hôte privé, `robots.txt`, panne réseau) et la
        `note` dit alors pourquoi. Une note n'est pas un plantage : c'est la
        raison exacte pour laquelle la donnée manque.
    """
    enrichissement = (reglages or {}).get("enrichissement") or {}
    agent = str(enrichissement.get("user_agent") or "").strip() or (
        "AddressValidator-prospection/1.0 (+https://kder-ai.com)"
    )
    delai = float(timeout or enrichissement.get("timeout_s") or 10)
    if respecter_robots is None:
        respecter_robots = bool(enrichissement.get("respecter_robots_txt", True))

    adresse = normaliser_url(url)
    parties = urllib.parse.urlparse(adresse)
    if parties.scheme not in ("http", "https"):
        return 0, "", f"schéma refusé : {parties.scheme or 'absent'}"
    if not hote_public(parties.hostname or ""):
        return 0, "", f"hôte non public refusé : {parties.hostname or 'absent'}"
    if respecter_robots and not robots_autorise(adresse, agent):
        return 0, "", "robots.txt refuse l'accès"

    demande = urllib.request.Request(
        adresse, headers={"User-Agent": agent, "Accept": "text/html,application/xhtml+xml"}
    )
    try:
        with urllib.request.urlopen(demande, timeout=delai) as reponse:
            corps = reponse.read(TAILLE_MAX_PAGE).decode("utf-8", errors="replace")
            return int(getattr(reponse, "status", 200)), corps, ""
    except urllib.error.HTTPError as erreur:
        return int(erreur.code), "", f"HTTP {erreur.code}"
    except Exception as erreur:  # noqa: BLE001 - réseau : l'échec est une donnée
        return 0, "", f"erreur réseau : {type(erreur).__name__}"


def attendre(reglages: dict[str, Any] | None, cle: str = "delai_entre_requetes_s") -> None:
    """Marque une pause entre deux requêtes sortantes (politesse d'usage).

    Une rafale de requêtes sur un même site est le meilleur moyen d'être bloqué
    — et de perdre la possibilité de le recontacter plus tard.
    """
    delai = float(((reglages or {}).get("enrichissement") or {}).get(cle) or 0)
    if delai > 0:
        time.sleep(delai)


# --- Base de données -------------------------------------------------------

#: Colonnes de `prospects` qu'un script a le droit d'écrire. Liste explicite :
#: un `UPDATE` construit à partir d'une clé lue dans un CSV importé ne doit
#: jamais pouvoir toucher une autre colonne.
COLONNES_PROSPECTS = (
    "nom_boutique", "site_web", "domaine", "prenom", "nom", "secteur", "plateforme",
    "taille", "ville", "pays", "siren", "statut", "source", "campagne", "variante",
    "objet_retenu", "produit", "accroche", "revenu_usdc", "notes", "date_envoi",
    "date_ouverture", "date_reponse",
)


def securiser_sortie() -> None:
    """Empêche un caractère non représentable de faire tomber un script.

    La console Windows est en code page 1252 : une flèche (`→`) ou un emoji dans
    un libellé ferait lever `UnicodeEncodeError` au milieu d'un rapport, après un
    import de 500 lignes. On demande donc à Python de **remplacer** les caractères
    non représentables au lieu d'échouer : un rapport complet avec un caractère
    approché vaut mieux qu'un script mort et un état incertain.
    """
    for flux in (sys.stdout, sys.stderr):
        try:
            flux.reconfigure(errors="replace")
        except (AttributeError, ValueError):  # flux redirigé sans reconfigure()
            continue


def connexion(chemin: Path | str | None = None, creer: bool = False) -> sqlite3.Connection:
    """Ouvre la base SQLite des prospects.

    Args:
        chemin: par défaut `data/prospects.db`.
        creer: `True` crée le fichier et son dossier ; `False` (défaut) refuse de
            travailler sur une base absente. Ce défaut est un choix : une lectrice
            (le CRM, une analyse) qui reçoit un chemin vide doit dire « il n'y a
            rien », pas créer en silence une base neuve et afficher des zéros.

    Returns:
        Connexion `sqlite3` avec `row_factory = Row` (accès par nom de colonne),
        clés étrangères actives et journal WAL.

    Raises:
        FileNotFoundError: base absente et `creer=False`.
    """
    fichier = Path(chemin) if chemin else CHEMIN_DB
    if creer:
        fichier.parent.mkdir(parents=True, exist_ok=True)
    elif not fichier.is_file():
        raise FileNotFoundError(
            f"base absente : {fichier} — importez d'abord des prospects :\n"
            "  python scripts/import_prospects.py --input data/prospects_exemple.csv"
        )
    base = sqlite3.connect(str(fichier))
    base.row_factory = sqlite3.Row
    base.execute("PRAGMA foreign_keys = ON")
    base.execute("PRAGMA journal_mode = WAL")
    return base


def initialiser(base: sqlite3.Connection) -> None:
    """Crée tables et index manquants (idempotent, sans perte de données)."""
    base.executescript(SCHEMA_SQL)
    base.commit()


def journaliser(
    base: sqlite3.Connection | None,
    script: str,
    action: str,
    chiffres: dict[str, Any] | None = None,
) -> None:
    """Consigne une exécution dans la table `journal` **et** dans `tracking.json`.

    Le double enregistrement est volontaire : SQLite sert aux analyses,
    `tracking.json` se lit à l'œil nu, se compare entre deux passages et survit à
    la suppression accidentelle de la base.

    Args:
        base: connexion (facultative : un sourcing sans base reste consignable).
        script: nom du script appelant (`import_prospects`).
        action: verbe court et lisible (`import`, `generation`, `suivi`).
        chiffres: dictionnaire de compteurs, sérialisé en JSON.
    """
    horodatage = maintenant()
    if base is not None:
        base.execute(
            "INSERT INTO journal (date_evenement, script, action, chiffres) VALUES (?,?,?,?)",
            (horodatage, script, action, json.dumps(chiffres or {}, ensure_ascii=False)),
        )
        base.commit()
    try:
        contenu = (
            json.loads(CHEMIN_TRACKING.read_text(encoding="utf-8"))
            if CHEMIN_TRACKING.is_file()
            else {}
        )
    except (ValueError, OSError):
        contenu = {}
    contenu["version"] = int(contenu.get("version") or 1)
    contenu["derniere_mise_a_jour"] = horodatage
    contenu.setdefault("journal", []).append(
        {"date": horodatage, "script": script, "action": action, "chiffres": chiffres or {}}
    )
    CHEMIN_TRACKING.parent.mkdir(parents=True, exist_ok=True)
    CHEMIN_TRACKING.write_text(
        json.dumps(contenu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def noter_campagne(nom_fichier: str, chiffres: dict[str, Any]) -> None:
    """Ajoute une campagne au registre lisible de `tracking.json`.

    Une campagne est un fichier d'envoi daté. En garder la trace permet de
    répondre plus tard à la seule question qui compte en A/B testing :
    « quel fichier contenait quel objet, et pour quels prospects ? ».
    """
    try:
        contenu = (
            json.loads(CHEMIN_TRACKING.read_text(encoding="utf-8"))
            if CHEMIN_TRACKING.is_file()
            else {}
        )
    except (ValueError, OSError):
        contenu = {}
    contenu.setdefault("campagnes", []).append(
        {"date": maintenant(), "fichier": nom_fichier, **chiffres}
    )
    CHEMIN_TRACKING.write_text(
        json.dumps(contenu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def prospect_par_email(base: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    """Ligne d'un prospect par son adresse (insensible à la casse), sinon `None`."""
    return base.execute(
        "SELECT * FROM prospects WHERE email = ? COLLATE NOCASE", (str(email).strip(),)
    ).fetchone()


def maj_prospect(base: sqlite3.Connection, prospect_id: int, **champs: Any) -> None:
    """Met à jour les colonnes autorisées d'un prospect, et son horodatage.

    Args:
        base: connexion ouverte.
        prospect_id: identifiant du prospect.
        **champs: colonnes de `COLONNES_PROSPECTS` uniquement. Toute autre clé
            lève `KeyError` — garde-fou contre un `UPDATE` construit depuis une
            entrée non fiable (export d'outil d'envoi, CSV reçu).

    Raises:
        KeyError: une colonne non modifiable a été demandée.
    """
    inconnues = set(champs) - set(COLONNES_PROSPECTS)
    if inconnues:
        raise KeyError(f"colonnes non modifiables : {sorted(inconnues)}")
    if not champs:
        return
    affectations = ", ".join(f"{colonne} = ?" for colonne in champs)
    valeurs = list(champs.values()) + [maintenant(), int(prospect_id)]
    base.execute(
        f"UPDATE prospects SET {affectations}, date_derniere_maj = ? WHERE id = ?", valeurs
    )
    base.commit()


def enregistrer_evenement(
    base: sqlite3.Connection,
    prospect_id: int,
    type_evenement: str,
    objet: str = "",
    variante: int | None = None,
    canal: str = "email",
    detail: str = "",
    quand: str | None = None,
) -> None:
    """Ajoute un fait daté au journal d'événements d'un prospect.

    La table `envois` n'est jamais réécrite : c'est une suite de faits (envoi,
    ouverture, réponse, refus). Les statistiques en découlent, et un doublon
    d'export d'outil d'envoi reste **visible** au lieu d'écraser la première ligne.

    Raises:
        ValueError: type d'événement hors de `TYPES_EVENEMENT`.
    """
    if type_evenement not in TYPES_EVENEMENT:
        raise ValueError(f"type d'événement inconnu : {type_evenement}")
    base.execute(
        "INSERT INTO envois (prospect_id, date_evenement, type, objet, variante, canal, detail)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            int(prospect_id),
            quand or maintenant(),
            type_evenement,
            objet or "",
            variante,
            canal,
            detail or "",
        ),
    )
    base.commit()


# --- CSV --------------------------------------------------------------------


def lire_csv(
    chemin: Path | str, colonnes_obligatoires: Sequence[str]
) -> tuple[list[dict], list[dict]]:
    """Lit un CSV de travail et contrôle son en-tête.

    Args:
        chemin: fichier à lire (UTF-8, BOM toléré — les exports tableur en posent
            un systématiquement).
        colonnes_obligatoires: colonnes sans lesquelles le fichier est refusé.

    Returns:
        `(lignes, erreurs)` : les lignes utiles (clés en minuscules, valeurs
        détourées, numéro de ligne conservé dans `_ligne`) et la liste des
        problèmes (`{"ligne": n, "motif": "..."}`).

    Raises:
        FileNotFoundError: fichier absent.
        ValueError: en-tête vide, ou colonne obligatoire manquante. Un fichier
            dont l'en-tête ne correspond pas est refusé en bloc : mieux vaut un
            arrêt net qu'un import où toutes les colonnes sont décalées.
    """
    fichier = Path(chemin)
    if not fichier.is_file():
        raise FileNotFoundError(f"fichier absent : {fichier}")
    lignes: list[dict] = []
    with fichier.open(encoding="utf-8-sig", newline="") as flux:
        lecteur = csv.DictReader(flux)
        entetes = [str(colonne or "").strip().lower() for colonne in (lecteur.fieldnames or [])]
        if not entetes:
            raise ValueError(f"en-tête illisible dans {fichier.name}")
        manquantes = [colonne for colonne in colonnes_obligatoires if colonne.lower() not in entetes]
        if manquantes:
            raise ValueError(
                f"colonnes obligatoires absentes de {fichier.name} : {manquantes}\n"
                f"  en-tête lue : {entetes}"
            )
        for numero, brut in enumerate(lecteur, start=2):
            ligne = {
                str(cle or "").strip().lower(): str(valeur or "").strip()
                for cle, valeur in brut.items()
                if cle is not None
            }
            if not any(ligne.values()):
                continue  # ligne vide : ignorée, sans bruit
            ligne["_ligne"] = numero
            lignes.append(ligne)
    return lignes, []


def ecrire_csv(
    chemin: Path | str, entetes: Sequence[str], lignes: Iterable[dict[str, Any]]
) -> int:
    """Écrit un CSV UTF-8 sans BOM (les outils modernes le lisent tel quel).

    Les clés absentes d'une ligne deviennent des cellules vides : un fichier de
    campagne reste aligné même si un prospect n'a pas de prénom connu.

    Returns:
        Le nombre de lignes écrites, en-tête exclue.
    """
    fichier = Path(chemin)
    fichier.parent.mkdir(parents=True, exist_ok=True)
    compte = 0
    with fichier.open("w", encoding="utf-8", newline="") as flux:
        ecrivain = csv.DictWriter(flux, fieldnames=list(entetes), extrasaction="ignore")
        ecrivain.writeheader()
        for ligne in lignes:
            ecrivain.writerow({entete: ligne.get(entete, "") for entete in entetes})
            compte += 1
    return compte


# --- Gabarits : rendu et affectation A/B ------------------------------------


def rendre_gabarit(corps: str, valeurs: dict[str, str]) -> str:
    """Remplace les `{placeholders}` d'un gabarit et résorbe les trous.

    Un champ vide (l'accroche personnalisée, souvent) ne doit pas laisser une
    ligne blanche au milieu du message : après remplacement, les espaces en fin
    de ligne et les blancs surnuméraires sont nettoyés.
    """
    texte = corps or ""
    for cle, valeur in valeurs.items():
        texte = texte.replace("{" + cle + "}", str(valeur if valeur is not None else ""))
    texte = re.sub(r"[ \t]+\n", "\n", texte)
    texte = re.sub(r"\n{3,}", "\n\n", texte)
    return texte.strip()


def placeholders_restants(texte: str) -> list[str]:
    """Placeholders jamais remplacés, triés et sans doublon.

    Contrôle passé avant tout export : un email qui part avec `{prenom}` en clair
    est un email qui part à la poubelle — et qui coûte une réputation d'expéditeur.
    """
    return sorted({trouve for trouve in re.findall(r"\{([a-z_]+)\}", texte or "")})


def variante_deterministe(email: str, nb_variantes: int) -> int:
    """Numéro de variante (1..`nb_variantes`) attribué à une adresse, de façon stable.

    Stable veut dire : deux exécutions donnent la même affectation, donc
    ré-exporter une campagne ne redistribue pas les groupes en cours d'A/B test.
    La répartition suit sha256 et non l'ordre du fichier : trier la liste des
    prospects ne déplace pas non plus les groupes.
    """
    nombre = max(1, int(nb_variantes))
    empreinte = hashlib.sha256(normaliser(email).encode("utf-8")).digest()
    return (empreinte[0] % nombre) + 1


# --- Affichage --------------------------------------------------------------


def titre(texte: str) -> None:
    """Titre d'exécution, encadré : repérable dans une sortie longue."""
    print("=" * 72)
    print(texte)
    print("=" * 72)


def etape(texte: str) -> None:
    """Étape en cours (ce que le script est en train de faire)."""
    print(f"  - {texte}")


def resultat(texte: str) -> None:
    """Conclusion d'étape (ce qui vient d'être obtenu)."""
    print(f"    => {texte}")


def alerte(texte: str) -> None:
    """Point de vigilance : l'exécution continue, mais il faut le lire."""
    print(f"    !! {texte}")


def tableau(entetes: Sequence[str], lignes: Iterable[Sequence[Any]]) -> str:
    """Tableau ASCII aligné, sans dépendance externe (lisible dans un terminal)."""
    corps = [
        [str(cellule if cellule is not None else "") for cellule in ligne] for ligne in lignes
    ]
    largeurs: list[int] = []
    for index, entete in enumerate(entetes):
        largeur = len(str(entete))
        for ligne in corps:
            if index < len(ligne):
                largeur = max(largeur, len(ligne[index]))
        largeurs.append(largeur)

    def formater(cellules: Sequence[Any]) -> str:
        return "  ".join(
            str(cellule if cellule is not None else "").ljust(largeurs[index])
            for index, cellule in enumerate(cellules)
            if index < len(largeurs)
        )

    morceaux = [formater(entetes), "  ".join("-" * largeur for largeur in largeurs)]
    morceaux.extend(formater(ligne) for ligne in corps)
    return "\n".join(morceaux)


def barre(valeur: float, maximum: float, largeur: int = 24) -> str:
    """Barre ASCII (`####....`) pour les graphiques du terminal."""
    if maximum <= 0 or valeur <= 0:
        return "." * largeur
    rempli = max(1, round(largeur * float(valeur) / float(maximum)))
    return "#" * min(largeur, rempli) + "." * max(0, largeur - rempli)
