"""Tests de la page d'accueil `GET /` (décision D53) et de ses six langues (D57).

Ce que ces tests mesurent, et pourquoi :

  * un **navigateur** reçoit la vitrine, un **programme** garde exactement le JSON
    d'avant : les deux vivent à la même adresse, donc `Vary: Accept` est posé sur
    les deux pour qu'un cache ne serve jamais l'une à la place de l'autre ;
  * la vitrine respecte la CSP des pages publiques : aucun script, aucune
    ressource externe, et `form-action 'self'` pour que son formulaire d'essai
    puisse poster vers `/validate-form` ; les seules images sont celles du site
    (`/images/`, `img-src 'self'`, D57) ;
  * ses chiffres sont ceux du **code** (essai, quotas, prix, prix x402, contact) :
    changer la configuration change la page ;
  * chaque langue (`/en/`, `/zh/`, `/hi/`, `/es/`, `/ar/`) a sa vitrine, dans sa
    langue, son sens d'écriture et sa monnaie, et son sélecteur mène aux autres.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import accounts, i18n
from app import seo
from app.config import get_settings
from app.main import app
from app.routes import accueil
from app.routes.pricing import OFFRES
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
    assert page.headers["vary"] == "Accept, User-Agent"
    assert page.headers["cache-control"] == "public, max-age=300"
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "form-action 'self'" in csp
    assert page.text.count("<h1") == 1
    assert "{{" not in page.text


def test_un_programme_garde_le_json_d_avant():
    for accept in (None, "*/*", "application/json", "application/json, text/html"):
        reponse = client.get("/", headers={"Accept": accept} if accept else {})
        assert reponse.headers["content-type"] == "application/json"
        assert reponse.headers["vary"] == "Accept, User-Agent"
        assert reponse.json() == JSON_AVANT


def test_la_vitrine_ne_charge_rien_de_l_exterieur():
    # D65 : le bloc de référencement a ses propres règles (tests/test_seo.py).
    texte = seo.hors_bloc(client.get("/", headers=NAVIGATEUR).text)

    for interdit in ("<script", "@import", "url("):
        assert interdit not in texte
    assert not re.search(r"https?://", texte)
    # D57 : des photos, mais seulement celles du site ; des <link>, mais seulement
    # les hreflang (aucune feuille, aucune police).
    assert re.findall(r"<img\b", texte)
    assert all(src.startswith("/images/") for src in re.findall(r'<img\b[^>]*\bsrc="([^"]*)"', texte))
    assert all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", texte))


def test_le_formulaire_est_celui_de_l_essai_gratuit():
    texte = client.get("/", headers=NAVIGATEUR).text

    assert 'method="post" action="/validate-form"' in texte
    noms = re.findall(r'<input[^>]*\bname="(\w+)"', texte)
    # D87 : le champ unique de l'essai gratuit (D86).
    assert noms == ["address"]


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


# --- D57 : les six langues -------------------------------------------------------

IMAGES = Path(__file__).resolve().parents[1] / "app" / "static"


def _vitrine(lang):
    return client.get(i18n.chemin(lang), headers=NAVIGATEUR)


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_chaque_langue_a_sa_vitrine(lang):
    page = _vitrine(lang)

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "public, max-age=300"
    balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', page.text)
    assert balise and balise.group(1).split("-")[0] == lang
    assert bool(balise.group(2)) == (lang in i18n.RTL)
    assert page.text.count("<h1") == 1
    hors = seo.hors_bloc(page.text)  # D65 : le bloc de référencement a ses propres règles
    for interdit in ("<script", "{{", "@import", "url("):
        assert interdit not in hors
    assert not re.search(r"https?://", hors)


def test_une_langue_prefixee_est_toujours_du_html():
    for accept in (None, "application/json"):
        page = client.get("/en/", headers={"Accept": accept} if accept else {})
        assert page.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_photos_sont_celles_du_site(lang):
    sources = re.findall(r'<img\b[^>]*\bsrc="([^"]*)"', _vitrine(lang).text)

    assert len(sources) >= 3  # D87 : la vitrine animée en montre 3
    for src in sources:
        assert src.startswith("/images/") and (IMAGES / src.lstrip("/")).is_file()


def test_une_photo_est_servie_en_jpeg():
    photo = client.get("/images/commercante-colis.jpg")

    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/jpeg"
    assert photo.headers["x-content-type-options"] == "nosniff"
    assert photo.content == (IMAGES / "images" / "commercante-colis.jpg").read_bytes()


def test_la_csp_n_ouvre_que_les_images_du_site():
    csp = _vitrine("ar").headers["content-security-policy"]

    assert "img-src 'self'" in csp
    assert "default-src 'none'" in csp and "form-action 'self'" in csp
    assert "script-src" not in csp


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_le_selecteur_et_hreflang_menent_aux_autres_langues(lang):
    texte = _vitrine(lang).text
    selecteur = re.findall(r'<a href="([^"]*)" hreflang="([\w-]+)"', texte)
    alternatives = re.findall(r'<link rel="alternate" hreflang="([\w-]+)" href="([^"]*)">', texte)

    assert selecteur == [(i18n.chemin(autre), autre) for autre in i18n.LANGUES]
    assert dict(alternatives) == {
        **{autre: i18n.chemin(autre) for autre in i18n.LANGUES},
        "x-default": i18n.chemin("en"),
    }
    for href, _ in selecteur:
        assert client.get(href, headers=NAVIGATEUR).status_code == 200


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_la_navigation_reste_dans_la_langue(lang):
    texte = _vitrine(lang).text

    for page in ("pricing", "guide", "validate-form", "signup", "compte"):
        assert f'href="{i18n.chemin(lang, page)}"' in texte
    assert f'method="post" action="{i18n.chemin(lang, "validate-form")}"' in texte
    noms = re.findall(r'<input[^>]*\bname="(\w+)"', texte)
    assert noms == ["address"]


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_chiffres_sont_ceux_du_code_dans_chaque_langue(lang):
    texte = _vitrine(lang).text
    reglages = get_settings()

    for cle in ("essai", "starter", "pro"):
        assert f">{i18n.prix(lang, cle)}<" in texte
    assert i18n.nombre(lang, accounts.PLANS["starter"]) in texte
    assert i18n.nombre(lang, accounts.PLANS["pro"]) in texte
    assert texte.count(i18n.nombre(lang, reglages.x402_price)) >= 4
    assert i18n.mention_euros(lang) in texte
    assert seo.hors_bloc(texte).count(reglages.contact_email) >= 2
