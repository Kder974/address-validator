"""Langues du site public (D57) : adresses, nombres, prix par monnaie, gabarits.

Le site parle six langues. Le français reste à la racine (`/`, `/pricing`), les
autres sont préfixées (`/en/`, `/en/pricing`) : les adresses déjà publiées ne
changent pas. `chemin()` est la **seule** fabrique d'adresses du site public :
navigation, sélecteur de langue et `hreflang` en sortent tous (`liens()`).

Prix (décision du 14/10/2026, `PRIX-PAR-LANGUE.md`) : Kder facture en **euros** ;
chaque langue affiche la conversion arrondie de ces euros dans sa monnaie, avec
la mention « facturé en euros » (sauf fr/es, qui affichent déjà l'euro). Les
montants en euros viennent du code (`pricing.OFFRES`) : ils ne sont pas recopiés
ici, ce qui fait échouer les tests le jour où le prix change sans que la table
convertie soit refaite. Le paiement à l'appel (USDC), les quotas et « Enterprise
sur devis » ne se convertissent pas.

`rendre()` généralise le gabarit de la vitrine (D53) : un fichier HTML dont les
champs `{{nom}}` sont remplis par le code, échappés, et dont un champ oublié lève
une erreur au lieu de publier `{{…}}` en clair.
"""

from __future__ import annotations

import html
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from app.routes.pricing import OFFRES

LANGUES = ("fr", "en", "zh", "hi", "es", "ar")
DEFAUT = "fr"
PREFIXEES = tuple(lang for lang in LANGUES if lang != DEFAUT)
RTL = frozenset({"ar"})

#: Pages publiques, par leur adresse française (`""` = l'accueil).
PAGES = ("", "pricing", "guide", "validate-form", "signup", "compte")


def chemin(lang: str, page: str = "") -> str:
    """Adresse d'une page : `/pricing` en français, `/en/pricing` ailleurs."""
    if lang not in LANGUES or page not in PAGES:
        raise ValueError(f"page inconnue : {lang!r}, {page!r}")
    return ("" if lang == DEFAUT else f"/{lang}") + f"/{page}"


def liens(lang: str, page: str = "") -> dict[str, str]:
    """Champs d'adresse communs aux gabarits.

    `url_<page>` : la navigation dans la langue de la page ; `lien_<langue>` : la
    **même** page dans chaque langue (sélecteur de langue et `hreflang`).
    """
    champs = {"url_" + (p or "accueil").replace("-", "_"): chemin(lang, p) for p in PAGES}
    champs.update({f"lien_{autre}": chemin(autre, page) for autre in LANGUES})
    return champs


#: Séparateurs (milliers, décimale), tels que les maquettes validées les écrivent.
SEPARATEURS = {
    "fr": (" ", ","),
    "es": (".", ","),
    "en": (",", "."),
    "zh": (",", "."),
    "hi": (",", "."),
    "ar": (",", "."),
}


def nombre(lang: str, valeur) -> str:
    """2 000 / 2.000 / 2,000 et 0,02 / 0.02 selon la langue."""
    if isinstance(valeur, float):
        # ponytail: `:g` garde 6 chiffres significatifs, assez pour un prix ou un quota.
        valeur = Decimal(f"{valeur:g}")
    milliers, decimale = SEPARATEURS[lang]
    return f"{valeur:,}".translate({ord(","): milliers, ord("."): decimale})


#: Date des taux de conversion. Les changer, c'est refaire `CONVERTIS` **et** les
#: phrases de `MENTION_EUROS`, qui la citent.
DATE_TAUX = "2026-09"
#: Taux retenus à `DATE_TAUX` (approximatifs) : ce qu'un euro vaut dans chaque monnaie.
TAUX = {"USD": Decimal("1.14"), "CNY": Decimal("7.70"), "INR": Decimal("109")}

DEVISE = {"fr": "EUR", "es": "EUR", "en": "USD", "ar": "USD", "zh": "CNY", "hi": "INR"}
SYMBOLE = {"USD": "$", "CNY": "¥", "INR": "₹"}


def _offre(nom: str) -> dict:
    return next(offre for offre in OFFRES if offre["nom"] == nom)


def _euros(nom: str) -> Decimal:
    return Decimal(f"{_offre(nom)['prix_eur']:g}")


#: Les montants en euros : ceux que l'API facture (`pricing.OFFRES`), prix
#: unitaire **calculé** comme sur `/pricing` (D43). Seul le colis raté (15 € /
#: 15–26 €) n'existe pas en euros dans le code.
EUROS = {
    "essai": Decimal(0),
    "starter": _euros("Starter"),
    "pro": _euros("Pro"),
    "unitaire_starter": _euros("Starter") / _offre("Starter")["validations"],
    "unitaire_pro": _euros("Pro") / _offre("Pro")["validations"],
    "colis_rate": Decimal(15),
    "colis_rate_max": Decimal(26),
}

