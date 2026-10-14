"""Tests du référencement des pages publiques (décision D65).

Ce qui est mesuré, et pourquoi :

  * les cinq pages publiques, dans les six langues, portent **un** bloc de
    référencement dans leur en-tête : adresse canonique, aperçus de partage,
    icône, données schema.org ; les pages privées n'en ont pas ;
  * le bloc ne contient que des **données** (un seul `<script>`, de type
    `application/ld+json`) et des adresses **du site** (`public_base_url`) ; une
    valeur hostile ne peut pas en sortir ;
  * les prix des données structurées sont ceux qu'applique l'API (D48) ;
  * à la racine, un robot de moteur reçoit la vitrine, un programme le JSON ;
  * `robots.txt`, `sitemap.xml`, `/favicon.ico` répondent, et `www.` renvoie vers
    l'adresse officielle.
"""

import html
import json
import re
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import accounts, i18n, seo
from app.config import get_settings
from app.main import app
from app.routes import guide

client = TestClient(app)
BASE = get_settings().public_base_url.rstrip("/")


def _bloc(texte: str) -> str:
    blocs = re.findall(r"<!-- seo -->(.*?)<!-- /seo -->", texte, re.S)
    assert len(blocs) == 1, f"{len(blocs)} blocs de référencement"
    assert texte.index("<!-- seo -->") < texte.index("</head>")
    return blocs[0]


def _graphe(bloc: str) -> dict:
    return json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', bloc, re.S).group(1))


@pytest.mark.parametrize("page", seo.PUBLIQUES)
@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_chaque_page_publique_a_son_bloc(lang, page):
    reponse = client.get(i18n.chemin(lang, page), headers={"Accept": "text/html"})
    assert reponse.status_code == 200
    bloc = _bloc(reponse.text)
    url = BASE + i18n.chemin(lang, page)
    assert f'<link rel="canonical" href="{url}">' in bloc
    assert f'<meta property="og:url" content="{url}">' in bloc
    assert f'<meta property="og:locale" content="{seo.LOCALES[lang]}">' in bloc
    assert f'<link rel="icon" href="{seo.ICONE}" type="image/svg+xml">' in bloc
    titre = re.search(r"<title>(.*?)</title>", reponse.text, re.S).group(1)
    assert f'<meta property="og:title" content="{html.escape(html.unescape(titre), quote=True)}">' in bloc
    # Que des données, que des adresses du site.
    assert re.findall(r"<script\b[^>]*>", bloc) == ['<script type="application/ld+json">']
    assert all(adresse.startswith(BASE) for adresse in re.findall(r"https?://[^\s\"'<>]+", bloc) if "schema.org" not in adresse)
    graphe = _graphe(bloc)["@graph"]
    assert [noeud["@type"] for noeud in graphe[:2]] == ["Organization", "WebSite"]
    assert graphe[0]["email"] == get_settings().contact_email


def test_les_prix_des_donnees_sont_ceux_de_l_api():
    graphe = _graphe(_bloc(client.get("/pricing").text))["@graph"]
    service = next(noeud for noeud in graphe if noeud["@type"] == "SoftwareApplication")
    prix = {offre["name"]: offre["price"] for offre in service["offers"]}
    attendus = {offre["nom"]: offre["prix_eur"] for offre in accounts.OFFRES if offre["prix_eur"] is not None}
    assert prix == {"Trial": 0, **attendus}
    assert all(offre["priceCurrency"] == "EUR" for offre in service["offers"])


def test_le_bloc_suit_la_configuration_et_reste_echappe():
    hostile = 'https://exemple.test/"><script>alert(1)</script>'
    reglages = get_settings().model_copy(update={"public_base_url": hostile, "contact_email": "a&b@exemple.test"})
    texte = guide.page(reglages, "en")
    bloc = _bloc(texte)
    assert len(re.findall(r"<script\b", bloc)) == 1 and bloc.count("</script>") == 1
    assert "alert(1)</script>" not in bloc and "<script>alert" not in bloc
    assert _graphe(bloc)["@graph"][0]["email"] == "a&b@exemple.test"
    assert "kder-ai.com" not in bloc


def test_les_pages_privees_n_ont_pas_de_bloc():
    for reponse in (
        client.get("/compte/connexion"),
        client.get("/en/compte/mot-de-passe-oublie"),
        client.post("/validate-form", data={"street": ""}),
        client.post("/signup", data={"email": "pas-un-email"}),
    ):
        assert "<!-- seo -->" not in reponse.text
        assert "noindex" in reponse.text


@pytest.mark.parametrize("agent", [
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1; +https://openai.com/gptbot",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0)",
])
def test_un_robot_de_moteur_recoit_la_vitrine(agent):
    reponse = client.get("/", headers={"User-Agent": agent, "Accept": "*/*"})
    assert reponse.headers["content-type"].startswith("text/html")
    assert reponse.headers["vary"] == "Accept, User-Agent"
    # Un robot qui demande du JSON le garde.
    assert client.get("/", headers={"User-Agent": agent, "Accept": "application/json"}).headers["content-type"] == "application/json"


@pytest.mark.parametrize("agent", ["curl/8.4.0", "python-requests/2.32", "testclient", ""])
def test_un_programme_garde_le_json(agent):
    assert client.get("/", headers={"User-Agent": agent, "Accept": "*/*"}).headers["content-type"] == "application/json"


def test_robots_txt():
    reponse = client.get("/robots.txt")
    assert reponse.status_code == 200 and reponse.headers["content-type"].startswith("text/plain")
    texte = reponse.text
    assert "User-agent: *\nAllow: /\n" in texte and f"Sitemap: {BASE}/sitemap.xml" in texte
    for robot in ("GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"):
        assert f"User-agent: {robot}\nAllow: /" in texte
    # Les pages privées restent visitables : elles disent elles-mêmes noindex.
    assert "Disallow: /compte" not in texte


def test_le_plan_du_site_liste_les_pages_publiques_dans_les_six_langues():
    reponse = client.get("/sitemap.xml")
    assert reponse.status_code == 200 and reponse.headers["content-type"].startswith("application/xml")
    racine = ET.fromstring(reponse.content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}
    entrees = racine.findall("s:url", ns)
    assert len(entrees) == len(seo.PUBLIQUES) * len(i18n.LANGUES)
    attendues = {BASE + i18n.chemin(lang, page) for page in seo.PUBLIQUES for lang in i18n.LANGUES}
    assert {entree.find("s:loc", ns).text for entree in entrees} == attendues
    for entree in entrees:
        versions = {lien.get("hreflang"): lien.get("href") for lien in entree.findall("x:link", ns)}
        assert set(versions) == set(i18n.LANGUES) | {"x-default"}
        assert all(adresse.startswith(BASE) for adresse in versions.values())


def test_icone_et_adresse_unique():
    icone = client.get("/favicon.ico", follow_redirects=False)
    assert icone.status_code == 301 and icone.headers["location"] == seo.ICONE
    svg = client.get(seo.ICONE)
    assert svg.headers["content-type"].startswith("image/svg+xml") and svg.headers["x-content-type-options"] == "nosniff"
    hote = BASE.split("://", 1)[1]
    www = client.get("/pricing?x=1", headers={"Host": f"www.{hote}"}, follow_redirects=False)
    assert www.status_code == 301 and www.headers["location"] == f"{BASE}/pricing?x=1"
    assert client.get("/pricing", headers={"Host": hote}).status_code == 200
