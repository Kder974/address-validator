"""Tests de la page des tarifs dans les six langues (D43, D57).

Ce que ces tests mesurent, et pourquoi :

  * chaque langue a sa page, à une adresse **littérale** (`/pricing`, `/en/pricing`
    … `/ar/pricing`), servie comme `/pricing` : publique, cachable cinq minutes,
    indexable, sans formulaire (`form-action 'none'`), sans script ni ressource
    externe, dans sa langue et son sens d'écriture ;
  * aucun chiffre du service n'est écrit dans un gabarit (D48) : prix convertis par
    `i18n`, prix unitaire et sa division, quotas d'`accounts.PLANS`, essai, cache,
    purge des journaux, prix x402 et contact sont injectés, et changer la
    configuration change la page ;
  * les boutons mènent où il faut : essai, Starter et Pro vers l'inscription de la
    langue (D54 : aucun plan transmis), Enterprise vers le contact, le bloc x402
    vers le guide de la langue ; le sélecteur et `hreflang` vers la même page ;
  * les corrections d'honnêteté de la maquette validée tiennent dans chaque
    langue : aucun témoignage (même drapeau levé), les deux ordres de grandeur
    étiquetés, cinq questions, « cinq fois le volume de Starter » vrai ;
  * une valeur injectée est échappée.
"""

import re
from html import escape

import pytest
from fastapi.testclient import TestClient

from app import accounts, i18n
from app.config import get_settings
from app.main import app
from app.routes import pricing
from app.web import site_head
from app import seo

client = TestClient(app)

TITRES = {
    "fr": "Tarifs",
    "en": "Pricing",
    "zh": "价格",
    "hi": "कीमतें",
    "es": "Precios",
    "ar": "الأسعار",
}


def _tarifs(lang):
    return client.get(i18n.chemin(lang, "pricing"))


def _sans_dessin(html: str) -> str:
    """Sans feuille de style, icônes ni `style="…"` : le texte et les attributs lus."""
    return re.sub(r'<style>.*?</style>|<svg\b.*?</svg>|\sstyle="[^"]*"', "", html, flags=re.S)


def _nombre_seul(valeur: str) -> str:
    """Motif d'un nombre qui n'est pas un morceau d'un autre (14, pas 0,0145)."""
    return rf"(?<![\d.,]){re.escape(valeur)}(?![\d.,])"


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_chaque_langue_a_sa_page_des_tarifs(lang):
    reponse = _tarifs(lang)

    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("text/html")
    assert reponse.headers["cache-control"] == "public, max-age=300"
    assert reponse.headers["x-content-type-options"] == "nosniff"
    csp = reponse.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "form-action 'none'" in csp and "script-src" not in csp
    texte = reponse.text
    balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', texte)
    assert balise and balise.group(1).split("-")[0] == lang
    assert bool(balise.group(2)) == (lang in i18n.RTL)
    assert f"<title>{TITRES[lang]} · Address Validator</title>" in texte
    assert '<meta name="robots" content="index, follow">' in texte
    assert texte.count("<h1") == 1
    hors = seo.hors_bloc(texte)  # D65 : le bloc de référencement a ses propres règles
    for interdit in ("<script", "{{", "@import", "url(", "<img", "<iframe", "<form"):
        assert interdit not in hors
    assert not re.search(r"https?://", hors)
    assert all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", hors))


def test_les_adresses_sont_litterales():
    """D57 : `/en/pricing`… sont écrits en toutes lettres, jamais `/{lang}/pricing`."""
    chemins = [getattr(route, "path", "") for route in app.routes]

    for lang in i18n.LANGUES:
        assert chemins.count(i18n.chemin(lang, "pricing")) == 1
    assert not [chemin for chemin in chemins if "{" in chemin and "pricing" in chemin]
    assert client.get("/de/pricing").status_code == 404


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_le_selecteur_et_hreflang_menent_a_la_meme_page(lang):
    texte = _tarifs(lang).text
    selecteur = re.findall(r'<a href="([^"]*)" hreflang="([\w-]+)"', texte)
    alternatives = re.findall(r'<link rel="alternate" hreflang="([\w-]+)" href="([^"]*)">', texte)

    assert selecteur == [(i18n.chemin(autre, "pricing"), autre) for autre in i18n.LANGUES]
    assert dict(alternatives) == {
        **{autre: i18n.chemin(autre, "pricing") for autre in i18n.LANGUES},
        "x-default": i18n.chemin("en", "pricing"),
    }


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_la_navigation_reste_dans_la_langue(lang):
    texte = _tarifs(lang).text

    for page in ("", "guide", "validate-form", "signup", "compte"):
        assert f'href="{i18n.chemin(lang, page)}"' in texte, page


