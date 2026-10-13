"""Page d'accueil publique (D53) : la vitrine servie aux navigateurs sur `GET /`.

`GET /` répondait du JSON à tout le monde : une boutique qui tapait `kder-ai.com`
voyait `{"service": ...}`. Un navigateur (qui annonce `text/html`) reçoit désormais
la vitrine ; un programme garde **exactement** le JSON d'avant, que les tests, la
recette (`scripts/e2e_check.py`) et les portes lisent.

Le dessin vient de la maquette validée (`docs/maquettes/page-accueil/`) et vit dans
`app/templates/accueil.html` : c'est surtout du CSS, qu'une chaîne Python rendrait
illisible. Il respecte la même CSP que les autres pages publiques (D43) : aucun
script, aucune ressource externe, animations en CSS et SVG inline.

Les chiffres ne sont pas dans le gabarit, ils y sont **injectés** (D48 : ce qui est
montré est ce qui est appliqué) : durée et quota de l'essai depuis `app/accounts.py`,
quotas des plans depuis `accounts.PLANS`, prix des plans depuis `pricing.OFFRES`, prix
x402 et contact depuis la configuration. Un champ oublié lève une erreur au lieu de
publier un `{{...}}` en clair.
"""

from __future__ import annotations

import html
from functools import lru_cache
from pathlib import Path

from app import accounts
from app.routes.pricing import OFFRES

GABARIT = Path(__file__).resolve().parents[1] / "templates" / "accueil.html"


def veut_html(accept: str) -> bool:
    """Un navigateur annonce `text/html` ; un client qui préfère JSON le garde."""
    # ponytail: pas de lecture des poids q= ; « application/json » cité l'emporte toujours.
    return "text/html" in accept and "application/json" not in accept


@lru_cache(maxsize=1)
def _gabarit() -> str:
    return GABARIT.read_text(encoding="utf-8")


def _milliers(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def valeurs(settings) -> dict[str, str]:
    """Les champs du gabarit, tous lus dans le code ou la configuration."""
    prix = {offre["nom"]: offre["prix"] for offre in OFFRES}
    return {
        "jours_essai": str(accounts.JOURS_ESSAI),
        "validations_essai": str(accounts.VALIDATIONS_ESSAI),
        "prix_starter": prix["Starter"],
        "prix_pro": prix["Pro"],
        "quota_starter": _milliers(accounts.PLANS["starter"]),
        "quota_pro": _milliers(accounts.PLANS["pro"]),
        "prix_x402": f"{settings.x402_price:g}".replace(".", ","),
        "contact": settings.contact_email,
    }


def page(settings) -> str:
    """La vitrine complète, champs remplis et échappés."""
    texte = _gabarit()
    for nom, valeur in valeurs(settings).items():
        texte = texte.replace("{{" + nom + "}}", html.escape(valeur, quote=True))
    if "{{" in texte:
        raise RuntimeError("gabarit d'accueil : un champ n'est pas rempli")
    return texte
