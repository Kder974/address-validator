"""`GET /kit-anti-colis-perdu` — page de vente du « Kit Anti-Colis Perdu », 29 € (D99).

Le kit est un PDF de 19 pages (checklist, guide en quatre chapitres, cinq modèles d'e-mails,
explication du calculateur) et un calculateur Excel ; il est vendu et livré par Gumroad. Les
fichiers du kit ne sont pas dans ce dépôt public (`.gitignore`) : ils sont le produit vendu.

Le lien d'achat vient de la configuration (`GUMROAD_KIT_URL`, dans le `.env`). Tant qu'il est vide,
le bouton dit « Bientôt disponible », ne mène nulle part, et la page demande à ne pas être indexée :
on ne publie pas un bouton d'achat cassé. Aucun avis client ni garantie n'est affiché : il n'y en a
pas encore. Chaque chiffre vient du code (D48).
"""

from __future__ import annotations

import html
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import i18n
from app.config import get_settings
from app.web import public_headers

GABARIT = Path(__file__).resolve().parents[1] / "templates" / "kit" / "fr.html"

#: Prix du kit, en euros, tel que vendu sur Gumroad.
PRIX_KIT_EUR = Decimal(29)
#: Ce que contient le kit (le PDF est construit par `docs/commercial/produits/kit_anti_colis_final.html`).
PAGES_PDF = 19
POINTS_CHECKLIST = 27
CHAPITRES = 4
MODELES_EMAILS = 5

FLECHE = ('<svg class="arr" viewBox="0 0 10 10" aria-hidden="true"><path class="l" d="M1 5h7"/>'
          '<path class="h" d="M3.5 1.5 7 5 3.5 8.5"/></svg>')


def bouton_achat(lien: str, prix: str) -> str:
    """Le bouton d'achat : un vrai lien Gumroad, ou « Bientôt disponible » tant qu'il n'y en a pas."""
    if lien:
        return (f'<a class="btn btn-accent" href="{html.escape(lien, quote=True)}" rel="noopener">'
                f'Acheter maintenant, {html.escape(prix)} {FLECHE}</a>')
    return '<span class="btn btn-accent btn-off" aria-disabled="true">Bientôt disponible</span>'


def valeurs(settings) -> dict[str, str]:
    prix = f"{i18n.nombre('fr', PRIX_KIT_EUR)} €"
    return {
        **i18n.liens("fr", ""),
        "base": settings.public_base_url.rstrip("/"),
        "contact": settings.contact_email,
        "prix_x402": i18n.nombre("fr", settings.x402_price),
        "robots": "index, follow" if settings.gumroad_kit_url else "noindex, follow",
        "prix_kit": prix,
        "cout_max": i18n.prix("fr", "colis_rate_fourchette").split("–")[-1].strip(),
        "colis_rate": i18n.prix("fr", "colis_rate_fourchette"),
        "pages_pdf": i18n.nombre("fr", PAGES_PDF),
        "points": i18n.nombre("fr", POINTS_CHECKLIST),
        "chapitres": i18n.nombre("fr", CHAPITRES),
        "modeles": i18n.nombre("fr", MODELES_EMAILS),
    }


def page(settings) -> str:
    champs = valeurs(settings)
    texte = i18n.rendre(GABARIT, champs)
    return texte.replace("<!--@achat-->", bouton_achat(settings.gumroad_kit_url, champs["prix_kit"]))


router = APIRouter()


@router.get("/kit-anti-colis-perdu", include_in_schema=False)
async def kit() -> HTMLResponse:
    """Page publique, cachable cinq minutes, sans formulaire ni script."""
    return HTMLResponse(page(get_settings()), headers=public_headers())
