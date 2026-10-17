"""Vérification des adresses françaises par la Base Adresse Nationale (BAN).

Le fournisseur historique (SmartyStreets) est un service **américain** : une
adresse française ne l'intéresse pas et il renvoie la saisie telle quelle, sans
aucun code DPV. Le pipeline la déclarait pourtant « validée » (mesuré le
17/10/2026 : « 8 rue madelaine, Lyon, 75005 » -> 1 candidat, `dpv_match_code`
absent, `validated: true` dans le formulaire).

La France a sa propre base de référence, gratuite et officielle : la **Base
Adresse Nationale** (`api-adresse.data.gouv.fr`, service de l'État). Une adresse
française (métropole et outre-mer, dont La Réunion) est donc vérifiée là, et
**jamais** renvoyée à SmartyStreets : si la BAN ne répond pas, le résultat est
`degraded` — un repli sur un fournisseur qui ne sait pas juger produirait un
faux verdict.

Règles appliquées (ce qu'un client achète, c'est un verdict) :

* score BAN < `BAN_SEUIL_SCORE` (0,8) -> refus ;
* code postal retourné différent de celui saisi -> refus ;
* ville retournée différente de la ville saisie (accents, casse, tirets et
  ponctuation normalisés) -> refus ;
* type `municipality` ou `locality` (la ville seule, aucune voie reconnue) ->
  refus ;
* type `housenumber` -> point de livraison complet, type `street` -> voie
  reconnue : mêmes deux niveaux que les codes DPV `Y` et `S` du fournisseur
  américain (voir `scorer.py`).

Limite dite : la BAN publie l'existence d'une adresse, pas sa nature
(résidentiel / commercial) ; les deux types acceptés sont donc rendus comme
`residential`, par analogie avec les codes DPV `Y`/`S` déjà présentés ainsi.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import httpx

#: Service de recherche de la BAN (API publique de l'État, sans clé).
BAN_SEARCH_URL = "https://api-adresse.data.gouv.fr/search/"
#: Même budget que le fournisseur américain (D23) : une adresse FR ne doit pas
#: coûter plus cher en latence qu'une adresse US.
BAN_TIMEOUT = httpx.Timeout(connect=2.0, read=4.0, write=4.0, pool=2.0)
#: Score minimal publié par la BAN pour accepter une adresse.
BAN_SEUIL_SCORE = 0.8
#: Pays dont les adresses sont vérifiées par la BAN : France métropolitaine et
#: outre-mer. La Réunion (`RE`) est un cas d'usage du service (formulaire, exemples).
PAYS_BAN = frozenset({"FR", "RE", "GP", "MQ", "GF", "YT", "BL", "MF", "PM"})
#: Types BAN acceptables : une voie (`street`) ou un point de livraison (`housenumber`).
TYPES_VALIDES = frozenset({"housenumber", "street"})


def normaliser_ville(texte: str) -> str:
    """Forme comparable d'un nom de ville : sans accents, casse ni ponctuation."""
    decompose = unicodedata.normalize("NFKD", texte or "")
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sans_accents.casefold()).split())


def requete_ban(standardized: dict) -> str:
    """Texte envoyé à la BAN : voie + ville (la ville améliore le score)."""
    morceaux = [
        str(standardized.get("line1") or "").strip(),
        str(standardized.get("city") or "").strip(),
    ]
    return " ".join(morceau for morceau in morceaux if morceau)


def _call_ban(q: str, postcode: str) -> list[dict]:
    """Appel HTTP à la BAN. Séparé pour que les tests le remplacent (comme `_call_smarty`)."""
    parametres: dict[str, Any] = {"q": q, "limit": 5}
    if postcode:
        parametres["postcode"] = postcode
    with httpx.Client(timeout=BAN_TIMEOUT) as client:
        reponse = client.get(BAN_SEARCH_URL, params=parametres)
        reponse.raise_for_status()
        donnees = reponse.json()
    if not isinstance(donnees, dict) or donnees.get("type") != "FeatureCollection":
        raise ValueError(f"reponse BAN inattendue : {type(donnees).__name__}")
    features = donnees.get("features")
    return features if isinstance(features, list) else []


def _meilleur_resultat(features: list[dict]) -> dict:
    """Le résultat BAN le mieux noté (la BAN en renvoie jusqu'à `limit`)."""

    def score(feature: dict) -> float:
        return float((feature.get("properties") or {}).get("score") or 0.0)

    return max(features, key=score)


def verify_ban(standardized: dict) -> dict:
    """Vérifie une adresse française. Même contrat que `verifier.verify_address`.

    `{"valid": ..., "deliverability": ..., "metadata": ..., "raw": ...}`, plus
    `"error"` quand la BAN n'a pas pu être interrogée (le pipeline en fait
    `degraded: true`, jamais un verdict d'inexistence).
    """
    zip_saisi = str(standardized.get("zip") or "").strip()
    ville_saisie = normaliser_ville(str(standardized.get("city") or ""))
    voie = str(standardized.get("line1") or "").strip()
    q = requete_ban(standardized)
    if not voie or not zip_saisi:
        return {
            "valid": False,
            "deliverability": "invalid",
            "metadata": {"motif": "adresse incomplete : voie ou code postal absent"},
            "raw": None,
        }

    try:
        features = _call_ban(q, zip_saisi)
    except Exception as exc:  # noqa: BLE001 - panne fournisseur = resultat degrade, jamais un verdict
        return {
            "valid": False,
            "deliverability": "unknown",
            "metadata": {},
            "raw": None,
            "error": f"BAN injoignable : {type(exc).__name__}: {exc}",
        }

    if not features:
        return {
            "valid": False,
            "deliverability": "invalid",
            "metadata": {"motif": "aucun resultat BAN pour cette adresse", "requete": q},
            "raw": None,
        }

    meilleur = _meilleur_resultat(features)
    proprietes = meilleur.get("properties") or {}
    score = float(proprietes.get("score") or 0.0)
    postcode = str(proprietes.get("postcode") or "")
    ville = normaliser_ville(str(proprietes.get("city") or ""))
    type_resultat = str(proprietes.get("type") or "")
    ban = {
        "label": proprietes.get("label"),
        "score": score,
        "postcode": postcode,
        "city": proprietes.get("city"),
        "type": type_resultat,
        "id": proprietes.get("id"),
        "coordinates": (meilleur.get("geometry") or {}).get("coordinates"),
    }

    motifs: list[str] = []
    if score < BAN_SEUIL_SCORE:
        motifs.append(f"score {score:.3f} < {BAN_SEUIL_SCORE}")
    if postcode != zip_saisi:
        motifs.append(f"code postal {postcode!r} != {zip_saisi!r}")
    if ville != ville_saisie:
        motifs.append(f"ville {proprietes.get('city')!r} != {standardized.get('city')!r}")
    if type_resultat not in TYPES_VALIDES:
        motifs.append(f"type {type_resultat!r} : aucune voie reconnue")
    if motifs:
        return {
            "valid": False,
            "deliverability": "invalid",
            "metadata": {"motif": "; ".join(motifs), "ban": ban},
            "raw": meilleur,
        }

    return {
        "valid": True,
        "deliverability": "ban",
        "metadata": {"ban": ban},
        "raw": meilleur,
        "source": "ban",
        "type": type_resultat,
    }
