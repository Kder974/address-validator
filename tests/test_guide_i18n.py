"""Le guide d'intégration en six langues (décisions D48, D57).

Ce que ces tests mesurent, et pourquoi :

  * chaque langue a **sa** route littérale (`/guide` en français, `/en/guide`…),
    les mêmes en-têtes que le guide français (public, cachable cinq minutes,
    `form-action 'none'`), et rien à charger : ni script, ni image, ni feuille ;
  * la navigation, le sélecteur de langue et les `hreflang` restent dans la page
    « guide » de chaque langue ; le sommaire n'est fait que d'ancres qui existent ;
  * D48 tient dans chaque langue : la base des exemples est `public_base_url`,
    le prix, les plafonds, l'essai, les durées du cache et la version x402 sont
    **injectés** (la page suit le code quand il change), les en-têtes et les huit
    codes sont ceux de la route, les corps d'exemple ceux de `app/schemas.py` ;
  * les exemples de code restent du code : seuls leurs commentaires changent
    d'une langue à l'autre, et la clé d'exemple n'est jamais une clé ;
  * une valeur injectée est échappée.
"""

import html
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import accounts, cache, i18n, x402
from app.config import get_settings
from app.main import app
from app.routes import guide
from app.routes.guide import CODES
from app.schemas import AddressInput, AddressOutput, ValidationRequest, ValidationResponse
from app.web import public_headers

client = TestClient(app)

#: La clé des exemples : un gabarit par langue, jamais une clé.
CLES = {
    "fr": "sk_live_VOTRE_CLE",
    "en": "sk_live_YOUR_KEY",
    "zh": "sk_live_YOUR_KEY",
    "hi": "sk_live_YOUR_KEY",
    "es": "sk_live_TU_CLAVE",
    "ar": 'sk_live_<bdi class="ar">مفتاحك</bdi>',
}


def _guide(lang):
    return client.get(i18n.chemin(lang, "guide"))


