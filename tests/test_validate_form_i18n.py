"""Tests de l'essai gratuit dans les six langues (`/validate-form`, `/<langue>/validate-form`, D46, D57).

Ce que ces tests mesurent, et pourquoi :

  * chaque langue a sa page, à son adresse, dans sa langue et son sens d'écriture,
    sans script ni ressource externe, et son formulaire poste **sur sa propre
    adresse** avec les noms de champs de l'API payante ;
  * un `POST` répond dans la langue de son adresse, avec les en-têtes d'avant
    (`no-store`, `noindex`) et le **vrai** résultat du pipeline : adresse
    standardisée, coordonnées, livrabilité, confiance, essais restants ; aucun
    écran d'exemple de la maquette n'est servi ;
  * un résultat dégradé n'est jamais une adresse invalide, dans aucune langue ;
  * la saisie refusée est réaffichée échappée, avec des erreurs dans la langue ;
  * le `429` donne l'attente exacte de `Retry-After` dans la langue, à la place
    du formulaire ; le plafond est **par IP**, toutes langues confondues ;
  * les chiffres affichés sont ceux du code et de la configuration (D48), et le
    pluriel suit la langue (l'arabe a six formes, le chinois aucune).

Aucun appel réseau : le pipeline est remplacé et le plafond remis à zéro.
"""

import html
import re

import pytest
from fastapi.testclient import TestClient

from app import accounts, i18n, ratelimit
from app.config import get_settings
from app.main import app
from app.routes import validate_form as route
from app.routes.metrics import metrics
from app.routes.validate_form import CHAMPS, TEXTES, _texte

client = TestClient(app)

RECONNUE = {
    "validated": True,
    "standardized": {
        "line1": "100 Stewart Street",
        "line2": "",
        "city": "Seattle",
        "state": "WA",
        "zip": "98101",
        "country": "US",
    },
    "latitude": 47.6105,
    "longitude": -122.341,
    "deliverability": "residential",
    "confidence": 1.0,
    "degraded": False,
}
DEGRADE = {**RECONNUE, "validated": False, "deliverability": "invalid", "confidence": 0.05, "degraded": True}
NON_RECONNUE = {**RECONNUE, "validated": False, "deliverability": "invalid", "confidence": 0.05}
ADRESSE = {"street": "100 stewart st", "city": "seattle", "state": "wa", "zip": "98101", "country": "US"}
NOMS = [nom for nom, _libelle, _exemple in CHAMPS]


@pytest.fixture(autouse=True)
def plafond_vide():
    ratelimit.reset()
    yield
    ratelimit.reset()


def _chemin(lang: str) -> str:
    return i18n.chemin(lang, "validate-form")


def _pipeline(monkeypatch, resultat: dict, appels: list | None = None):
    def _valide(adresse: dict) -> tuple[dict, str]:
        if appels is not None:
            appels.append(dict(adresse))
        return dict(resultat), "cle"

    monkeypatch.setattr(route, "perform_validation", _valide)


