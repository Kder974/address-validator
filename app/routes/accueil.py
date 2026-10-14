"""Page d'accueil publique (D53, D57) : la vitrine, en six langues.

`GET /` répondait du JSON à tout le monde : une boutique qui tapait `kder-ai.com`
voyait `{"service": ...}`. Un navigateur (qui annonce `text/html`) reçoit désormais
la vitrine française ; un programme garde **exactement** le JSON d'avant, que les
tests, la recette (`scripts/e2e_check.py`) et les portes lisent. Les autres langues
vivent sous leur préfixe (`/en/`, `/zh/`, `/hi/`, `/es/`, `/ar/`, D57) et sont
toujours du HTML : aucun programme ne les lit.

Le dessin vient des maquettes validées (`docs/maquettes/site-multilingue/`) et vit
dans `app/templates/accueil/<langue>.html` : c'est surtout du CSS, qu'une chaîne
Python rendrait illisible. Il respecte la CSP des pages publiques (D43) : aucun
script, aucune ressource externe, animations en CSS et SVG inline ; les seules
images sont celles du site (`/images/`, `img-src 'self'`, D57).

Les chiffres ne sont pas dans les gabarits, ils y sont **injectés** (D48 : ce qui est
montré est ce qui est appliqué) : durée et quota de l'essai depuis `app/accounts.py`,
quotas des plans depuis `accounts.PLANS`, prix des plans depuis `pricing.OFFRES`
(convertis par `app/i18n.py` hors fr/es), prix x402 et contact depuis la
configuration. Un champ oublié lève une erreur au lieu de publier un `{{...}}`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import accounts, i18n
from app.config import get_settings
from app.web import public_headers

GABARITS = Path(__file__).resolve().parents[1] / "templates" / "accueil"


def veut_html(accept: str) -> bool:
    """Un navigateur annonce `text/html` ; un client qui préfère JSON le garde."""
    # ponytail: pas de lecture des poids q= ; « application/json » cité l'emporte toujours.
    return "text/html" in accept and "application/json" not in accept


def valeurs(settings, lang: str = i18n.DEFAUT) -> dict[str, str]:
    """Les champs du gabarit, tous lus dans le code ou la configuration."""
    return {
        **i18n.liens(lang),
        "jours_essai": i18n.nombre(lang, accounts.JOURS_ESSAI),
        "validations_essai": i18n.nombre(lang, accounts.VALIDATIONS_ESSAI),
        "prix_essai": i18n.prix(lang, "essai"),
        "prix_starter": i18n.prix(lang, "starter"),
        "prix_pro": i18n.prix(lang, "pro"),
        "mention_euros": i18n.mention_euros(lang),
        "quota_starter": i18n.nombre(lang, accounts.PLANS["starter"]),
        "quota_pro": i18n.nombre(lang, accounts.PLANS["pro"]),
        "prix_x402": i18n.nombre(lang, settings.x402_price),
        "contact": settings.contact_email,
    }


def page(settings, lang: str = i18n.DEFAUT) -> str:
    """La vitrine complète dans une langue, champs remplis et échappés."""
    return i18n.rendre(GABARITS / f"{lang}.html", valeurs(settings, lang))


router = APIRouter()


def _vitrine(lang: str):
    async def vitrine() -> HTMLResponse:
        return HTMLResponse(page(get_settings(), lang), headers=public_headers(form=True))

    return vitrine


# Chemins **littéraux** (`/en/`…), jamais `/{lang}/` : un paramètre de chemin
# avalerait d'autres routes (`/.well-known/…`, `/mcp/…`).
for _lang in i18n.PREFIXEES:
    router.add_api_route(i18n.chemin(_lang), _vitrine(_lang), methods=["GET"], include_in_schema=False)
