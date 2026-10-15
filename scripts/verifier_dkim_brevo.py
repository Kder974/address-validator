#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verificateur du DKIM Brevo (tache 5) : deux selecteurs, un verdict code.

QUI   : l'exploitant, juste apres avoir colle les enregistrements dans le DNS du
        domaine, et apres toute modification DNS.
QUOI  : interroge les **deux** selecteurs que Brevo utilise pour authentifier le
        domaine d'envoi (`mail._domainkey`, `brevo._domainkey`) et rend un verdict
        unique et lisible par un script : `ok`, `manquant` ou `different`.
COMMENT : CNAME puis TXT sur chaque selecteur, avec l'outil DNS du poste (`dig`,
        sinon `nslookup`), donc avec exactement le meme code que
        `scripts/verifier_dns_kder.py` (importe, pas recopie : une seule
        implementation DNS, une seule table de symboles). Une cible est conforme
        si elle est hebergee par Brevo (`.dkim.brevo.com`) ; une cle TXT est
        conforme si elle commence par `v=DKIM1`. Avec `--attendu`, c'est la
        valeur exacte affichee par la console Brevo qui tranche.
QUAND : apres la pose des enregistrements (la propagation prend de quelques
        minutes a quelques heures), puis une fois par mois avec le rapport.
OU    : scripts/verifier_dkim_brevo.py, lance depuis la racine du depot :
        .venv\\Scripts\\python.exe scripts\\verifier_dkim_brevo.py
        .venv\\Scripts\\python.exe scripts\\verifier_dkim_brevo.py --json
        .venv\\Scripts\\python.exe scripts\\verifier_dkim_brevo.py --attendu "mail._domainkey=b1.kder-ai-com.dkim.brevo.com"
FAIT QUOI EXACTEMENT : affiche un constat par selecteur puis le verdict, ou tout
        en JSON (`--json` : cles `selecteurs` et `verdict`, le verdict portant
        `code`, `publies`, `total`, `detail` et la liste de ce qu'il reste a
        publier). Le verdict ne se devine pas :

        * `ok`        — les **deux** selecteurs publies et conformes ;
        * `different` — un selecteur publie mais pas conforme (cible hors Brevo,
                        cle TXT qui n'est pas une cle DKIM, ou valeur differente
                        de celle annoncee par `--attendu`), **ou** un seul des
                        deux publie : Brevo signe avec les deux, un demi-DKIM
                        n'authentifie rien ;
        * `manquant`  — aucun des deux selecteurs n'a d'enregistrement ;
        * `non-verifie` — le DNS n'a pas pu etre interroge : dans ce cas il n'y a
                        **pas** de verdict, et le code de retour le dit.

Codes de retour : 0 `ok`, 1 `manquant`, 2 `different`, 3 DNS non interrogeable,
4 erreur d'appel (`--attendu`) — voir `docs/commercial/DKIM-BREVO-A-CONFIGURER.md`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

#: Le socle DNS vit dans `verifier_dns_kder.py` : importer le dossier de ce
#: fichier (les scripts ne sont pas un paquet) evite de reecrire `dig`/`nslookup`,
#: la detection des pannes de resolution et les symboles d'affichage.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from verifier_dns_kder import (  # noqa: E402
    Constat,
    ErreurDns,
    SYMBOLES,
    _forcer_utf8,
    cname,
    outil_dns,
    txt,
)

#: Domaine dont Brevo authentifie l'envoi.
DOMAINE_DEFAUT = "kder-ai.com"

#: Les deux selecteurs de Brevo. Ils sont interroges tous les deux : Brevo signe
#: avec les deux, donc en verifier un seul ne dirait rien de l'authentification.
SELECTEURS = ("mail._domainkey", "brevo._domainkey")

#: Fragment qui trahit un DKIM heberge par Brevo dans une cible CNAME.
CIBLE_BREVO = ".dkim.brevo.com"

#: Debut d'une cle DKIM publiee en TXT.
PREFIXE_CLE = "v=dkim1"

