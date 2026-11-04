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


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_la_page_agents_publie_le_snippet_mcp_copiable(lang):
    """D96 : le snippet MCP est sur la page, dans chaque langue, sans placeholder."""
    texte = unescape(contenu.page(get_settings(), lang, "agents"))
    assert 'id="mcp-payer"' in texte
    assert 'params._meta["x402/payment"]' in texte
    assert "Account.sign_typed_data" in texte and 'method": "tools/call' in texte
    assert "{{" not in texte, "un placeholder du snippet n'a pas ete rendu"


def test_le_snippet_mcp_est_aussi_dans_llms_et_agents_md():
    racine = Path(__file__).resolve().parents[1]
    for nom in ("llms.txt", "AGENTS.md"):
        texte = (racine / nom).read_text(encoding="utf-8")
        assert 'params._meta["x402/payment"]' in texte, nom
        assert "Account.sign_typed_data" in texte, nom
        assert "tools/call" in texte, nom


# D83 : les prix des autres services, publiés en dollars, sont montrés dans la
# monnaie de la page, avec le prix publié entre parenthèses.
@pytest.mark.parametrize("lang, attendu", [("fr", "≈\u00a015\u00a0€"), ("es", "≈\u00a015\u00a0€"), ("zh", "≈\u00a0¥115"),
                                           ("hi", "≈\u00a0₹1,625"), ("en", "$17"), ("ar", "$17")])
def test_un_prix_en_dollars_est_montre_dans_la_monnaie_de_la_page(lang, attendu):
    assert contenu.converti(lang, "17") == attendu


def test_l_arrondi_suit_la_taille_du_prix():
    assert contenu.converti("fr", "552") == "≈\u00a0484\u00a0€"
    assert contenu.converti("fr", "10.20") == "≈\u00a08,95\u00a0€"
    assert contenu.converti("fr", "0.0002") == "≈\u00a00,00018\u00a0€"
    assert contenu.converti("zh", "552") == "≈\u00a0¥3,728"
    assert contenu.dollars("fr", "13.60") == "13,60\u00a0$" and contenu.dollars("hi", "13.60") == "$13.60"


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_aucun_prix_en_dollars_sans_sa_conversion(lang):
    texte = seo.hors_bloc(client.get(i18n.chemin(lang, "compare")).text)
    cartes = texte[texte.index("<h3>Google"):texte.index('<section aria-labelledby="choix">')]
    assert texte.count("<article") == texte.count("</article>") == 5 and texte.count("<dd") == texte.count("</dd>")
    if i18n.DEVISE[lang] == "USD":
        assert 'class="conv"' not in cartes
        return
    assert cartes.count('<bdi class="conv">≈') == len(contenu.CONCURRENTS_USD)
    assert "$" not in re.sub(r'<small class="usd">.*?</small>', "", cartes)
    calcul = re.search(r'<h2 id="calcul">.*?<p class="sub">(.*?)</p>', texte, re.S).group(1)
    assert contenu.converti(lang, "1") in calcul
