"""`GET /checklist` — « La Checklist Anti-Colis Perdu », page gratuite en français (D98).

Le contenu vient de `docs/commercial/produits/01_checklist.md` : 27 contrôles en trois blocs,
un exemple de coût évité, puis les offres. La page n'existe qu'en français : elle n'entre ni
dans `i18n.PAGES` ni dans `seo.PUBLIQUES` (qui exigent les six langues), et elle est liée depuis
l'accueil, les tarifs et le guide français.

Comme toutes les pages publiques : aucun script, aucune ressource externe, et chaque chiffre
vient du code (D48). Le coût d'un colis en échec est l'ordre de grandeur que la page Tarifs
affiche déjà (`i18n.EUROS`, « non mesuré par notre service ») ; le taux de 2 % n'est qu'une
hypothèse d'exemple, dite comme telle. Pas d'e-mail demandé, rien d'enregistré : la page se
coche dans le navigateur et s'imprime (feuille de style d'impression).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import accounts, i18n
from app.config import get_settings
from app.web import public_headers

GABARIT = Path(__file__).resolve().parents[1] / "templates" / "checklist" / "fr.html"

#: Hypothèse de l'exemple de coût évité : part des colis avec un problème d'adresse. Ce n'est pas
#: une mesure ; la page dit de la remplacer par le taux réel du transporteur.
TAUX_EXEMPLE = Decimal("0.02")
#: Les deux boutiques de l'exemple : commandes par mois.
COMMANDES_EXEMPLE = (1000, 5000)


def _euros(valeur: Decimal) -> str:
    return f"{i18n.nombre('fr', int(valeur))} €"


def valeurs(settings) -> dict[str, str]:
    bas, haut = i18n.EUROS["colis_rate"], i18n.EUROS["colis_rate_max"]
    champs = {
        **i18n.liens("fr", ""),
        "base": settings.public_base_url.rstrip("/"),
        "contact": settings.contact_email,
        "prix_x402": i18n.nombre("fr", settings.x402_price),
        "essais_jour": i18n.nombre("fr", settings.validate_form_rate_limit_per_day),
        "colis_rate": i18n.prix("fr", "colis_rate_fourchette"),
        "taux_exemple": f"{i18n.nombre('fr', int(TAUX_EXEMPLE * 100))} %",
        "prix_starter": i18n.prix("fr", "starter"),
        "prix_pro": i18n.prix("fr", "pro"),
        "quota_starter": i18n.nombre("fr", accounts.PLANS["starter"]),
        "quota_pro": i18n.nombre("fr", accounts.PLANS["pro"]),
        "jours_essai": i18n.nombre("fr", settings.trial_days),
        "validations_essai": i18n.nombre("fr", accounts.VALIDATIONS_ESSAI),
    }
    for rang, commandes in enumerate(COMMANDES_EXEMPLE, start=1):
        incidents = Decimal(commandes) * TAUX_EXEMPLE
        champs[f"commandes_{rang}"] = i18n.nombre("fr", commandes)
        champs[f"incidents_{rang}"] = i18n.nombre("fr", int(incidents))
        champs[f"cout_{rang}"] = f"{i18n.nombre('fr', int(incidents * bas))} à {_euros(incidents * haut)}"
    return champs


def page(settings) -> str:
    return i18n.rendre(GABARIT, valeurs(settings))


router = APIRouter()


@router.get("/checklist", include_in_schema=False)
async def checklist() -> HTMLResponse:
    """Page publique, indexable, cachable cinq minutes, sans formulaire ni script."""
    return HTMLResponse(page(get_settings()), headers=public_headers())
