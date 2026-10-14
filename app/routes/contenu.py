"""GET /agents et /compare, dans les six langues — les pages de contenu (D66).

Deux pages publiques, écrites pour qu'on les trouve :

* `/agents` explique le service à qui construit un agent IA : découverte MCP
  gratuite, offre lue dans le 402, paiement x402, champs rendus, limites. Le lien
  « Serveur MCP » du pied de page y mène (`/mcp` répond 405 à un navigateur) ;
* `/compare` met côte à côte les prix publiés de Google, Smarty, PostGrid et
  Geoapify, relevés le 14/10/2026 sur leurs pages officielles, et les nôtres. Il
  dit **honnêtement** quand choisir un autre service (PostGrid moins cher aux
  États-Unis et au Canada, Google gratuit sous 5 000 validations par mois).

**Ce qui est montré est ce qui est appliqué** (D48) : nos prix, quotas, durée
d'essai, prix x402 et son nombre d'unités USDC, durée du cache et adresse du
service viennent du code et de la configuration. Les prix des autres services
sont des faits datés, écrits dans le gabarit avec leur source.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import accounts, i18n, seo
from app.cache import CACHE_TTL
from app.config import get_settings
from app.onchain import usdc_units
from app.web import public_headers

GABARITS = Path(__file__).resolve().parents[1] / "templates"
PAGES = ("agents", "compare")


def valeurs(settings, lang: str, page: str) -> dict[str, str]:
    """Les champs des deux gabarits, tous lus dans le code ou la configuration."""
    return {
        **i18n.liens(lang, page),
        "base": settings.public_base_url.rstrip("/"),
        "prix_x402": i18n.nombre(lang, settings.x402_price),
        "unites": str(usdc_units(settings.x402_price)),
        "jours_cache": i18n.nombre(lang, CACHE_TTL // 86400),
        "jours_essai": i18n.nombre(lang, settings.trial_days),
        "validations_essai": i18n.nombre(lang, accounts.PLANS["trial"]),
        "quota_starter": i18n.nombre(lang, accounts.PLANS["starter"]),
        "quota_pro": i18n.nombre(lang, accounts.PLANS["pro"]),
        **{f"prix_{cle}": i18n.prix(lang, cle) for cle in ("starter", "pro")},
        **{cle: i18n.prix(lang, cle) for cle in ("unitaire_starter", "unitaire_pro")},
        "mention_euros": i18n.mention_euros(lang),
        "contact": settings.contact_email,
    }


def page(settings, lang: str, nom: str) -> str:
    """Une page de contenu dans une langue, champs remplis et échappés."""
    texte = i18n.rendre(GABARITS / nom / f"{lang}.html", valeurs(settings, lang, nom))
    return seo.enrichir(texte, lang, nom, settings)


def _route(lang: str, nom: str):
    async def contenu() -> HTMLResponse:
        return HTMLResponse(page(get_settings(), lang, nom), headers=public_headers())

    return contenu


router = APIRouter()
# Chemins **littéraux** (`/en/agents`…), jamais `/{lang}/agents` (D57).
for _nom in PAGES:
    for _lang in i18n.LANGUES:
        router.add_api_route(i18n.chemin(_lang, _nom), _route(_lang, _nom), methods=["GET"],
                             response_class=HTMLResponse, include_in_schema=False)
