"""Tests des pages de contenu /agents et /compare (décision D66).

Ce qui est mesuré, et pourquoi :

  * les deux pages répondent dans les six langues, publiques, sans champ oublié,
    sans script hors du bloc de référencement, avec la langue de la page ;
  * ce qu'elles montrent du service vient du code et de la configuration (D48) :
    prix x402 et unités USDC, adresse du serveur MCP, prix, quotas, essai ;
  * le comparatif est honnête : chaque prix d'un autre service a sa source (un
    domaine, pas un lien), et la page dit quand un autre service est meilleur ;
  * le lien « Serveur MCP » ne mène plus à `/mcp` (405 dans un navigateur) mais à
    la page des agents.
"""

import re
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import accounts, i18n, seo
from app.config import get_settings
from app.main import app
from app.onchain import usdc_units
from app.routes import contenu

client = TestClient(app)
GABARITS = Path(__file__).resolve().parents[1] / "app" / "templates"


@pytest.mark.parametrize("nom", contenu.PAGES)
@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_chaque_page_se_rend_dans_chaque_langue(lang, nom):
    reponse = client.get(i18n.chemin(lang, nom))
    assert reponse.status_code == 200 and reponse.headers["cache-control"].startswith("public")
    texte = seo.hors_bloc(reponse.text)
    for interdit in ("{{", "<!--@", "<script"):
        assert interdit not in texte
    assert re.search(rf'<html lang="{lang}[^"]*"', texte)
    assert 'content="index, follow"' in texte
    autre = "compare" if nom == "agents" else "agents"
    assert f'href="{i18n.chemin(lang, autre)}"' in texte
    # Seules adresses complètes hors du bloc : celles du service lui-même.
    base = get_settings().public_base_url.rstrip("/")
    assert all(adresse.startswith(base) for adresse in re.findall(r"https?://[^\s\"'<>]+", texte))


def test_les_chiffres_du_service_suivent_la_configuration():
    reglages = get_settings().model_copy(update={"x402_price": 0.05, "public_base_url": "https://exemple.test", "trial_days": 21})
    agents = unescape(contenu.page(reglages, "fr", "agents"))
    assert "0,05 USDC" in agents and f'"amount": "{usdc_units(0.05)}"' in agents
    assert '"url": "https://exemple.test/mcp"' in agents and "kder-ai.com/mcp" not in agents
    compare = unescape(contenu.page(reglages, "en", "compare"))
    assert "Trial: 21 days" in compare
    for cle in ("starter", "pro", "unitaire_starter", "unitaire_pro"):
        assert i18n.prix("en", cle) in compare
    assert i18n.nombre("en", accounts.PLANS["starter"]) in compare and i18n.mention_euros("en") in compare


def test_aucun_chiffre_du_service_n_est_ecrit_dans_les_gabarits():
    for nom in contenu.PAGES:
        for lang in i18n.LANGUES:
            gabarit = (GABARITS / nom / f"{lang}.html").read_text(encoding="utf-8")
            assert "0.02 USDC" not in gabarit and "0,02 USDC" not in gabarit and "20000" not in gabarit
            assert "kder-ai.com/mcp" not in gabarit


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_le_comparatif_cite_ses_sources_et_dit_quand_choisir_un_autre(lang):
    texte = seo.hors_bloc(client.get(i18n.chemin(lang, "compare")).text)
    for source in ("developers.google.com/maps/billing-and-pricing/pricing", "smarty.com/pricing",
                   "postgrid.com/pricing-address-verification", "geoapify.com/pricing"):
        assert f'<span dir="ltr">{source}</span>' in texte
    choix = re.search(r'<ul class="choix">(.*?)</ul>', texte, re.S).group(1)
    for service in ("PostGrid", "Google", "Address Validator", "Geoapify"):
        assert f"<b>{service}</b>" in choix
    assert "2026" in re.search(r"<h1[^>]*>.*?</h1>\s*<p[^>]*>(.*?)</p>", texte, re.S).group(1)


def test_le_lien_serveur_mcp_mene_a_la_page_des_agents():
    for gabarit in GABARITS.rglob("*.html"):
        assert 'href="/mcp"' not in gabarit.read_text(encoding="utf-8"), gabarit
    for lang in i18n.LANGUES:
        for page in ("", "pricing", "guide", "validate-form", "signup"):
            texte = client.get(i18n.chemin(lang, page), headers={"Accept": "text/html"}).text
            assert f'href="{i18n.chemin(lang, "agents")}"' in texte and f'href="{i18n.chemin(lang, "compare")}"' in texte