#: Document qui porte les valeurs a coller.
DOCUMENT = "docs/commercial/DKIM-BREVO-A-CONFIGURER.md"


@dataclass
class Selecteur:
    """Etat d'un selecteur : ce qui est publie, et ce qu'on en conclut."""

    nom: str
    gravite: str  # ok | manquant | different | non-verifie
    detail: str
    type_: str | None = None
    valeur: str | None = None

    def constat(self) -> "Constat":
        """Le constat affichable, dans la forme de `verifier_dns_kder`."""
        return Constat(self.gravite, self.nom, self.detail)

    def en_dictionnaire(self) -> dict:
        """Forme JSON : le nom, le type, la valeur publiee, la gravite."""
        return {
            "nom": self.nom,
            "type": self.type_,
            "valeur": self.valeur,
            "gravite": self.gravite,
            "detail": self.detail,
        }


@dataclass
class Verdict:
    """Verdict unique : un code, un compte, et la liste de ce qui manque."""

    code: str
    detail: str
    publies: int = 0
    total: int = len(SELECTEURS)
    a_publier: list = field(default_factory=list)

    def en_dictionnaire(self) -> dict:
        return {
            "code": self.code,
            "libelle": LIBELLES.get(self.code, self.code),
            "publies": self.publies,
            "total": self.total,
            "detail": self.detail,
            "a_publier": list(self.a_publier),
        }


#: Libelle lisible de chaque code de verdict.
LIBELLES = {
    "ok": "DKIM Brevo en place",
    "manquant": "DKIM Brevo absent",
    "different": "DKIM Brevo incomplet ou divergent",
    "non-verifie": "verdict impossible (DNS non interrogeable)",
}


def _normaliser(valeur: str) -> str:
    """Valeur comparable : guillemets, espaces et retours a la ligne retires."""
    return "".join(valeur.split()).strip('"').lower()


def est_cible_brevo(cible: str) -> bool:
    """Vrai si la cible CNAME est hebergee par Brevo (`*.dkim.brevo.com`)."""
    minuscule = (cible or "").strip().rstrip(".").lower()
    return CIBLE_BREVO in minuscule or minuscule.endswith(".brevo.com")


def est_cle_dkim(valeur: str) -> bool:
    """Vrai si la valeur TXT est une cle DKIM publiee (`v=DKIM1; k=rsa; p=...`)."""
    return _normaliser(valeur).startswith(PREFIXE_CLE)


# --- Interrogation d'un selecteur -------------------------------------------


def interroger_selecteur(
    nom_court: str,
    domaine: str,
    attendu: str | None = None,
    serveur: str | None = None,
    outil: str | None = None,
) -> Selecteur:
    """CNAME puis TXT sur `<nom_court>.<domaine>`.

    Le CNAME est cherche d'abord : c'est la forme que Brevo annonce pour un
    domaine a authentifier. Un TXT est accepte en second parce qu'un compte peut
    publier la cle directement : le nom et la valeur exacts restent la reference,
    et `--attendu` sert alors a comparer au lieu de deviner.

    Leve `ErreurDns` si le DNS n'a pas repondu — un doute n'est pas un manquant.
    """
    nom = f"{nom_court}.{domaine}"
    cibles = cname(nom, serveur, outil)
    if cibles:
        cible = cibles[0]
        if attendu and _normaliser(cible) != _normaliser(attendu):
            return Selecteur(
                nom,
                "different",
                f"CNAME {nom} -> {cible} alors que Brevo annonce {attendu}",
                "CNAME",
                cible,
            )
        if not attendu and not est_cible_brevo(cible):
            return Selecteur(
                nom,
                "different",
                f"CNAME {nom} -> {cible} : cible hors Brevo (attendu *{CIBLE_BREVO})",
                "CNAME",
                cible,
            )
        return Selecteur(nom, "ok", f"CNAME {nom} -> {cible}", "CNAME", cible)

    valeurs = txt(nom, serveur, outil)
    if valeurs:
        valeur = valeurs[0]
        if attendu and _normaliser(valeur) != _normaliser(attendu):
            return Selecteur(
                nom,
                "different",
                f"TXT {nom} publie, different de la cle annoncee par Brevo",
                "TXT",
                valeur,
            )
        if not attendu and not est_cle_dkim(valeur):
            return Selecteur(
                nom,
                "different",
                f"TXT {nom} publie mais ce n'est pas une cle DKIM "
                f"(attendu un texte commencant par {PREFIXE_CLE})",
                "TXT",
                valeur,
            )
        return Selecteur(
            nom, "ok", f"TXT {nom} ({len(valeur)} caracteres)", "TXT", valeur
        )

    return Selecteur(
        nom,
        "manquant",
        f"aucun CNAME ni TXT sur {nom} : coller l'enregistrement affiche par "
        f"Brevo (voir {DOCUMENT})",
    )


