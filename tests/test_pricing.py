"""Tests de la page publique `/pricing` (décision D43, dessin validé de D57).

Ce que ces tests mesurent, et pourquoi :

  * la page est **publique** (servie sans compte, `200`) mais reste **autonome**
    — aucune ressource externe, aucun script : la même propriété que D32 exige
    du tableau de bord, vérifiée ici sur la page qui reçoit les inconnus ; sa
    feuille est celle du dessin validé, écrite dans la page ;
  * les chiffres affichés sont **calculés** (prix unitaire dérivé du plan, et sa
    division affichée) ou **réels** (le prix facturé à la requête,
    `settings.x402_price`) ;
  * aucun témoignage n'est publié, même si l'ancien drapeau est levé ;
  * rien de secret ne fuit (mot de passe du dashboard, secret API, adresse de
    portefeuille) : une page indexable ne doit contenir que ce qu'on veut voir
    indexé.

Les six langues sont mesurées par `tests/test_pricing_i18n.py`.
"""

import re
from html import unescape

from fastapi.testclient import TestClient

from app import i18n
from app.config import Settings, get_settings
from app.main import app
from app.routes.pricing import OFFRES, page

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
    # La feuille est celle du dessin validé, écrite dans la page ; les seuls <link>
    # sont les hreflang (aucune feuille, aucune police).
    assert html.count("<style>") == 1
    assert all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", html))


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
        "<b>2 000</b> par mois",
        "<b>10 000</b> par mois",
        "<b>Illimitées</b> sans plafond mensuel",
    ):
        assert attendu in html, f"offre incomplète : {attendu} manquant"
    assert len(OFFRES) == 4
    assert all(offre["periode"] in html for offre in OFFRES)
    # Prix ÷ quota, et la division est affichée à côté du résultat.
    assert "<b>0,0145 €</b>" in html and "29 € ÷ 2 000 validations" in html
    assert "<b>0,0079 €</b>" in html and "79 € ÷ 10 000 validations" in html
    # Un plan sans quota ne fabrique pas un prix unitaire.
    assert "<b>Sur devis</b>" in html


def test_les_trois_etapes_les_cinq_questions_et_les_faits():
    """Trois étapes, cinq questions, les ordres de grandeur et un appel à l'action."""
    html = _texte()
    for etape in (
        "Envoyer l'adresse",
        "Recevoir la validation",
        "Expédier l'adresse corrigée",
    ):
        assert etape in html, f"étape manquante : {etape}"
    assert html.count('<details class="qa"') == 5
    assert "22 %" in html and "environ 15–26 €" in re.sub(r"<[^>]+>", "", html)
    assert html.count("Ordre de grandeur du secteur") == 2
    assert "1. " not in html  # l'ordre des étapes est porté par la mise en page
    assert "Commencer l'essai gratuit" in html
    assert 'href="/signup"' in html
    assert 'href="/validate-form"' in html
    assert "RGPD" in html
    minuscules = html.lower()
    for usage in ("e-commerce", "logistique", "places de marché"):
        assert usage in minuscules, f"cas d'usage manquant : {usage}"


def test_aucun_temoignage_meme_drapeau_leve():
    """Maquette validée : aucun témoignage ; `PRICING_SHOW_TESTIMONIALS` n'a plus d'effet."""
    assert 'id="temoignages"' not in client.get("/pricing").text
    avec_drapeau = unescape(page(Settings(pricing_show_testimonials=True), "fr"))
    assert 'id="temoignages"' not in avec_drapeau
    assert "exemple — à remplacer" not in avec_drapeau


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
    assert f"{i18n.nombre('fr', settings.x402_price)} USDC" in html