def _texte(fragment: str) -> str:
    """Le texte lu par le visiteur : balises retirées, entités décodées, espaces réduits."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


def _code(page: str) -> list[str]:
    """Les blocs de code sans leurs commentaires ni leurs chaînes : ce qui ne se traduit pas."""
    code = html.unescape(re.sub(r"<[^>]+>", "", "\n".join(re.findall(r"<pre\b[^>]*>(.*?)</pre>", page, re.S))))
    code = re.sub(r'"""[\s\S]*?"""', "", code)
    code = re.sub(r"(?<!:)//.*|#.*", "", code)
    return re.sub(r'"[^"\n]*"|\'[^\'\n]*\'', '""', code).split()


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_chaque_langue_a_sa_route_et_les_en_tetes_du_guide(lang):
    reponse = _guide(lang)

    assert reponse.status_code == 200
    for nom, valeur in public_headers(form=False, max_age=300).items():
        assert reponse.headers[nom] == valeur, nom
    balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', reponse.text)
    assert balise.group(1).split("-")[0] == lang
    assert bool(balise.group(2)) == (lang in i18n.RTL)
    assert '<meta name="robots" content="index, follow">' in reponse.text


def test_les_routes_des_langues_sont_litterales():
    chemins = {route.path for route in app.routes}

    assert {i18n.chemin(lang, "guide") for lang in i18n.LANGUES} <= chemins
    assert not any("{" in chemin and chemin.endswith("/guide") for chemin in chemins)
    assert client.get("/de/guide").status_code == 404


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_le_guide_ne_charge_rien_et_ne_cite_que_la_base_publique(lang):
    texte = _guide(lang).text
    base = get_settings().public_base_url.rstrip("/")

    for interdit in ("<script", "<img", "src=", "@import", "url(", "<form", "{{", "BASE_URL"):
        assert interdit not in texte, interdit
    assert all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", texte))
    adresses = re.findall(r"https?://[^\s\"'<]+", texte)
    assert adresses and all(adresse == f"{base}/validate" for adresse in adresses)


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_navigation_selecteur_et_hreflang_restent_sur_le_guide(lang):
    texte = _guide(lang).text
    selecteur = re.findall(r'<a href="([^"]*)" hreflang="([\w-]+)"', texte)
    alternatives = re.findall(r'<link rel="alternate" hreflang="([\w-]+)" href="([^"]*)">', texte)

    assert selecteur == [(i18n.chemin(autre, "guide"), autre) for autre in i18n.LANGUES]
    assert dict(alternatives) == {
        **{autre: i18n.chemin(autre, "guide") for autre in i18n.LANGUES},
        "x-default": i18n.chemin("en", "guide"),
    }
    for href, _ in selecteur:
        assert client.get(href).status_code == 200
    for page in i18n.PAGES:
        assert f'href="{i18n.chemin(lang, page)}"' in texte, page


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_le_sommaire_n_est_fait_que_d_ancres_qui_existent(lang):
    texte = _guide(lang).text
    ancres = re.findall(r'href="#([^"]+)"', texte)

    assert len(ancres) > 20
    for ancre in ancres:
        assert f'id="{ancre}"' in texte, ancre
    # Le code défile dans son bloc, jamais la page.
    assert "overflow-x:auto" in re.search(r"\.cb pre\{[^}]*\}", texte).group(0)


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_faits_publies_sont_ceux_du_code(lang):
    texte = _guide(lang).text
    reglages = get_settings()

    assert texte.count(i18n.nombre(lang, reglages.x402_price)) == 5
    assert i18n.nombre(lang, reglages.validate_form_rate_limit_per_day) in texte
    for en_tete in ("X-Payment-Mode", "X-Plan", "X-Quota-Remaining"):
        assert en_tete in texte
        assert en_tete in (Path(guide.__file__).parent / "validate.py").read_text(encoding="utf-8")
    for plan in accounts.PLANS:
        assert re.search(rf"<code[^>]*>{plan}</code>", texte), plan
    assert f'{{"reason": "{accounts.REFUS_QUOTA_ATTEINT}"}}' in texte
    assert texte.count(f'{{"x402Version": {x402.X402_VERSION}}}') == 2
    for schema in (ValidationRequest, AddressInput, ValidationResponse, AddressOutput):
        for champ in schema.model_fields:
            assert f'<span class="k">"{champ}"</span>' in texte, champ
    # Les huit codes, une ligne chacun.
    assert texte.count('class="code-row') == len(CODES)
    for code, _situation, _action in CODES:
        assert f'id="code-{code}"' in texte and f'<span class="sc">{code}</span>' in texte, code


def test_la_page_francaise_dit_les_situations_et_actions_de_codes():
    texte = _guide("fr").text
    lu = _texte(texte)

    for code, situation, action in CODES:
        assert f'<span class="cr-t">{situation}</span>' in texte, code
        assert " ".join(action.replace("`", "").replace("**", "").split()) in lu, code


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_chiffres_sont_injectes_pas_recopies(lang, monkeypatch):
    """Changer le code change la page : aucun chiffre n'est écrit en dur dans un gabarit."""
    monkeypatch.setattr(accounts, "JOURS_ESSAI", 8642)
    monkeypatch.setattr(accounts, "VALIDATIONS_ESSAI", 7531)
    monkeypatch.setattr(cache, "CACHE_TTL", 864 * 86400)
    monkeypatch.setattr(cache, "CACHE_TTL_DEGRADED", 975 * 60)
    monkeypatch.setattr(x402, "X402_VERSION", 7)
    reglages = get_settings().model_copy(
        update={
            "x402_price": 0.05,
            "validate_form_rate_limit_per_day": 9753,
            "public_base_url": "https://exemple.test/",
            "contact_email": "contact@exemple.test",
        }
    )

    texte = guide.page(reglages, lang)

    def n(valeur):
        return i18n.nombre(lang, valeur)

    assert texte.count(n(8642)) == 1
    assert texte.count(n(7531)) == 2
    assert texte.count(n(9753)) == 1
    assert texte.count(n(864)) == 3
    assert texte.count(n(975)) == 1
    assert texte.count(n(0.05)) == 5 and n(0.02) not in texte
    assert texte.count('{"x402Version": 7}') == 2
    assert texte.count("https://exemple.test/validate") == 4 and "kder-ai.com" not in texte
    assert texte.count("contact@exemple.test") == 2


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_le_code_reste_du_code_et_la_cle_un_gabarit(lang):
    texte = _guide(lang).text
    reglages = get_settings()

    assert _code(texte) == _code(_guide("fr").text)
    assert texte.count(CLES[lang]) == 2
    assert not re.search(r"sk_live_[A-Za-z0-9]{16,}", texte)
    for secret in (reglages.api_secret_key, reglages.dashboard_password):
        if secret:
            assert secret not in texte
    for identifiant in ("X-API-Key", "Retry-After", "cache_key", "/validate-form", "Shopify", "WooCommerce"):
        assert identifiant in texte, identifiant


def test_une_valeur_injectee_est_echappee():
    reglages = get_settings().model_copy(
        update={"public_base_url": 'https://exemple.test/"><script>x</script>', "contact_email": "a&b@exemple.test"}
    )

    for lang in i18n.LANGUES:
        texte = guide.page(reglages, lang)
        assert "<script" not in texte and "&lt;script&gt;" in texte
        assert "a&amp;b@exemple.test" in texte
