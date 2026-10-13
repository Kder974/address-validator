"""Tests de la page publique `/pricing` (décision D43).

Ce que ces tests mesurent, et pourquoi :

  * la page est **publique** (servie sans compte, `200`) mais reste **autonome**
    — aucune ressource externe, aucun script : la même propriété que D32 exige
    du tableau de bord, vérifiée ici sur la page qui reçoit les inconnus ;
  * les chiffres affichés sont **calculés** (prix unitaire dérivé du plan) ou
    **réels** (le prix facturé à la requête, `settings.x402_price`) ;
  * les témoignages d'**exemple** ne sont pas publiés par défaut ;
  * rien de secret ne fuit (mot de passe du dashboard, secret API, adresse de
    portefeuille) : une page indexable ne doit contenir que ce qu'on veut voir
    indexé.
"""

from html import unescape

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.routes.pricing import OFFRES, QUESTIONS, _page, _prix_unitaire
from app.web import DASHBOARD_CSS

client = TestClient(app)


def _texte() -> str:
    """Le texte **déséchappé** de la page : les apostrophes y sont littérales.

    `html.escape` transforme `'` en `&#x27;` dans le HTML rendu : on mesure le
    texte que lit un humain, pas son encodage.
    """
    reponse = client.get("/pricing")
    assert reponse.status_code == 200
    return unescape(reponse.text)


def test_pricing_est_publique_et_annoncee():
    """La page se lit sans compte, s'indexe, et se laisse mettre en cache."""
    reponse = client.get("/pricing")
    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("text/html")
    assert reponse.headers["cache-control"] == "public, max-age=300"
    assert reponse.headers["x-content-type-options"] == "nosniff"
    assert 'content-security-policy' in reponse.headers
    assert "form-action 'none'" in reponse.headers["content-security-policy"]
    assert '<meta name="robots" content="index, follow">' in reponse.text
    assert client.get("/").json()["pricing"] == "/pricing"


def test_page_autonome_sans_ressource_externe():
    """Comme /dashboard (D32) : rien à charger, rien à exécuter."""
    html = client.get("/pricing").text
    assert "<script" not in html.lower()
    for motif in ("http://", "https://", "//cdn", "@import", "url(http", "<img", "<iframe"):
        assert motif not in html, f"ressource externe ou active : {motif}"
    # La feuille servie est bien celle du dashboard, importée et non recopiée.
    assert DASHBOARD_CSS in html


def test_offre_et_prix_unitaires_calcules():
    """Les quatre offres demandées, et un prix unitaire dérivé du plan."""
    html = _texte()
    for attendu in (
        "Essai gratuit",
        "Starter",
        "Pro",
        "Enterprise",
        "0 €",
        "29 €",
        "79 €",
        "Sur devis",
        "14 jours, 100 validations",
        "par mois, 2 000 validations",
        "par mois, 10 000 validations",
        "volume illimité",
    ):
        assert attendu in html, f"offre incomplète : {attendu} manquant"
    assert len(OFFRES) == 4
    assert _prix_unitaire({"prix_eur": 29.0, "validations": 2000}) == "0,0145 € la validation"
    assert _prix_unitaire({"prix_eur": 79.0, "validations": 10000}) == "0,0079 € la validation"
    assert "0,0145 € la validation" in html and "0,0079 € la validation" in html
    # Un plan sans quota ne fabrique pas un prix unitaire.
    assert _prix_unitaire({"prix_eur": None, "validations": None}) == ""


def test_les_trois_etapes_les_cinq_questions_et_les_faits():
    """Trois étapes, cinq questions, les ordres de grandeur et un appel à l'action."""
    html = _texte()
    for etape in (
        "Envoyez l'adresse",
        "Recevez la validation",
        "Expédiez l'adresse corrigée",
    ):
        assert etape in html, f"étape manquante : {etape}"
    assert html.count('<details class="faq">') == 5 == len(QUESTIONS)
    assert "22 %" in html and "17 à 30 $" in html
    assert "1. " not in html  # l'ordre des étapes est porté par la mise en page
    assert "Commencer l'essai gratuit" in html
    assert 'href="/signup"' in html
    assert 'href="/validate-form"' in html
    assert "RGPD" in html
    minuscules = html.lower()
    for usage in ("e-commerce", "logistique", "marketplace"):
        assert usage in minuscules, f"cas d'usage manquant : {usage}"


def test_temoignages_masques_par_defaut():
    """D43 : publier des exemples comme de vrais témoignages serait un mensonge."""
    assert 'id="temoignages"' not in client.get("/pricing").text
    page = unescape(_page(Settings(pricing_show_testimonials=True)))
    assert 'id="temoignages"' in page
    assert "exemple — à remplacer" in page


def test_la_page_ne_publie_aucun_secret():
    """Une page indexable ne contient que ce qui doit être indexé."""
    html = client.get("/pricing").text
    settings = get_settings()
    for nom, secret in (
        ("mot de passe du dashboard", settings.dashboard_password),
        ("secret API", settings.api_secret_key),
        ("secret CDP", settings.cdp_api_key_secret),
        ("adresse de portefeuille", settings.cdp_wallet_address),
    ):
        assert not secret or secret not in html, f"fuite : {nom}"
    # Le seul prix annoncé est celui réellement facturé à la requête.
    assert f"{settings.x402_price:g} USDC" in html
