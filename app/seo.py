"""Référencement des pages publiques (D65) : moteurs de recherche et moteurs d'IA.

Ce que les moteurs lisent, et ce que le site leur donne :

* **un seul bloc**, dans l'en-tête des pages publiques (accueil, tarifs, guide,
  essai gratuit, inscription, et depuis D66 agents IA et comparatif), entre `<!-- seo -->` et `<!-- /seo -->`, produit
  ici : adresse canonique, aperçus de partage (Open Graph, Twitter), icône, et
  données structurées schema.org en JSON-LD. Les pages privées (espace client,
  connexion, mot de passe) n'en ont pas : elles sont `noindex` ;
* `robots.txt` (tout le site public ouvert, moteurs d'IA compris, avec l'adresse
  du plan du site) et `sitemap.xml` (les pages publiques × six langues, avec leurs
  versions de langue `hreflang` en adresses complètes) ;
* une seule adresse : `www.` renvoie en `301` vers `public_base_url` (Google avait
  indexé les deux).

Deux règles des pages publiques sont **précisées**, pas abandonnées : « aucun
script » devient « aucun script exécutable » (le seul `<script>` admis est un bloc
de données `application/ld+json`, sérialisé par `json.dumps`, `</` échappé) ;
« aucune URL » devient « aucune URL vers un autre site » (les seules adresses
complètes sont celles de `public_base_url`). Les deux ne valent que dans le bloc :
`hors_bloc()` le retire, et le reste de la page tient les règles d'avant.

**Ce qui est montré est ce qui est appliqué** (D48) : les prix des données
structurées viennent d'`accounts.OFFRES`, le contact et l'adresse de la
configuration, le titre et la description de la page elle-même.
"""

from __future__ import annotations

import html
import json
import re
from functools import lru_cache

from fastapi import APIRouter
from fastapi.responses import RedirectResponse, Response

from app import accounts, i18n
from app.config import get_settings

DEBUT, FIN = "<!-- seo -->", "<!-- /seo -->"
_BLOC = re.compile(re.escape(DEBUT) + r".*?" + re.escape(FIN) + r"\n?", re.S)

#: Les pages publiques, indexables, par leur adresse française (`""` = l'accueil).
PUBLIQUES = ("", "pricing", "guide", "validate-form", "signup", "agents", "compare")
LOCALES = {"fr": "fr_FR", "en": "en_US", "zh": "zh_CN", "hi": "hi_IN", "es": "es_ES", "ar": "ar_AR"}
IMAGE = "/images/commercante-colis.jpg"
ICONE = "/images/favicon.svg"

#: Robots des moteurs de recherche et d'IA : à la racine, ils reçoivent la vitrine
#: même s'ils n'annoncent pas `text/html` (Bing, GPTBot, ClaudeBot et Perplexity
#: envoient `*/*`, et recevaient le JSON de l'API).
ROBOTS = re.compile(
    r"bot\b|bot/|crawler|spider|slurp|facebookexternalhit|embedly|whatsapp|telegram|"
    r"perplexity|claude|gptbot|chatgpt|oai-searchbot|applebot|ccbot|bytespider|"
    r"amazonbot|meta-externalagent|google-extended|googleother|duckduck|baidu|yandex",
    re.I,
)

#: Robots d'IA nommés dans `robots.txt` : la règle `*` les couvre déjà, mais un
#: groupe explicite lève le doute pour qui lit le fichier.
ROBOTS_IA = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "Claude-User",
             "PerplexityBot", "Perplexity-User", "Google-Extended", "Applebot-Extended", "Bingbot")


def est_robot(agent: str) -> bool:
    return bool(ROBOTS.search(agent or ""))


def hors_bloc(texte: str) -> str:
    """La page sans le bloc de référencement : les règles d'avant s'y lisent telles quelles."""
    return _BLOC.sub("", texte)


def _base(reglages=None) -> str:
    return (reglages or get_settings()).public_base_url.rstrip("/")


def _json(donnees) -> str:
    # <, > et & échappés : la donnée ne peut ni fermer la balise qui la porte, ni en ouvrir une.
    texte = json.dumps(donnees, ensure_ascii=False, separators=(",", ":"))
    return texte.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _lu(trouve) -> str:
    return html.unescape(trouve.group(1)).strip() if trouve else ""


def _offres() -> list[dict]:
    """Les offres en euros, telles que l'API les facture (`accounts.OFFRES`)."""
    offres = []
    for offre in accounts.OFFRES:
        if offre["nom"] == "Essai gratuit":
            offres.append({"@type": "Offer", "name": "Trial", "price": 0, "priceCurrency": "EUR"})
        elif offre["prix_eur"] is not None:
            offres.append({"@type": "Offer", "name": offre["nom"], "price": offre["prix_eur"], "priceCurrency": "EUR"})
    return offres