#: Table de `PRIX-PAR-LANGUE.md`, recopiée telle quelle : conversion arrondie de
#: 29 € et 79 € aux `TAUX`. `tests/test_i18n.py` et la porte D57 vérifient qu'elle
#: suit les euros du code (à 1 % près) et que le prix unitaire vaut le prix divisé
#: par le quota : changer `pricing.OFFRES` sans refaire cette table les fait échouer.
CONVERTIS = {
    "USD": {
        "essai": "0", "starter": "33", "pro": "90",
        "unitaire_starter": "0.0165", "unitaire_pro": "0.0090",
        "colis_rate": "17", "colis_rate_max": "30",
    },
    "CNY": {
        "essai": "0", "starter": "223", "pro": "608",
        "unitaire_starter": "0.1115", "unitaire_pro": "0.0608",
        "colis_rate": "115", "colis_rate_max": "200",
    },
    "INR": {
        "essai": "0", "starter": "3160", "pro": "8620",
        "unitaire_starter": "1.58", "unitaire_pro": "0.862",
        "colis_rate": "1600", "colis_rate_max": "2900",
    },
}
MONTANTS = {
    "EUR": EUROS,
    **{devise: {cle: Decimal(v) for cle, v in table.items()} for devise, table in CONVERTIS.items()},
}

#: Clés de `prix()`, dans l'ordre de la table (`colis_rate_max` n'existe que dans la fourchette).
CLES_PRIX = (
    "essai", "starter", "pro", "unitaire_starter", "unitaire_pro",
    "colis_rate", "colis_rate_fourchette",
)


def prix(lang: str, cle: str) -> str:
    """Un prix dans la monnaie de la langue : « 29 € », « $33 », « ¥223 », « ₹3,160 »."""
    devise = DEVISE[lang]
    montants = MONTANTS[devise]
    if cle == "colis_rate_fourchette":
        texte = f"{nombre(lang, montants['colis_rate'])}–{nombre(lang, montants['colis_rate_max'])}"
    else:
        texte = nombre(lang, montants[cle])
    return f"{texte} €" if devise == "EUR" else SYMBOLE[devise] + texte


#: Mention obligatoire près d'un prix converti. En arabe, U+2066…U+2069 isolent
#: « €29 » de gauche à droite, comme le `<span dir="ltr">` de la maquette :
#: sans eux, l'algorithme bidirectionnel affiche « 29€ ».
MENTION_EUROS = {
    "fr": "",
    "es": "",
    "en": "Approximate prices. Billed in euros: €{starter} and €{pro} per month, "
          "excl. VAT (exchange rate of September 2026).",
    "zh": "价格为约数。按欧元计费：每月 {starter} 欧元和 {pro} 欧元，不含税（2026 年 9 月汇率）。",
    "hi": "अनुमानित कीमतें। बिलिंग यूरो में होती है: €{starter} और €{pro} प्रति माह, "
          "कर के बिना (सितंबर 2026 की विनिमय दर)।",
    "ar": "أسعار تقريبية. تتم الفوترة باليورو: ⁦€{starter}⁩ و⁦€{pro}⁩ "
          "شهريًا قبل الضريبة (سعر الصرف في سبتمبر 2026).",
}


def mention_euros(lang: str) -> str:
    """« Billed in euros: €29 and €79 … » dans la langue ; vide en fr/es."""
    return MENTION_EUROS[lang].format(
        starter=nombre(lang, EUROS["starter"]), pro=nombre(lang, EUROS["pro"])
    )


_CHAMP = re.compile(r"\{\{(\w+)\}\}")


@lru_cache(maxsize=None)
def _lire(chemin_gabarit: str) -> str:
    return Path(chemin_gabarit).read_text(encoding="utf-8")


def rendre(chemin_gabarit, valeurs: dict) -> str:
    """Remplit les champs `{{nom}}` d'un gabarit, valeurs échappées.

    Un seul passage : une valeur n'est jamais relue comme un champ. Un champ sans
    valeur, ou un `{{` mal écrit dans le gabarit, lève `RuntimeError`. Seul le
    gabarit est contrôlé : un visiteur qui tape « {{ » (nom de boutique, adresse)
    le voit s'afficher, il ne fait pas tomber la page en `500`.
    """
    nom_fichier = Path(chemin_gabarit).name
    gabarit = _lire(str(chemin_gabarit))
    if "{{" in _CHAMP.sub("", gabarit):
        raise RuntimeError(f"gabarit {nom_fichier} : un champ est mal écrit")

    def remplir(trouve: re.Match) -> str:
        if trouve.group(1) not in valeurs:
            raise RuntimeError(f"gabarit {nom_fichier} : le champ {trouve.group(1)} n'est pas rempli")
        return html.escape(str(valeurs[trouve.group(1)]), quote=True)

    return _CHAMP.sub(remplir, gabarit)