def test_la_navigation_couvre_celle_de_site_head():
    """D48 : une seule liste de pages ; la barre du dessin validé n'en oublie aucune."""
    texte = _tarifs("fr").text

    for lien in re.findall(r'href="(/[^"]*)"', site_head()):
        assert f'href="{lien}"' in texte, lien


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_boutons_menent_ou_il_faut(lang):
    texte = _tarifs(lang).text
    grille = re.search(r'<ol class="tiers">(.*?)</ol>', texte, re.S).group(1)
    appel = re.search(r'id="a-l-appel".*?<a class="link" href="([^"]*)"', texte, re.S)

    assert re.findall(r'<a class="btn [^"]*" href="([^"]*)"', grille) == [
        *[i18n.chemin(lang, "signup")] * 3,
        f"mailto:{get_settings().contact_email}",
    ]
    assert appel.group(1) == i18n.chemin(lang, "guide")
    assert "plan=" not in texte  # D54 : l'inscription ne reçoit aucun plan


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_chiffres_sont_ceux_du_code(lang):
    texte = _tarifs(lang).text
    lu = _sans_dessin(texte)
    reglages = get_settings()
    montants = i18n.MONTANTS[i18n.DEVISE[lang]]

    for cle in ("essai", "starter", "pro", "unitaire_starter", "unitaire_pro"):
        assert f">{i18n.prix(lang, cle)}<" in texte, cle
    for plan in ("trial", "starter", "pro"):
        assert f">{i18n.nombre(lang, accounts.PLANS[plan])}<" in texte, plan
    for plan in ("starter", "pro"):
        # La division affichée est celle qui donne le prix unitaire.
        assert f"{i18n.prix(lang, plan)} ÷ {i18n.nombre(lang, accounts.PLANS[plan])}" in texte, plan
    baisse = int(100 - 100 * montants["unitaire_pro"] / montants["unitaire_starter"])
    assert re.search(rf"{_nombre_seul(str(baisse))} ?%", lu)
    assert i18n.prix(lang, "colis_rate_fourchette") in texte
    # « Facturé en euros », juste sous la grille, partout où le prix est converti.
    sous_la_grille = re.search(
        r'</ol>\s*(?:<p class="[^"]*">(.*?)</p>\s*)?<div class="tiers-foot">', texte, re.S
    )
    assert sous_la_grille and (sous_la_grille.group(1) or "") == escape(i18n.mention_euros(lang))
    assert len(re.findall(_nombre_seul(i18n.nombre(lang, reglages.x402_price)), lu)) == 4
    assert texte.count(f'href="mailto:{reglages.contact_email}"') == 3


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_aucun_chiffre_du_service_n_est_ecrit_dans_le_gabarit(lang):
    """D48 : chaque chiffre injecté par `pricing.valeurs` est absent du gabarit brut."""
    gabarit = _sans_dessin((pricing.GABARITS / f"{lang}.html").read_text(encoding="utf-8"))
    # Les exemples de requête (`"100 stewart st"`) ne sont pas des chiffres du service.
    gabarit = re.sub(r'"100 [Ss]tewart (st|Street)"', "", gabarit)

    for cle, valeur in pricing.valeurs(get_settings(), lang).items():
        if cle.startswith(("url_", "lien_", "jauge_")) or cle == "mention_euros":
            continue
        assert not re.search(_nombre_seul(str(valeur)), gabarit), (cle, valeur)


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_changer_la_configuration_change_la_page(lang):
    reglages = get_settings().model_copy(
        update={
            "trial_days": 21,
            "x402_price": 0.05,
            "contact_email": "contact@example.org",
            "discovery_log_retention_days": 7,
        }
    )
    lu = _sans_dessin(seo.hors_bloc(pricing.page(reglages, lang)))

    assert re.search(_nombre_seul("21"), lu) and not re.search(_nombre_seul("14"), lu)
    assert re.search(_nombre_seul("7"), lu)
    assert i18n.nombre(lang, 0.05) in lu and i18n.nombre(lang, 0.02) not in lu
    # Trois liens `mailto:` (devis, FAQ, pied de page), dont deux affichent l'adresse.
    assert lu.count("contact@example.org") == 5 and get_settings().contact_email not in lu


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_une_valeur_injectee_est_echappee(lang):
    piege = '<b>"x"</b>@example.org'
    texte = pricing.page(get_settings().model_copy(update={"contact_email": piege}), lang)

    assert piege not in texte
    assert escape(piege, quote=True) in texte


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_corrections_d_honnetete_tiennent(lang):
    texte = pricing.page(get_settings().model_copy(update={"pricing_show_testimonials": True}), lang)

    assert 'id="temoignages"' not in texte and "<blockquote" not in texte
    assert texte.count('<li class="tier') == 4
    assert texte.count('<details class="qa"') == 5
    # Les deux ordres de grandeur du problème portent leur étiquette, et le taux
    # affiché est celui de `PROBLEME`.
    assert texte.count('<span class="st st-warn">') == 2
    assert re.search(_nombre_seul(pricing.PROBLEME[0][0].split()[0]), _sans_dessin(texte))


def test_cinq_fois_le_volume_de_starter():
    """Les gabarits écrivent « cinq fois le volume de Starter » en toutes lettres."""
    assert accounts.PLANS["pro"] == 5 * accounts.PLANS["starter"], "réécrire la puce Pro des six gabarits"


def test_les_promesses_retirees_ne_reviennent_pas():
    """Maquette validée : rien que le service ne fasse, et la couverture dite telle quelle."""
    fr, en = _tarifs("fr").text, _tarifs("en").text

    assert "Base Adresse Nationale" in fr and "reste partielle" in fr
    assert "Base Adresse Nationale" in en and "stays partial" in en
    for retire in (
        "Le plus choisi",
        "Validation en masse",
        "Webhooks",
        "Réponse prioritaire",
        "facturation annuelle",
        "niveau de service",
        "Interlocuteur unique",
        "référentiel d'adresses international",
    ):
        assert retire not in fr, retire
    for retire in ("popular", "Webhooks", "Priority", "SLA", "annual billing"):
        assert retire not in en, retire
