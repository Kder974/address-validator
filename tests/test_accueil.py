"""Tests de la page d'accueil `GET /` (décision D53).

Ce que ces tests mesurent, et pourquoi :

  * un **navigateur** reçoit la vitrine, un **programme** garde exactement le JSON
    d'avant : les deux vivent à la même adresse, donc `Vary: Accept` est posé sur
    les deux pour qu'un cache ne serve jamais l'une à la place de l'autre ;
  * la vitrine respecte la CSP des pages publiques : aucun script, aucune
    ressource externe, et `form-action 'self'` pour que son formulaire d'essai
    puisse poster vers `/validate-form` ;
  * ses chiffres sont ceux du **code** (essai, quotas, prix, prix x402, contact) :
    changer la configuration change la page.
"""

import re

from fastapi.testclient import TestClient

from app import accounts
from app.config import get_settings
from app.main import app
from app.routes import accueil
from app.routes.pricing import OFFRES
from app.routes.validate_form import CHAMPS
from app.web import site_head

client = TestClient(app)

NAVIGATEUR = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

JSON_AVANT = {
    "service": "Address Validator API",
    "version": "1.0.0",
    "documentation": "/docs",
    "manifest": "/manifest",
    "health": "/health",
    "mcp": "/mcp",
    "pricing": "/pricing",
}


def test_un_navigateur_recoit_la_vitrine():
    page = client.get("/", headers=NAVIGATEUR)

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["vary"] == "Accept"
    assert page.headers["cache-control"] == "public, max-age=300"
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "form-action 'self'" in csp
    assert page.text.count("<h1") == 1
    assert "{{" not in page.text


def test_un_programme_garde_le_json_d_avant():
    for accept in (None, "*/*", "application/json", "application/json, text/html"):
        reponse = client.get("/", headers={"Accept": accept} if accept else {})
        assert reponse.headers["content-type"] == "application/json"
        assert reponse.headers["vary"] == "Accept"
        assert reponse.json() == JSON_AVANT


def test_la_vitrine_ne_charge_rien_de_l_exterieur():
    texte = client.get("/", headers=NAVIGATEUR).text

    for interdit in ("<script", "<img", "<link", "@import", "url("):
        assert interdit not in texte
    assert not re.search(r"https?://", texte)


def test_le_formulaire_est_celui_de_l_essai_gratuit():
    texte = client.get("/", headers=NAVIGATEUR).text

    assert 'method="post" action="/validate-form"' in texte
    noms = re.findall(r'<input[^>]*\bname="(\w+)"', texte)
    assert noms == [nom for nom, _, _ in CHAMPS]


def test_la_navigation_couvre_les_pages_de_site_head():
    texte = client.get("/", headers=NAVIGATEUR).text

    for lien in re.findall(r'href="(/[^"]*)"', site_head()):
        assert f'href="{lien}"' in texte


def test_les_chiffres_sont_ceux_du_code():
    texte = client.get("/", headers=NAVIGATEUR).text
    prix = {offre["nom"]: offre["prix"] for offre in OFFRES}

    assert f"{accounts.JOURS_ESSAI} jours" in texte
    assert f"{accounts.VALIDATIONS_ESSAI} validations" in texte
    assert prix["Starter"] in texte and prix["Pro"] in texte
    assert "2 000 validations" in texte and "10 000 validations" in texte
    assert get_settings().contact_email in texte


def test_changer_la_configuration_change_la_page():
    reglages = get_settings().model_copy(
        update={"x402_price": 0.05, "contact_email": "contact@example.org"}
    )
    texte = accueil.page(reglages)

    assert "0,05 USDC" in texte and "0,02 USDC" not in texte
    assert "contact@example.org" in texte
