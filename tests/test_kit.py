"""`/kit-anti-colis-perdu` : la page de vente du Kit Anti-Colis Perdu (D99).

Ce qui est mesuré, et pourquoi :

  * la page est publique, cachable, sans script ni formulaire ;
  * sans lien Gumroad configuré, le bouton dit « Bientôt disponible », ne mène nulle part, et la
    page n'est pas indexée : pas de bouton d'achat cassé en ligne ;
  * avec le lien, les deux boutons y mènent, lien échappé, et la page redevient indexable ;
  * les chiffres viennent du code (D48), et la page n'affiche ni avis ni garantie inventés ;
  * les fichiers du kit, qui sont le produit vendu, ne peuvent pas entrer dans le dépôt public.
"""

from decimal import Decimal

from fastapi.testclient import TestClient

from app import i18n
from app.config import get_settings
from app.main import app
from app.routes import kit

client = TestClient(app)
LIEN = "https://exemple.gumroad.com/l/kit?a=1&b=2"


def test_la_page_est_publique_et_sans_script():
    page = client.get("/kit-anti-colis-perdu")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "public, max-age=300"
    assert "script-src" not in page.headers["content-security-policy"]
    for interdit in ("<script", "<img", "src=", "@import", "url(", "<form", "{{", "<!--@"):
        assert interdit not in page.text, interdit


def test_sans_lien_gumroad_le_bouton_attend():
    texte = kit.page(get_settings().model_copy(update={"gumroad_kit_url": ""}))
    assert texte.count("Bientôt disponible") == 2 and "Acheter maintenant" not in texte
    assert 'name="robots" content="noindex, follow"' in texte and "gumroad.com" not in texte


def test_avec_le_lien_les_deux_boutons_y_menent():
    texte = kit.page(get_settings().model_copy(update={"gumroad_kit_url": LIEN}))
    assert texte.count('href="https://exemple.gumroad.com/l/kit?a=1&amp;b=2" rel="noopener">Acheter maintenant, 29 €') == 2
    assert 'name="robots" content="index, follow"' in texte and "Bientôt disponible" not in texte


def test_les_chiffres_sont_ceux_du_code(monkeypatch):
    monkeypatch.setattr(kit, "PRIX_KIT_EUR", Decimal(39))
    texte = kit.page(get_settings())
    assert "39 €" in texte and "29 €" not in texte
    assert i18n.prix("fr", "colis_rate_fourchette") in texte
    assert "jusqu'à 26 €" in texte  # le haut de la fourchette de Tarifs, pas « 30 € »


def test_ni_avis_ni_garantie_inventes_et_la_checklist_liee():
    texte = client.get("/kit-anti-colis-perdu").text.lower()
    for interdit in ("★", "témoign", "avis client", "satisfait ou rembours", "garanti"):
        assert interdit not in texte, interdit
    assert 'href="/checklist"' in texte


def test_les_fichiers_du_kit_restent_hors_du_depot():
    regles = open(".gitignore", encoding="utf-8").read()
    assert "docs/commercial/produits/kit_anti_colis_final.*" in regles
    assert "docs/commercial/produits/calculateur_pertes.xlsx" in regles
