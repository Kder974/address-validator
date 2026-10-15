#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verifie les DNS de kder-ai.com avant le premier envoi (Zoho Mail + Brevo).

QUI   : l'exploitant, a la main, sur son poste.
QUOI  : controle les enregistrements dont depend le courrier du domaine — les
        3 MX Zoho, le SPF, la preuve de domaine Zoho, le DKIM Zoho et le DMARC —
        puis dit lesquels sont prets, manquants ou differents.
COMMENT : interroge le DNS public avec `dig` (s'il est present) ou `nslookup`
        (Windows, macOS), et compare aux valeurs de
        `docs/commercial/DNS-A-CONFIGURER.md`. Le datacenter controle est celui
        que le domaine **utilise reellement**, et c'est le defaut : `kder-ai.com`
        depend de Zoho monde (`.com`), ses MX publies sont `mx.zoho.com`.
        `--dc eu` sert a un domaine heberge par `zoho.eu` — le mauvais choix
        ferait signaler trois MX manquants sur un domaine correctement
        configure. Aucune connexion a Zoho ni a Brevo : le DNS public suffit,
        et aucun secret n'est demande.
QUAND : apres avoir colle les enregistrements dans Namecheap (compter jusqu'a
        1 h de propagation), puis apres tout changement d'expediteur.
OU    : scripts/verifier_dns_kder.py, lance depuis la racine du depot :
        .venv\\Scripts\\python.exe scripts\\verifier_dns_kder.py
        .venv\\Scripts\\python.exe scripts\\verifier_dns_kder.py --dkim "v=DKIM1; k=rsa; p=..."
        .venv\\Scripts\\python.exe scripts\\verifier_dns_kder.py --dc com --serveur 8.8.8.8
FAIT QUOI EXACTEMENT : affiche une ligne par enregistrement

        ✅ OK            trouve et conforme
        ❌ MANQUANT      absent du DNS
        ⚠️ DIFFERENT     present mais non conforme
        ⚠️ NON VERIFIE   controle impossible (DKIM non fourni)

Codes de retour : 0 tout est pret, 1 au moins un enregistrement manquant,
2 uniquement des ecarts, 3 aucun outil DNS utilisable sur la machine.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

# --- Valeurs attendues (docs/commercial/DNS-A-CONFIGURER.md) ----------------

#: Domaine teste par defaut.
DOMAINE_DEFAUT = "kder-ai.com"

#: Datacenter Zoho teste par defaut. `kder-ai.com` est heberge par Zoho
#: **monde** : le DNS public repond `mx.zoho.com`, jamais `mx.zoho.eu`. Se
#: tromper de datacenter fait signaler trois MX manquants sur un domaine
#: pourtant correct (constate le 15/10/2026) : ce defaut suit donc le domaine,
#: `--dc eu` restant disponible pour un compte europeen.
DC_DEFAUT = "com"

#: MX attendus par datacenter : (hote, priorite). Releve du DNS Zoho le
#: 13/10/2026 : `mx4.zoho.eu` n'existe pas, il n'y a que trois MX par region.
MX_ATTENDUS = {
    "eu": (("mx.zoho.eu", 10), ("mx2.zoho.eu", 20), ("mx3.zoho.eu", 50)),
    "com": (("mx.zoho.com", 10), ("mx2.zoho.com", 20), ("mx3.zoho.com", 50)),
}

#: `include` SPF a autoriser pour que Zoho puisse envoyer au nom du domaine.
#: C'est le `include` que Zoho **publie et documente** pour chaque datacenter :
#: `zoho.eu` en Europe (`spf.zoho.eu` couvre `89.36.170.0/24`, l'adresse de
#: `smtp.zoho.eu`) et `zoho.com` pour le monde. Le TXT SPF de `zoho.com` chaine
#: lui-meme `include:spf.zohomail.com`, la plage d'envoi du datacenter monde :
#: exiger `include:zoho.com` couvre donc bien les hotes `zohomail.com`.
#: `zohomail.com` resout aussi (`v=spf1 include:spf.zohomail.com -all`, releve
#: le 15/10/2026) mais n'est pas la valeur publiee pour un domaine : l'exiger
#: faisait signaler un ecart sur le SPF correct de `kder-ai.com`
#: (`v=spf1 include:zoho.com include:spf.brevo.com ~all`) — faux positif
#: corrige le 15/10/2026.
INCLUDE_SPF = {"eu": "include:zoho.eu", "com": "include:zoho.com"}

#: `include` SPF du relais d'envoi Brevo (releve dans le TXT `spf.brevo.com`).
INCLUDE_BREVO = "include:spf.brevo.com"

#: Fragment d'un SPF laisse par la redirection eForward de Namecheap : sa
#: presence signifie que l'ancien SPF n'a pas ete supprime.
INCLUDE_EFORWARD = "spf.efwd.registrar-servers.com"

#: Un MX contenant l'un de ces fragments est une redirection de registrar : a
#: supprimer, un domaine ne peut avoir qu'un seul jeu de MX.
MX_ETRANGERS = ("registrar-servers.com", "eforward")

#: Selecteur DKIM de Zoho Mail.
SELECTEUR_DKIM = "zmail"

#: Preuve de domaine demandee par Zoho, telle qu'elle est publiee aujourd'hui.
CLE_DOMAINE_DEFAUT = "zoho-verification=zb82668052.zmverify.zoho.com"

#: Tags DMARC attendus : surveillance d'abord (`p=none`), rapport vers l'adresse
#: du domaine. `fo=1` remonte tout echec DKIM/SPF.
DMARC_TAGS_ATTENDUS = {"v": "dmarc1", "p": "none"}
DMARC_RUA_ATTENDUE = "mailto:address-validator@kder-ai.com"

#: `rua` acceptees : l'adresse du domaine (rapports lisibles par toi) et celle
#: que Brevo pose d'office pour sa surveillance DMARC — releve le 13/10/2026
#: sur `_dmarc.kder-ai.com` : `rua=mailto:rua@dmarc.brevo.com`.
RUA_ACCEPTEES = (DMARC_RUA_ATTENDUE.split(":", 1)[1], "rua@dmarc.brevo.com")

#: Noms ou Brevo publie le DKIM de son domaine d'envoi : un CNAME (ou un TXT)
#: sur l'un des deux, la cible etant propre au compte.
NOMS_DKIM_BREVO = ("mail._domainkey", "brevo._domainkey")

#: Symboles affiches par gravite (voir la docstring).
SYMBOLES = {
    "ok": "✅ OK",
    "manquant": "❌ MANQUANT",
    "different": "⚠️  DIFFERENT",
    "non-verifie": "⚠️  NON VERIFIE",
}

#: Delai maximal d'une interrogation DNS (secondes).
DELAI_DNS = 15.0


class ErreurDns(RuntimeError):
    """Aucun outil DNS utilisable, ou reponse DNS illisible."""


@dataclass
class Constat:
    """Resultat d'un controle : une gravite, un libelle et un detail."""

    gravite: str  # ok | manquant | different | non-verifie
    libelle: str
    detail: str

    def ligne(self) -> str:
        """Ligne affichee a l'exploitant."""
        return f"{SYMBOLES[self.gravite]:<16} {self.libelle} : {self.detail}"


# --- Interrogation du DNS (dig, sinon nslookup) -----------------------------


def _forcer_utf8() -> None:
    """Sortie en UTF-8 : sans cela, ✅ redirige vers un fichier leve une erreur."""
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")


def outil_dns() -> str:
    """Nom de l'outil DNS disponible (`dig`, sinon `nslookup`)."""
    for nom in ("dig", "nslookup"):
        if shutil.which(nom):
            return nom
    raise ErreurDns(
        "ni `dig` ni `nslookup` sur cette machine : impossible d'interroger le DNS"
    )


def _interroger(nom: str, type_: str, serveur: str | None, outil: str) -> str:
    """Sortie brute de l'outil DNS pour `nom`/`type_` (stdout + stderr)."""
    if outil == "dig":
        commande = ["dig", "+short", type_, nom]
        if serveur:
            commande.append(f"@{serveur}")
    else:
        commande = ["nslookup", f"-type={type_}", nom]
        if serveur:
            commande.append(serveur)
    try:
        resultat = subprocess.run(
            commande,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=DELAI_DNS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as erreur:  # pragma: no cover
        raise ErreurDns(f"`{outil}` n'a pas repondu pour {nom} : {erreur}") from erreur
    return f"{resultat.stdout or ''}{resultat.stderr or ''}"


def _echec_reseau(sortie: str) -> bool:
    """Vrai si la sortie traduit une panne de resolution, pas une absence."""
    marqueurs = (
        "timed out",
        "timed-out",
        "timeout",
        "no servers could be reached",
        "requete expiree",
        "delai d'attente",
        "expir",  # « a expire », « expired », selon la langue de l'outil
        "refused",
        "network is unreachable",
    )
    texte = sortie.lower()
    return any(marqueur in texte for marqueur in marqueurs)


_RE_MX_NSLOOKUP = re.compile(
    r"preference\s*=\s*(\d+)\s*,\s*mail exchanger\s*=\s*(\S+)", re.IGNORECASE
)
_RE_MX_DIG = re.compile(r"^(\d+)\s+(\S+?)\.?$")


def mx(domaine: str, serveur: str | None = None, outil: str | None = None) -> dict:
    """MX du domaine, sous la forme `{hote_en_minuscules: priorite}`."""
    outil = outil or outil_dns()
    sortie = _interroger(domaine, "MX", serveur, outil)
    if _echec_reseau(sortie):
        raise ErreurDns(f"resolution MX impossible pour {domaine} : {sortie.strip()}")
    trouves: dict[str, int] = {}
    if outil == "dig":
        for ligne in sortie.splitlines():
            correspondance = _RE_MX_DIG.match(ligne.strip())
            if correspondance:
                trouves[correspondance.group(2).lower()] = int(correspondance.group(1))
    else:
        for priorite, hote in _RE_MX_NSLOOKUP.findall(sortie):
            trouves[hote.lower().rstrip(".")] = int(priorite)
    if not trouves and "mail exchanger" in sortie.lower():  # pragma: no cover
        raise ErreurDns(
            f"sortie nslookup MX non reconnue pour {domaine} : {sortie.strip()}"
        )
    return trouves


_RE_TXT_DIG = re.compile(r'"([^"]*)"')
_RE_TXT_NSLOOKUP = re.compile(r"(?:text|texte)\s*=\s*(.*)$", re.IGNORECASE)


def _sans_guillemets(fragment: str) -> str:
    """Valeur d'un fragment TXT : guillemets retires, espaces de bord enleves."""
    return fragment.replace('"', "").strip()


def _txt_depuis_nslookup(sortie: str) -> list:
    """TXT d'une sortie `nslookup` : un enregistrement par ligne `text =`.

    Un enregistrement long (une cle DKIM) arrive decoupe en fragments de 255
    caracteres, chacun entre guillemets sur sa propre ligne : les fragments qui
    suivent une meme ligne `text =` sont recolles, sinon toute comparaison de
    cle serait impossible.
    """
    enregistrements: list[str] = []
    courant: list[str] | None = None
    for ligne in sortie.splitlines():
        ligne = ligne.strip()
        correspondance = _RE_TXT_NSLOOKUP.search(ligne)
        if correspondance:
            if courant is not None:
                enregistrements.append("".join(courant))
            courant = []
            reste = correspondance.group(1).strip()
            if reste:
                courant.append(_sans_guillemets(reste))
            continue
        if courant is not None and ligne.startswith('"'):
            courant.append(_sans_guillemets(ligne))
    if courant is not None:
        enregistrements.append("".join(courant))
    valeurs = (valeur.strip() for valeur in enregistrements)
    return [valeur for valeur in valeurs if valeur]


def txt(nom: str, serveur: str | None = None, outil: str | None = None) -> list:
    """Enregistrements TXT de `nom` : un element par enregistrement."""
    outil = outil or outil_dns()
    sortie = _interroger(nom, "TXT", serveur, outil)
    if _echec_reseau(sortie):
        raise ErreurDns(f"resolution TXT impossible pour {nom} : {sortie.strip()}")
    if outil == "dig":
        return [
            "".join(_RE_TXT_DIG.findall(ligne))
            for ligne in sortie.splitlines()
            if ligne.strip()
        ]
    return _txt_depuis_nslookup(sortie)


_RE_CNAME_NSLOOKUP = re.compile(
    r"(?:canonical name|nom canonique)\s*=\s*(\S+)", re.IGNORECASE
)


def cname(nom: str, serveur: str | None = None, outil: str | None = None) -> list:
    """Cibles CNAME de `nom` (liste vide quand l'enregistrement n'existe pas)."""
    outil = outil or outil_dns()
    sortie = _interroger(nom, "CNAME", serveur, outil)
    if _echec_reseau(sortie):
        raise ErreurDns(f"resolution CNAME impossible pour {nom} : {sortie.strip()}")
    if outil == "dig":
        return [
            ligne.strip().rstrip(".")
            for ligne in sortie.splitlines()
            if ligne.strip()
        ]
    return [cible.rstrip(".") for cible in _RE_CNAME_NSLOOKUP.findall(sortie)]


# --- Controles, un par famille d'enregistrement -----------------------------


def _presents_parmi(hotes: Sequence[str], fragments: Sequence[str]) -> list:
    """Hotes contenant l'un des fragments, tries (ex. MX de registrar)."""
    return sorted(hote for hote in hotes if any(f in hote for f in fragments))


def constats_mx(domaine: str, dc: str = DC_DEFAUT, serveur=None, outil=None) -> list:
    """Les trois MX Zoho, plus les MX de registrar a supprimer."""
    trouves = mx(domaine, serveur, outil)
    constats = []
    for hote, priorite in MX_ATTENDUS[dc]:
        if hote not in trouves:
            constats.append(
                Constat("manquant", f"MX {hote}", f"absent (priorite {priorite} attendue)")
            )
        elif trouves[hote] != priorite:
            constats.append(
                Constat(
                    "different",
                    f"MX {hote}",
                    f"priorite {trouves[hote]} au lieu de {priorite}",
                )
            )
        else:
            constats.append(Constat("ok", f"MX {hote}", f"priorite {priorite}"))
    for hote in _presents_parmi(trouves, MX_ETRANGERS):
        constats.append(
            Constat(
                "different",
                f"MX {hote}",
                "a supprimer : redirection du registrar, un domaine ne porte "
                "qu'un seul jeu de MX",
            )
        )
    return constats


def constats_spf(
    domaine: str, dc: str = DC_DEFAUT, sans_brevo=False, serveur=None, outil=None
) -> list:
    """Le TXT SPF : unique, avec les `include` Zoho et Brevo."""
    valeurs = txt(domaine, serveur, outil)
    spf = [valeur for valeur in valeurs if valeur.lower().startswith("v=spf1")]
    if not spf:
        return [Constat("manquant", "SPF", f"aucun TXT `v=spf1` sur {domaine}")]
    if len(spf) > 1:
        return [
            Constat(
                "different",
                "SPF",
                f"{len(spf)} enregistrements SPF : les fusionner en une seule "
                "ligne, deux `v=spf1` donnent `permerror`",
            )
        ]
    valeur = spf[0]
    minuscule = valeur.lower()
    ecarts = []
    if INCLUDE_SPF[dc] not in minuscule:
        ecarts.append(f"`{INCLUDE_SPF[dc]}` absent")
    if not sans_brevo and INCLUDE_BREVO not in minuscule:
        ecarts.append(f"`{INCLUDE_BREVO}` absent (envois Brevo)")
    if INCLUDE_EFORWARD in minuscule:
        ecarts.append(
            f"`include:{INCLUDE_EFORWARD}` encore present : SPF Namecheap a supprimer"
        )
    if "-all" in minuscule and "~all" not in minuscule:
        ecarts.append(
            "se termine par `-all` : preferer `~all` tant que tous les "
            "expediteurs ne sont pas listes"
        )
    if ecarts:
        return [Constat("different", "SPF", "; ".join(ecarts) + f" - publie : {valeur}")]
    return [Constat("ok", "SPF", valeur)]


def constats_preuve_domaine(
    domaine: str, cle: str = CLE_DOMAINE_DEFAUT, serveur=None, outil=None
) -> list:
    """Le TXT `zoho-verification=…` qui prouve le domaine a Zoho."""
    valeurs = txt(domaine, serveur, outil)
    if any(cle.lower() in valeur.lower() for valeur in valeurs):
        return [Constat("ok", "Preuve de domaine Zoho", cle)]
    return [
        Constat(
            "manquant",
            "Preuve de domaine Zoho",
            f"`{cle}` absent des TXT de {domaine} : relancer la verification "
            "dans la console Zoho (Admin > Domaines)",
        )
    ]


def _normaliser_dkim(valeur: str) -> str:
    """Cle DKIM comparable : espaces et retours a la ligne retires."""
    return re.sub(r"\s+", "", valeur)


def constats_dkim(
    domaine: str,
    attendue: str | None = None,
    serveur=None,
    outil=None,
    selecteur: str = SELECTEUR_DKIM,
) -> list:
    """Le TXT DKIM de Zoho, sur `<selecteur>._domainkey.<domaine>`."""
    nom = f"{selecteur}._domainkey.{domaine}"
    valeurs = txt(nom, serveur, outil)
    if not valeurs:
        return [
            Constat(
                "manquant",
                "DKIM Zoho",
                f"aucun TXT sur {nom} : coller la cle affichee par la console "
                "Zoho (Admin > Email Deliverability > DKIM)",
            )
        ]
    trouvee = valeurs[0]
    if not attendue:
        return [
            Constat(
                "non-verifie",
                "DKIM Zoho",
                f"{nom} renseigne ({len(trouvee)} caracteres) : relancer avec "
                "--dkim pour comparer a la cle Zoho",
            )
        ]
    if _normaliser_dkim(trouvee) == _normaliser_dkim(attendue):
        return [Constat("ok", "DKIM Zoho", f"{nom} identique a la cle Zoho")]
    return [
        Constat(
            "different",
            "DKIM Zoho",
            f"{nom} ne correspond pas a la cle fournie (publie : "
            f"{trouvee[:40]}... ; cle Zoho : {attendue[:40]}...)",
        )
    ]


_RE_TAG_DMARC = re.compile(r"\s*([a-zA-Z0-9]+)\s*=\s*([^;]*)", re.IGNORECASE)


def _tags_dmarc(valeur: str) -> dict:
    """Tags d'un TXT DMARC : `{v: dmarc1, p: none, rua: mailto:…}`."""
    return {
        cle.lower(): tag.strip().lower() for cle, tag in _RE_TAG_DMARC.findall(valeur)
    }


def constats_dmarc(domaine: str, serveur=None, outil=None) -> list:
    """Le TXT `_dmarc` : surveillance `p=none`, rapport vers le domaine."""
    nom = f"_dmarc.{domaine}"
    valeurs = txt(nom, serveur, outil)
    if not valeurs:
        return [
            Constat(
                "manquant",
                "DMARC",
                f"aucun TXT sur {nom} : ajouter "
                "`v=DMARC1; p=none; rua=mailto:address-validator@kder-ai.com`",
            )
        ]
    valeur = valeurs[0]
    tags = _tags_dmarc(valeur)
    ecarts = []
    if len(valeurs) > 1:
        ecarts.append(f"{len(valeurs)} TXT _dmarc : n'en garder qu'un")
    if tags.get("v") != DMARC_TAGS_ATTENDUS["v"]:
        ecarts.append(f"`v={tags.get('v', 'absent')}` au lieu de `v=DMARC1`")
    if tags.get("p") != DMARC_TAGS_ATTENDUS["p"]:
        ecarts.append(
            f"`p={tags.get('p', 'absent')}` au lieu de `p=none` (phase de surveillance)"
        )
    rua = tags.get("rua", "")
    if not rua:
        ecarts.append(f"`rua` absent : ajouter {DMARC_RUA_ATTENDUE} pour recevoir les rapports")
    elif not any(adresse in rua for adresse in RUA_ACCEPTEES):
        ecarts.append(
            f"`rua` sans {DMARC_RUA_ATTENDUE} ni rua@dmarc.brevo.com (publie : {rua})"
        )
    if ecarts:
        return [Constat("different", "DMARC", "; ".join(ecarts) + f" - publie : {valeur}")]
    return [Constat("ok", "DMARC", valeur)]


def constats_dkim_brevo(domaine: str, serveur=None, outil=None) -> list:
    """Le DKIM que Brevo publie pour authentifier le domaine d'envoi.

    Brevo peut poser un **CNAME** (`mail._domainkey` ou `brevo._domainkey` vers
    une cible propre au compte) ou un TXT selon la configuration du compte : les
    deux sont cherches, et le nom exact affiche par Brevo reste la reference.
    """
    for nom_court in NOMS_DKIM_BREVO:
        nom = f"{nom_court}.{domaine}"
        cibles = cname(nom, serveur, outil)
        if cibles:
            return [Constat("ok", "DKIM Brevo", f"CNAME {nom} -> {cibles[0]}")]
        valeurs = txt(nom, serveur, outil)
        if valeurs:
            return [
                Constat(
                    "ok",
                    "DKIM Brevo",
                    f"TXT {nom} ({len(valeurs[0])} caracteres)",
                )
            ]
    return [
        Constat(
            "different",
            "DKIM Brevo",
            "aucun CNAME/TXT sur mail._domainkey ni brevo._domainkey : "
            "terminer l'authentification du domaine dans Brevo (Senders, "
            "Domains & Dedicated IPs > Domains > Authenticate), ou relancer "
            "avec --sans-brevo si Brevo n'est pas utilise",
        )
    ]


def controler(
    domaine: str,
    dc: str = DC_DEFAUT,
    dkim: str | None = None,
    cle_domaine: str = CLE_DOMAINE_DEFAUT,
    sans_brevo: bool = False,
    serveur: str | None = None,
    outil: str | None = None,
) -> list:
    """Tous les controles, dans l'ordre des documents de configuration."""
    outil = outil or outil_dns()
    constats: list = []
    constats += constats_mx(domaine, dc, serveur, outil)
    constats += constats_spf(domaine, dc, sans_brevo, serveur, outil)
    constats += constats_preuve_domaine(domaine, cle_domaine, serveur, outil)
    constats += constats_dkim(domaine, dkim, serveur, outil)
    if not sans_brevo:
        constats += constats_dkim_brevo(domaine, serveur, outil)
    constats += constats_dmarc(domaine, serveur, outil)
    return constats


def code_retour(constats: Sequence[Constat]) -> int:
    """1 si un enregistrement manque, 2 pour un ecart seul, sinon 0."""
    gravites = {constat.gravite for constat in constats}
    if "manquant" in gravites:
        return 1
    if "different" in gravites:
        return 2
    return 0


# --- Sortie et programme principal ------------------------------------------


def parseur() -> argparse.ArgumentParser:
    """Options de la ligne de commande."""
    analyseur = argparse.ArgumentParser(
        prog="verifier_dns_kder.py",
        description=(
            "Verifie les DNS de kder-ai.com (Zoho Mail + Brevo) avant le "
            "premier envoi d'address-validator."
        ),
        epilog=(
            "Codes de retour : 0 tout est pret, 1 enregistrement manquant, "
            "2 ecart seulement, 3 DNS inutilisable."
        ),
    )
    analyseur.add_argument(
        "--domaine",
        default=DOMAINE_DEFAUT,
        help=f"domaine teste (defaut : {DOMAINE_DEFAUT})",
    )
    analyseur.add_argument(
        "--dc",
        choices=("eu", "com"),
        default=DC_DEFAUT,
        help="datacenter Zoho : com (defaut, celui de kder-ai.com) ou eu",
    )
    analyseur.add_argument(
        "--dkim",
        default=os.environ.get("DKIM_ZOHO", ""),
        metavar='"v=DKIM1; k=rsa; p=..."',
        help=(
            "cle DKIM affichee par la console Zoho, pour comparaison "
            "(defaut : variable d'environnement DKIM_ZOHO)"
        ),
    )
    analyseur.add_argument(
        "--cle-domaine",
        default=CLE_DOMAINE_DEFAUT,
        help="valeur TXT zoho-verification attendue",
    )
    analyseur.add_argument(
        "--sans-brevo",
        action="store_true",
        help="ne pas exiger include:spf.brevo.com dans le SPF",
    )
    analyseur.add_argument(
        "--serveur",
        default=None,
        metavar="IP",
        help=(
            "resolveur DNS a interroger, ex. 8.8.8.8 (defaut : celui du "
            "systeme ; utile pour ecarter un cache local)"
        ),
    )
    analyseur.add_argument(
        "--json",
        action="store_true",
        help="sortie JSON, pour un script ou une trace",
    )
    return analyseur


def _recapituler(constats: Sequence[Constat]) -> tuple:
    """Phrase de recapitulatif, conclusion et code de retour."""
    comptes: dict = {}
    for constat in constats:
        comptes[constat.gravite] = comptes.get(constat.gravite, 0) + 1
    code = code_retour(constats)
    phrase = (
        f"{len(constats)} enregistrements controles : {comptes.get('ok', 0)} OK, "
        f"{comptes.get('manquant', 0)} manquant(s), "
        f"{comptes.get('different', 0)} ecart(s), "
        f"{comptes.get('non-verifie', 0)} non verifie(s)."
    )
    if code == 0 and comptes.get("non-verifie"):
        conclusion = "✅ DNS prets (DKIM non compare)."
    elif code == 0:
        conclusion = "✅ DNS prets."
    elif code == 1:
        conclusion = (
            "❌ Enregistrements manquants : reprendre "
            "docs/commercial/DNS-A-CONFIGURER.md, puis attendre la propagation."
        )
    else:
        conclusion = "⚠️  A corriger avant l'envoi : voir les ecarts ci-dessus."
    return phrase, conclusion, code


def _sortie_json(
    domaine: str, dc: str, quand: str, constats: Sequence[Constat], code: int
) -> str:
    """Rapport machine : memes constats que la sortie lisible."""
    return json.dumps(
        {
            "domaine": domaine,
            "datacenter": dc,
            "consulte_le": quand,
            "constats": [
                {
                    "gravite": constat.gravite,
                    "libelle": constat.libelle,
                    "detail": constat.detail,
                }
                for constat in constats
            ],
            "code": code,
        },
        ensure_ascii=False,
        indent=2,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entree : 0 pret, 1 manquant, 2 ecart, 3 DNS inutilisable."""
    _forcer_utf8()
    options = parseur().parse_args(argv)
    quand = datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M")
    if not options.json:
        print(
            f"=== DNS de {options.domaine} - datacenter Zoho : {options.dc} - "
            f"{quand} ==="
        )
    try:
        constats = controler(
            options.domaine,
            options.dc,
            options.dkim or None,
            options.cle_domaine,
            options.sans_brevo,
            options.serveur,
        )
    except ErreurDns as erreur:
        if options.json:
            print(
                json.dumps(
                    {
                        "domaine": options.domaine,
                        "consulte_le": quand,
                        "erreur": str(erreur),
                        "code": 3,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"❌ {erreur}")
        return 3

    phrase, conclusion, code = _recapituler(constats)
    if options.json:
        print(_sortie_json(options.domaine, options.dc, quand, constats, code))
        return code

    for constat in constats:
        print(constat.ligne())
    print()
    print(phrase)
    print(conclusion)
    if not options.dkim:
        print(
            "ℹ️  DKIM non compare : relancer avec --dkim "
            '"v=DKIM1; k=rsa; p=..." (valeur affichee dans la console Zoho, '
            "section 4.3 de docs/commercial/DNS-A-CONFIGURER.md)."
        )
    return code


if __name__ == "__main__":
    sys.exit(main())