def _reglages(monkeypatch, **valeurs):
    reglages = get_settings().model_copy(update=valeurs)
    monkeypatch.setattr(route, "get_settings", lambda: reglages)
    return reglages


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_chaque_langue_a_sa_page_autonome(lang):
    page = client.get(_chemin(lang))
    texte = page.text

    assert page.status_code == 200
    assert page.headers["cache-control"] == "public, max-age=300"
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "form-action 'self'" in csp and "script-src" not in csp
    assert 'name="robots" content="index, follow"' in texte
    balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', texte)
    assert balise.group(1).split("-")[0] == lang
    assert bool(balise.group(2)) == (lang in i18n.RTL)
    for interdit in ("<script", "{{", "\0", "http://", "https://", "<img", "@import"):
        assert interdit not in texte
    assert all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", texte))
    # Aucun écran d'exemple de la maquette : pas de résultat sans essai.
    for exemple in ('class="illus"', 'class="ex-cap"', "9999 Stewart", "47.61050", "Retry-After: 2220"):
        assert exemple not in texte
    assert 'class="examples"' not in texte


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_le_formulaire_poste_sur_sa_langue_avec_les_champs_de_l_api(lang):
    texte = client.get(_chemin(lang)).text

    assert f'method="post" action="{_chemin(lang)}"' in texte
    assert re.findall(r'<input[^>]*\bname="(\w+)"', texte) == NOMS
    for nom in ("street", "city", "state", "zip"):
        assert re.search(rf'<input[^>]*name="{nom}"[^>]*\brequired\b', texte)
    assert re.search(r'<input[^>]*name="country"[^>]*minlength="2" maxlength="2"', texte)
    # Exemple américain hors du français, comme sur les maquettes.
    rue = "1 Rue de Paris" if lang == "fr" else "100 Stewart St"
    assert f'name="street" type="text" dir="ltr" placeholder="{rue}"' in texte
    for _nom, libelle, _exemple in TEXTES[lang]["champs"]:
        assert f">{html.escape(libelle)}</label>" in texte


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_la_navigation_et_le_selecteur_restent_dans_la_page(lang):
    texte = client.get(_chemin(lang)).text

    for page in ("pricing", "guide", "signup", "compte"):
        assert f'href="{i18n.chemin(lang, page)}"' in texte
    alternatives = dict(re.findall(r'<link rel="alternate" hreflang="([\w-]+)" href="([^"]*)">', texte))
    assert alternatives == {**{autre: _chemin(autre) for autre in i18n.LANGUES}, "x-default": _chemin("en")}
    selecteur = re.findall(r'<a href="([^"]*)" hreflang="([\w-]+)"', texte)
    assert selecteur == [(_chemin(autre), autre) for autre in i18n.LANGUES]
    for href, _autre in selecteur:
        assert client.get(href).status_code == 200


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_chiffres_affiches_sont_ceux_de_la_configuration(lang, monkeypatch):
    reglages = _reglages(
        monkeypatch, validate_form_rate_limit_per_day=3, x402_price=0.05, contact_email="contact@exemple.test"
    )

    texte = client.get(_chemin(lang)).text

    assert _texte(lang, "essais", 3) in texte
    assert f"{i18n.nombre(lang, 0.05)}" in texte and i18n.nombre(lang, 0.02) not in texte
    assert texte.count(reglages.contact_email) == 2


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_un_essai_reconnu_montre_le_vrai_resultat_dans_la_langue(lang, monkeypatch):
    appels: list[dict] = []
    _pipeline(monkeypatch, RECONNUE, appels)
    avant = metrics.snapshot()["validate_form_requests"]

    reponse = client.post(_chemin(lang), data=ADRESSE)
    texte = reponse.text

    assert reponse.status_code == 200
    assert reponse.headers["cache-control"] == "no-store"
    assert 'name="robots" content="noindex, nofollow"' in texte
    assert appels == [ADRESSE]
    assert metrics.snapshot()["validate_form_requests"] - avant == 1
    assert TEXTES[lang]["ok"] in texte and TEXTES[lang]["ko"] not in texte
    assert TEXTES[lang]["degrade"] not in texte
    # La standardisation marque ce qu'elle a changé ; le pays saisi est resté tel quel.
    assert "<mark>100 Stewart Street</mark><br>98101 <mark>Seattle</mark><br><mark>WA</mark><br>US" in texte
    assert "<bdi dir=\"ltr\">100 stewart st, seattle, wa, 98101, US</bdi>" in texte
    assert ">47.61050, -122.34100<" in texte and 'class="ok-grid"' in texte
    assert '<p class="chip">residential</p>' in texte and '<p class="g-num">1.00</p>' in texte
    assert 'stroke-dashoffset="0.000"' in texte
    plafond = get_settings().validate_form_rate_limit_per_day
    assert _texte(lang, "reste", plafond - 1) in texte
    assert texte.count('<b class="u"></b>') == 1 and texte.count("<b></b>") == plafond - 1
    assert f'href="{i18n.chemin(lang, "signup")}"' in texte
    # Le formulaire reste là, rempli, et poste toujours dans la langue.
    assert f'method="post" action="{_chemin(lang)}"' in texte
    assert 'value="100 stewart st"' in texte