def verdict(selecteurs: Sequence[Selecteur]) -> Verdict:
    """Verdict unique, dans l'ordre : pas de verdict, manquant, different, ok."""
    total = len(selecteurs)
    inconnus = [s for s in selecteurs if s.gravite == "non-verifie"]
    if inconnus:
        return Verdict(
            "non-verifie",
            "verdict impossible : " + "; ".join(s.detail for s in inconnus),
            0,
            total,
            [s.nom for s in inconnus],
        )

    publies = [s for s in selecteurs if s.type_]
    if not publies:
        return Verdict(
            "manquant",
            "aucun des deux selecteurs n'est publie : Brevo ne peut pas signer "
            "les messages du domaine",
            0,
            total,
            [s.nom for s in selecteurs],
        )

    divergents = [s for s in selecteurs if s.gravite == "different"]
    manquants = [s for s in selecteurs if s.gravite == "manquant"]
    if divergents:
        return Verdict(
            "different",
            f"{len(divergents)} selecteur(s) publie(s) mais non conforme(s) : "
            + "; ".join(s.detail for s in divergents),
            len(publies),
            total,
            [s.nom for s in selecteurs if s.gravite != "ok"],
        )
    if manquants:
        return Verdict(
            "different",
            f"{len(publies)} selecteur(s) sur {total} publie(s) : Brevo signe "
            "avec les deux, un demi-DKIM n'authentifie rien — manquant(s) : "
            + ", ".join(s.nom for s in manquants),
            len(publies),
            total,
            [s.nom for s in manquants],
        )
    return Verdict(
        "ok",
        f"les {total} selecteurs sont publies et conformes",
        len(publies),
        total,
    )


# --- Rapport, affichage et programme principal -------------------------------


def rapport(
    domaine: str = DOMAINE_DEFAUT,
    noms: Sequence[str] = SELECTEURS,
    attendus: dict | None = None,
    serveur: str | None = None,
    outil: str | None = None,
) -> tuple[dict, int]:
    """`(rapport, code de retour)`. Les deux selecteurs sont toujours listes.

    Un selecteur non interrogeable ne disparait pas du rapport : il y apparait en
    `non-verifie`, avec la raison, et le verdict n'est alors **pas** rendu — un
    DNS muet n'est pas un enregistrement absent.
    """
    attendus = attendus or {}
    try:
        outil_utilise = outil or outil_dns()
    except ErreurDns as erreur:
        etats = [
            Selecteur(f"{nom}.{domaine}", "non-verifie", str(erreur)) for nom in noms
        ]
        return _composer(domaine, None, etats), 3

    etats = []
    for nom in noms:
        try:
            etats.append(
                interroger_selecteur(
                    nom, domaine, attendus.get(nom), serveur, outil_utilise
                )
            )
        except ErreurDns as erreur:
            etats.append(Selecteur(f"{nom}.{domaine}", "non-verifie", str(erreur)))

    verdict_final = verdict(etats)
    codes = {"ok": 0, "manquant": 1, "different": 2, "non-verifie": 3}
    return _composer(domaine, outil_utilise, etats, verdict_final), codes[
        verdict_final.code
    ]


