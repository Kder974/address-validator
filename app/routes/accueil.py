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

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import accounts, i18n, seo
from app.cache import CACHE_TTL
from app.config import get_settings
from app.web import public_headers

GABARITS = Path(__file__).resolve().parents[1] / "templates" / "accueil"


def veut_html(accept: str, agent: str = "") -> bool:
    """Un navigateur annonce `text/html` ; un robot de moteur reçoit aussi la vitrine (D65).

    Bing, GPTBot, ClaudeBot ou Perplexity envoient `*/*` : ils recevaient le JSON de
    l'API et ne voyaient pas le site. Un client qui cite `application/json` le garde.
    """
    # ponytail: pas de lecture des poids q= ; « application/json » cité l'emporte toujours.
    if "application/json" in accept:
        return False
    return "text/html" in accept or seo.est_robot(agent)


def _par_adresse(lang: str, montant: Decimal) -> str:
    """Un prix par adresse (D87) : « 0.9¢ » sur une page en dollars, sinon comme `i18n.prix`."""
    devise = i18n.DEVISE[lang]
    if devise == "USD":
        centimes = (montant * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)
        if centimes == centimes.to_integral():
            centimes = centimes.quantize(Decimal(1))
        return f"{i18n.nombre(lang, centimes)}¢"
    texte = i18n.nombre(lang, montant)
    return f"{texte} €" if devise == "EUR" else i18n.SYMBOLE[devise] + texte


def _plafond(montant: Decimal) -> Decimal:
    """Arrondi au-dessus, à un chiffre significatif : 0,0165 -> 0,02 ; 1,58 -> 2."""
    pas = Decimal(1).scaleb(montant.adjusted())
    return (montant / pas).quantize(Decimal(1), ROUND_CEILING) * pas


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
        # D87 : prix par adresse, dans la monnaie de la page (« 0.9¢ », « 0,0079 € »).
        **{cle: _par_adresse(lang, i18n.MONTANTS[i18n.DEVISE[lang]][f"unitaire_{offre}"])
           for cle, offre in (("unite_starter", "starter"), ("unite_pro", "pro"))},
        "moins_de": _par_adresse(lang, _plafond(i18n.MONTANTS[i18n.DEVISE[lang]]["unitaire_starter"])),
        "jours_cache": i18n.nombre(lang, CACHE_TTL // 86400),
    }


def page(settings, lang: str = i18n.DEFAUT) -> str:
    """La vitrine complète dans une langue, champs remplis et échappés."""
    return seo.enrichir(i18n.rendre(GABARITS / f"{lang}.html", valeurs(settings, lang)), lang, "", settings)


router = APIRouter()


def _vitrine(lang: str):
    async def vitrine() -> HTMLResponse:
        return HTMLResponse(page(get_settings(), lang), headers=public_headers(form=True))

    return vitrine


# Chemins **littéraux** (`/en/`…), jamais `/{lang}/` : un paramètre de chemin
# avalerait d'autres routes (`/.well-known/…`, `/mcp/…`).
for _lang in i18n.PREFIXEES:
    router.add_api_route(i18n.chemin(_lang), _vitrine(_lang), methods=["GET"], include_in_schema=False)
