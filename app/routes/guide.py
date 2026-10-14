"""`/guide` — guide d'intégration public (décision D48), en six langues (D57).

La page qui répond à la question que se pose un client qui vient de recevoir sa
clé : « je fais quoi, maintenant ? ». Elle est **publique** (elle doit être
lisible et indexable par un inconnu), **autonome** (aucun script, aucune
ressource externe : le sommaire collant est une liste d'ancres, le reste du CSS)
et elle ne contient **aucun secret** : la clé des exemples reste un gabarit
(`sk_live_VOTRE_CLE`, `sk_live_YOUR_KEY`…).

Le dessin vient des maquettes validées (`docs/maquettes/site-multilingue/`) et
vit dans `app/templates/guide/<langue>.html` : le français reste sur `/guide`,
les autres langues vivent sous `/en/guide`, `/zh/guide`… Les exemples de code
restent du code (identifiants intacts) : seuls leurs commentaires sont traduits.

Trois règles de rédaction, parce qu'un guide est un contrat :

1. **Ce qui est montré est ce qui est mesuré.** Le gabarit ne contient aucun
   chiffre du code, ils lui sont injectés : base publique (`public_base_url`,
   jamais recopiée en dur), prix x402 et plafond de l'essai sans compte
   (configuration), essai (`accounts`), durées du cache (`app/cache.py`, D47) et
   version x402. Les corps d'exemple sont ceux de `app/schemas.py`, les en-têtes
   ceux que la route pose vraiment (`X-Payment-Mode`, `X-Plan`,
   `X-Quota-Remaining`), et les codes ceux de `app/routes/validate.py`. Un guide
   qui promet un en-tête que le serveur ne renvoie pas fait perdre une journée à
   un intégrateur.
2. **Les limites sont dites avec le reste** : quota **mensuel** en UTC, cache
   long pour un verdict et **court** pour un résultat dégradé (D47), plafond de
   l'essai gratuit, `402` qui n'est pas forcément un défaut de paiement (quota
   du mois), `503` qui n'est jamais une demande de paiement.
3. **Aucune astuce qui contourne la porte** : l'exemple de masse illustre le
   cache (une même adresse n'interroge le fournisseur qu'une fois) et le respect
   de `Retry-After`. Rien qui aide à fabriquer une preuve de paiement.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from app import accounts, cache, i18n, x402
from app.config import get_settings
from app.web import public_headers

GABARITS = Path(__file__).resolve().parents[1] / "templates" / "guide"

#: Les huit codes de retour que le guide décrit : situation et action telles que
#: la page française les dit (les autres langues les traduisent). Les trois refus
#: du mode clé (401/403/402) sont volontairement distincts côté serveur (D45) :
#: les confondre ici annulerait le bénéfice.
CODES: tuple[tuple[str, str, str], ...] = (
    ("200", "Validation servie", "Lisez `result`. Gardez `cache_key` : c'est votre pseudonyme de cette adresse, il sert à demander un effacement."),
    ("401", "Clé API inconnue", "La clé est incomplète, révoquée (régénérée) ou destinée à un autre service. Corrigez la variable d'environnement. **Ne réessayez pas en boucle.**"),
    ("402", "Quota du mois consommé, ou aucun paiement valide", "Lisez le corps de la réponse : il dit lequel des deux cas s'applique. Un 402 de quota n'est pas une panne."),
    ("403", "Compte expiré", "L'essai gratuit est terminé et le compte est resté sur le plan d'essai. Passez à un plan payant : rien n'est prélevé automatiquement."),
    ("422", "Requête mal formée", "Un champ est absent ou vide. `street`, `city`, `state` et `zip` sont obligatoires ; `country` vaut `US` par défaut."),
    ("429", "Plafond d'essais gratuits atteint", "Concerne `/validate-form`, l'essai sans compte, pas l'API payante. Attendez le délai annoncé dans `Retry-After`, ou créez un compte."),
    ("500", "Paiement encaissé, mais registre injoignable", "Le service **refuse** de servir une validation qu'il ne peut pas inscrire. Réessayez. Si l'appel était payé, citez `transaction_id` : le rapprochement se fait à la main."),
    ("503", "Comptes clients ou base injoignable", "La clé n'a pas pu être vérifiée (ce n'est **pas** un refus), ou la base ne répond pas. Réessayez dans quelques minutes : rien n'a été consommé."),
)


def valeurs(settings, lang: str = i18n.DEFAUT) -> dict[str, str]:
    """Les champs du gabarit, tous lus dans le code ou la configuration."""
    return {
        **i18n.liens(lang, "guide"),
        "base": settings.public_base_url.rstrip("/"),
        "prix_x402": i18n.nombre(lang, settings.x402_price),
        "essai_sans_compte": i18n.nombre(lang, settings.validate_form_rate_limit_per_day),
        "jours_essai": i18n.nombre(lang, accounts.JOURS_ESSAI),
        "validations_essai": i18n.nombre(lang, accounts.VALIDATIONS_ESSAI),
        # ponytail: durées entières (30 jours, 5 minutes) ; un TTL fractionnaire serait arrondi par défaut.
        "cache_jours": i18n.nombre(lang, cache.CACHE_TTL // 86400),
        "cache_minutes": i18n.nombre(lang, cache.CACHE_TTL_DEGRADED // 60),
        "x402_version": x402.X402_VERSION,
        "contact": settings.contact_email,
    }


def page(settings, lang: str = i18n.DEFAUT) -> str:
    """Le guide complet dans une langue, champs remplis et échappés."""
    return i18n.rendre(GABARITS / f"{lang}.html", valeurs(settings, lang))


def _reponse(lang: str) -> HTMLResponse:
    return HTMLResponse(
        page(get_settings(), lang),
        status_code=200,
        headers=public_headers(form=False, max_age=300),
    )


router = APIRouter()


@router.get("/guide")
async def guide() -> Response:
    """Le guide public : indexable, cachable, sans secret et sans ressource externe."""
    return _reponse(i18n.DEFAUT)


def _guide(lang: str):
    async def guide_langue() -> Response:
        return _reponse(lang)

    return guide_langue


# Chemins **littéraux** (`/en/guide`…), comme la vitrine (D57) : jamais `/{lang}/guide`.
for _lang in i18n.PREFIXEES:
    router.add_api_route(i18n.chemin(_lang, "guide"), _guide(_lang), methods=["GET"], include_in_schema=False)