def donnees(lang: str, page: str, titre: str, description: str, reglages=None) -> dict:
    """Le graphe schema.org de la page : l'organisation, le site, et ce que la page présente."""
    reglages = reglages or get_settings()
    base = _base(reglages)
    url = base + i18n.chemin(lang, page)
    organisation = {"@type": "Organization", "@id": f"{base}/#organisation", "name": "Address Validator",
                    "url": f"{base}/", "logo": base + ICONE, "email": reglages.contact_email}
    site = {"@type": "WebSite", "@id": f"{base}/#site", "name": "Address Validator", "url": f"{base}/",
            "inLanguage": list(i18n.LANGUES), "publisher": {"@id": f"{base}/#organisation"}}
    service = {"@type": "SoftwareApplication", "@id": f"{base}/#api", "name": "Address Validator API",
               "applicationCategory": "DeveloperApplication", "operatingSystem": "Web",
               "url": f"{base}/", "description": description, "offers": _offres(),
               "publisher": {"@id": f"{base}/#organisation"}}
    if page in ("", "pricing"):
        principal = service
    elif page in ("guide", "agents"):
        principal = {"@type": "TechArticle", "headline": titre, "description": description, "url": url,
                     "inLanguage": lang, "about": {"@id": f"{base}/#api"}}
    elif page == "validate-form":
        principal = {"@type": "WebApplication", "name": titre, "description": description, "url": url,
                     "applicationCategory": "BusinessApplication", "operatingSystem": "Web",
                     "offers": {"@type": "Offer", "price": 0, "priceCurrency": "EUR"}}
    else:
        principal = {"@type": "WebPage", "name": titre, "description": description, "url": url, "inLanguage": lang}
    return {"@context": "https://schema.org", "@graph": [organisation, site, principal]}


def enrichir(texte: str, lang: str, page: str, reglages=None) -> str:
    """Pose le bloc de référencement dans l'en-tête d'une page publique."""
    base = _base(reglages)
    url = base + i18n.chemin(lang, page)
    titre = _lu(re.search(r"<title>(.*?)</title>", texte, re.S))
    description = _lu(re.search(r'<meta name="description" content="([^"]*)"', texte))
    e = lambda valeur: html.escape(valeur, quote=True)  # noqa: E731
    lignes = [
        DEBUT,
        f'<link rel="canonical" href="{e(url)}">',
        f'<link rel="icon" href="{ICONE}" type="image/svg+xml">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="Address Validator">',
        f'<meta property="og:title" content="{e(titre)}">',
        f'<meta property="og:description" content="{e(description)}">',
        f'<meta property="og:url" content="{e(url)}">',
        f'<meta property="og:image" content="{e(base + IMAGE)}">',
        f'<meta property="og:locale" content="{LOCALES[lang]}">',
        *(f'<meta property="og:locale:alternate" content="{LOCALES[autre]}">' for autre in i18n.LANGUES if autre != lang),
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{e(titre)}">',
        f'<meta name="twitter:description" content="{e(description)}">',
        f'<meta name="twitter:image" content="{e(base + IMAGE)}">',
        f'<script type="application/ld+json">{_json(donnees(lang, page, titre, description, reglages))}</script>',
        FIN,
    ]
    return texte.replace("</head>", "\n".join(lignes) + "\n</head>", 1)


@lru_cache(maxsize=1)
def _robots(base: str) -> str:
    ia = "".join(f"User-agent: {nom}\nAllow: /\n\n" for nom in ROBOTS_IA)
    return (
        "# Address Validator : tout le site public est ouvert aux moteurs de recherche et d'IA.\n"
        "# Les pages privées (/compte…) restent visitables : elles disent elles-mêmes noindex.\n"
        "User-agent: *\nAllow: /\nDisallow: /dashboard\nDisallow: /prospection\n\n"
        f"{ia}Sitemap: {base}/sitemap.xml\n"
    )


@lru_cache(maxsize=1)
def _sitemap(base: str) -> str:
    entrees = []
    for page in PUBLIQUES:
        versions = "".join(
            f'<xhtml:link rel="alternate" hreflang="{autre}" href="{base}{i18n.chemin(autre, page)}"/>'
            for autre in i18n.LANGUES
        ) + f'<xhtml:link rel="alternate" hreflang="x-default" href="{base}{i18n.chemin("en", page)}"/>'
        for lang in i18n.LANGUES:
            entrees.append(f"<url><loc>{base}{i18n.chemin(lang, page)}</loc>{versions}</url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(entrees) + "\n</urlset>\n"
    )


router = APIRouter()


@router.get("/robots.txt", include_in_schema=False)
async def robots_txt() -> Response:
    return Response(_robots(_base()), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml() -> Response:
    return Response(_sitemap(_base()), media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> RedirectResponse:
    # Les navigateurs et les moteurs demandent /favicon.ico ; l'icône du site est un SVG.
    return RedirectResponse(ICONE, status_code=301)