def test_les_reperes_de_la_jauge_viennent_du_bareme(monkeypatch):
    """Sans coordonnées, le barème rend 0.00 / 0.50 / 0.99 : la jauge le dit, pas la maquette."""
    _pipeline(monkeypatch, {**RECONNUE, "latitude": None, "longitude": None, "confidence": 0.99})

    texte = client.post("/validate-form", data=ADRESSE).text

    assert 'class="ok-grid no-map"' in texte and "GPS" not in texte
    for valeur in ("0.00", "0.50", "0.99"):
        assert f"<b>{valeur}</b>" in texte
    assert '<li class="cur" style="--c:#18b47c"><b>0.99</b>' in texte
    assert "<b>0.05</b>" not in texte and "<b>1.00</b>" not in texte


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_un_resultat_degrade_n_est_jamais_une_adresse_invalide(lang, monkeypatch):
    _pipeline(monkeypatch, DEGRADE)

    texte = client.post(_chemin(lang), data=ADRESSE).text

    assert TEXTES[lang]["degrade"] in texte and TEXTES[lang]["degrade_note"] in texte
    assert TEXTES[lang]["ko"] not in texte and TEXTES[lang]["ok"] not in texte
    assert 'class="badge b-ko"' not in texte and 'class="big ko"' not in texte
    # Les valeurs creuses ne vivent que dans la boîte grisée, dites sans portée.
    boite = texte.split('class="muted-box"')[1].split('class="ok-foot"')[0]
    assert texte.count(">invalid<") == 1 and ">invalid<" in boite and ">0.05<" in boite
    assert _texte(lang, "sans_portee", adresse="X").split("X")[0] in boite


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_une_adresse_non_reconnue_le_dit_dans_la_langue(lang, monkeypatch):
    _pipeline(monkeypatch, NON_RECONNUE)

    texte = client.post(_chemin(lang), data=ADRESSE).text

    assert TEXTES[lang]["ko"] in texte and TEXTES[lang]["ko_verdict"] in texte
    assert '<p class="big ko">invalid</p>' in texte and "--v:5%" in texte
    assert TEXTES[lang]["ok"] not in texte and TEXTES[lang]["degrade"] not in texte


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_une_saisie_refusee_est_reaffichee_avec_des_erreurs_dans_la_langue(lang, monkeypatch):
    appels: list[dict] = []
    _pipeline(monkeypatch, RECONNUE, appels)

    reponse = client.post(_chemin(lang), data={**ADRESSE, "street": "  ", "country": "France"})
    texte = reponse.text

    assert reponse.status_code == 400
    assert reponse.headers["cache-control"] == "no-store"
    assert appels == []
    rue = TEXTES[lang]["champs"][0][1]
    assert f"<li>{html.escape(_texte(lang, 'requis', libelle=rue))}</li>" in texte
    assert f"<li>{html.escape(TEXTES[lang]['pays_invalide'])}</li>" in texte
    assert 'role="alert"' in texte and 'value="seattle"' in texte and 'value="FRANCE"' in texte


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_la_saisie_est_echappee_dans_chaque_langue(lang, monkeypatch):
    _pipeline(monkeypatch, RECONNUE)
    piege = '<script>alert(1)</script>"{{x}}'

    reponse = client.post(_chemin(lang), data={**ADRESSE, "street": piege})

    assert reponse.status_code == 200
    assert "<script>" not in reponse.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;&quot;{{x}}" in reponse.text


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_le_429_donne_l_attente_exacte_dans_la_langue(lang, monkeypatch):
    _pipeline(monkeypatch, RECONNUE)
    _reglages(monkeypatch, validate_form_rate_limit_per_day=1)
    assert client.post(_chemin(lang), data=ADRESSE).status_code == 200

    bloque = client.post(_chemin(lang), data=ADRESSE)
    texte = bloque.text

    assert bloque.status_code == 429
    assert bloque.headers["cache-control"] == "no-store"
    secondes = int(bloque.headers["retry-after"])
    assert 0 < secondes <= route.FENETRE_ESSAI_SECONDES
    minutes = max(1, (secondes + 59) // 60)
    assert _texte(lang, "attente", minutes) in texte
    assert _texte(lang, "minutes", minutes) in texte
    assert f"Retry-After: {secondes}" in texte
    assert TEXTES[lang]["plafond_titre"] in texte and _texte(lang, "plafond_texte", 1) in texte
    # Une page dédiée, à la place du formulaire, avec la porte de sortie de la langue.
    assert "<form" not in texte and 'name="robots" content="noindex, nofollow"' in texte
    assert f'href="{i18n.chemin(lang, "signup")}"' in texte


def test_le_dernier_essai_le_dit_et_vide_la_barre(monkeypatch):
    _pipeline(monkeypatch, RECONNUE)
    _reglages(monkeypatch, validate_form_rate_limit_per_day=1)

    texte = client.post("/en/validate-form", data=ADRESSE).text

    assert TEXTES["en"]["dernier"] in texte and "free tries left" not in texte
    assert texte.count('<b class="u"></b>') == 1 and "<b></b>" not in texte


def test_le_plafond_est_par_ip_quelle_que_soit_la_langue(monkeypatch):
    _pipeline(monkeypatch, RECONNUE)
    _reglages(monkeypatch, validate_form_rate_limit_per_day=2)
    ip = {"X-Forwarded-For": "203.0.113.50"}

    codes = [client.post(_chemin(lang), data=ADRESSE, headers=ip).status_code for lang in ("fr", "en", "ar", "zh")]

    assert codes == [200, 200, 429, 429]


@pytest.mark.parametrize(
    "lang, n, attendu",
    [
        ("fr", 1, "Il vous reste 1 essai gratuit aujourd'hui."),
        ("fr", 7, "Il vous reste 7 essais gratuits aujourd'hui."),
        ("en", 1, "You have 1 free try left today."),
        ("es", 1, "Te queda 1 prueba gratuita hoy."),
        ("hi", 1, "आज आपका 1 निःशुल्क प्रयास बचा है।"),
        ("zh", 1, "今天还剩 1 次免费测试。"),
        ("ar", 1, "بقيت لك تجربة مجانية واحدة اليوم."),
        ("ar", 2, "بقيت لك تجربتان مجانيتان اليوم."),
        ("ar", 7, "بقيت لك 7 تجارب مجانية اليوم."),
        ("ar", 11, "بقيت لك 11 تجربة مجانية اليوم."),
        ("ar", 103, "بقيت لك 103 تجارب مجانية اليوم."),
    ],
)
def test_le_pluriel_suit_la_langue(lang, n, attendu):
    assert _texte(lang, "reste", n) == attendu


def test_chaque_langue_a_tous_les_messages_et_les_memes_champs():
    for lang in i18n.LANGUES:
        assert TEXTES[lang].keys() == TEXTES["fr"].keys()
        assert [nom for nom, _l, _e in TEXTES[lang]["champs"]] == NOMS
    assert TEXTES["fr"]["champs"] is CHAMPS
    # Le texte de l'essai offert et du prix vient du code, pas des gabarits.
    assert str(accounts.JOURS_ESSAI) not in TEXTES["fr"]["suite"]