def _composer(
    domaine: str,
    outil_utilise: str | None,
    selecteurs: Sequence[Selecteur],
    verdict_final: Verdict | None = None,
) -> dict:
    """Le rapport, dans une forme stable : `selecteurs` puis `verdict`."""
    return {
        "horodatage": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "domaine": domaine,
        "outil": outil_utilise,
        "document": DOCUMENT,
        "selecteurs": [selecteur.en_dictionnaire() for selecteur in selecteurs],
        "verdict": (verdict_final or verdict(selecteurs)).en_dictionnaire(),
    }


def _afficher(sortie: dict) -> None:
    """Un constat par selecteur, puis le verdict et ce qu'il reste a faire."""
    print(
        f"=== DKIM Brevo - {sortie['horodatage']} - {sortie['domaine']} "
        f"(outil : {sortie['outil'] or 'indisponible'}) ==="
    )
    for entree in sortie["selecteurs"]:
        print(
            Constat(entree["gravite"], entree["nom"], entree["detail"]).ligne()
        )
    verdict_final = sortie["verdict"]
    print(
        f"verdict : {verdict_final['code'].upper()} — {verdict_final['libelle']} "
        f"({verdict_final['publies']}/{verdict_final['total']} selecteur(s) publie(s))"
    )
    print(f"  {verdict_final['detail']}")
    for nom in verdict_final["a_publier"]:
        print(f"  a publier : {nom}")
    print(f"valeurs a coller : {sortie['document']}")


def parseur() -> argparse.ArgumentParser:
    """Options de la ligne de commande."""
    analyseur = argparse.ArgumentParser(
        prog="verifier_dkim_brevo.py",
        description=(
            "Verifie les deux selecteurs DKIM de Brevo et rend un verdict code "
            "(ok, manquant, different)."
        ),
        epilog=f"Valeurs a coller dans le DNS : {DOCUMENT}",
    )
    analyseur.add_argument(
        "--domaine",
        default=DOMAINE_DEFAUT,
        help=f"domaine dont Brevo authentifie l'envoi (defaut : {DOMAINE_DEFAUT})",
    )
    analyseur.add_argument(
        "--selecteur",
        action="append",
        dest="selecteurs",
        metavar="NOM",
        help="selecteur a interroger, repetable (defaut : les deux de Brevo)",
    )
    analyseur.add_argument(
        "--attendu",
        action="append",
        default=[],
        metavar="SELECTEUR=CIBLE",
        help=(
            "valeur exacte affichee par Brevo pour un selecteur, repetable : "
            "compare au lieu de deviner"
        ),
    )
    analyseur.add_argument(
        "--serveur", help="serveur DNS a interroger (defaut : celui du systeme)"
    )
    analyseur.add_argument(
        "--json", action="store_true", help="sortie JSON (selecteurs + verdict)"
    )
    return analyseur


def _attendus(bruts: Sequence[str]) -> dict:
    """`SELECTEUR=CIBLE` -> dictionnaire. Une entree sans `=` est refusee."""
    attendus = {}
    for brut in bruts:
        if "=" not in brut:
            raise ValueError(f"--attendu attend SELECTEUR=CIBLE, recu {brut!r}")
        nom, valeur = brut.split("=", 1)
        attendus[nom.strip()] = valeur.strip()
    return attendus


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entree : un rapport, un code de retour."""
    options = parseur().parse_args(argv)
    _forcer_utf8()
    try:
        attendus = _attendus(options.attendu)
    except ValueError as erreur:
        print(f"erreur d'appel : {erreur}")
        return 4

    sortie, code = rapport(
        domaine=options.domaine,
        noms=tuple(options.selecteurs or SELECTEURS),
        attendus=attendus,
        serveur=options.serveur,
    )
    if options.json:
        print(json.dumps(sortie, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _afficher(sortie)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
