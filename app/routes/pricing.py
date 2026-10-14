"""GET /pricing et /<langue>/pricing — la page publique des tarifs (D43, D57).

Première page **publique** du service (D43) : elle vend, donc elle se lit sans
compte, s'indexe et se met en cache quelques minutes (`public_headers()`, avec
`form-action 'none'` : elle n'a aucun formulaire). Elle reste autonome : aucun
script, aucune ressource externe, la FAQ en `<details>`, sans une ligne de
JavaScript. Elle ne contient ni donnée client ni identifiant : un cache partagé
peut la garder sans fuite.

Le dessin vient des maquettes validées (`docs/maquettes/site-multilingue/
tarifs-<langue>.html`) et vit dans `app/templates/pricing/<langue>.html`, rendu
par `i18n.rendre` (valeurs échappées, champ oublié = erreur), comme la vitrine
(`app/routes/accueil.py`). La page a la barre de navigation du dessin validé ;
elle couvre tous les liens de `site_head()`, pour que D48 (une seule liste de
pages) ne dérive pas : `tests/test_pricing_i18n.py` et la porte D48 le vérifient.

**Honnête** (corrections de la maquette validée) : Pro et Enterprise ne
promettent que ce que le service fait (plus d'import CSV en masse, de webhooks,
de réponse prioritaire, de niveau de service ni de facturation annuelle),
aucune étiquette « le plus choisi » (aucun client à ce jour), la FAQ dit que la
vérification est optimisée pour les États-Unis et partielle ailleurs, La Réunion
comprise, et les deux ordres de grandeur du problème sont présentés comme non
sourcés. La page n'a **plus** de témoignages : `PRICING_SHOW_TESTIMONIALS` reste
lu par la configuration, sans effet.

**Ce qui est montré est ce qui est appliqué** (D48) : aucun chiffre du service
n'est écrit dans un gabarit. Prix (convertis par `i18n.prix` hors fr/es, avec la
mention « facturé en euros »), prix unitaire et sa division, quotas
(`accounts.PLANS`), durée de l'essai (`trial_days`, celle que l'inscription
applique), durée du cache (`cache.CACHE_TTL`), purge des journaux, prix x402 et
contact viennent du code et de la configuration.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import accounts, i18n, seo
from app.cache import CACHE_TTL
from app.config import get_settings
from app.web import public_headers

#: Les offres, en euros : la **vérité de facturation** (D57), définie à côté des
#: quotas dans `app/accounts.py` (D60). Gardée ici sous ce nom, que lisent les
#: tests et les portes depuis D43 : c'est le même objet.
OFFRES = accounts.OFFRES

#: Ordres de grandeur du problème, **non sourcés** et non mesurés par le service :
#: la page le dit. Le coût d'un colis raté est publié en dollars depuis D43 ; c'est
#: la source de la fourchette que chaque langue affiche convertie
#: (`i18n.prix(lang, "colis_rate_fourchette")`) et d'`accounts.COUT_ECHEC_USD`.
PROBLEME = (
    ("22 %", "des échecs de livraison seraient causés par une adresse incorrecte"),
    ("17 à 30 $", "par colis en échec : retour, réexpédition, appel au client"),
)

GABARITS = Path(__file__).resolve().parents[1] / "templates" / "pricing"


def valeurs(settings, lang: str) -> dict:
    """Les champs du gabarit, tous lus dans le code ou la configuration."""
    plans = accounts.PLANS
    montants = i18n.MONTANTS[i18n.DEVISE[lang]]
    return {
        **i18n.liens(lang, "pricing"),
        **{
            f"prix_{cle}": i18n.prix(lang, cle)
            for cle in ("essai", "starter", "pro", "unitaire_starter", "unitaire_pro")
        },
        "colis_rate": i18n.prix(lang, "colis_rate_fourchette"),
        "mention_euros": i18n.mention_euros(lang),
        "jours_essai": i18n.nombre(lang, settings.trial_days),
        "validations_essai": i18n.nombre(lang, plans["trial"]),
        "quota_starter": i18n.nombre(lang, plans["starter"]),
        "quota_pro": i18n.nombre(lang, plans["pro"]),
        # Jauges de volume : la part du quota Pro (le plus grand quota chiffré).
        "jauge_essai": plans["trial"] * 100 // plans["pro"],
        "jauge_starter": plans["starter"] * 100 // plans["pro"],
        # « 45 % de moins par validation qu'en Starter » : arrondi par défaut, jamais flatteur.
        "baisse_pro": int(100 - 100 * montants["unitaire_pro"] / montants["unitaire_starter"]),
        "jours_cache": i18n.nombre(lang, CACHE_TTL // 86400),
        "jours_journaux": i18n.nombre(lang, settings.discovery_log_retention_days),
        "prix_x402": i18n.nombre(lang, settings.x402_price),
        "contact": settings.contact_email,
    }


def page(settings, lang: str) -> str:
    """La page des tarifs dans une langue, champs remplis et échappés."""
    return seo.enrichir(i18n.rendre(GABARITS / f"{lang}.html", valeurs(settings, lang)), lang, "pricing", settings)


def _tarifs(lang: str):
    async def pricing() -> HTMLResponse:
        """Page publique : servie sans compte, cachable quelques minutes (D43)."""
        return HTMLResponse(content=page(get_settings(), lang), headers=public_headers())

    return pricing


router = APIRouter()
# Chemins **littéraux** (`/en/pricing`…), jamais `/{lang}/pricing` (D57) ; seule
# l'adresse française, publiée depuis D43, figure dans le schéma OpenAPI.
for lang in i18n.LANGUES:
    router.add_api_route(
        i18n.chemin(lang, "pricing"),
        _tarifs(lang),
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=lang == i18n.DEFAUT,
    )
