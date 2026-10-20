"""`/signup`, la page de la clé et `/compte` dans les six langues (D44, D45, D54, D57).

Ce que ces tests mesurent, et pourquoi :

  * chaque langue a ses routes **littérales** (`/en/signup`, `/en/compte`,
    `/en/compte/cle`) et un `POST` répond dans la langue de son chemin ;
  * les règles de D44 tiennent dans chaque langue : formulaire sans champ `plan`
    (D54), clé montrée une fois sur une page `no-store` et `noindex`, empreinte
    seule en base, `/compte` fermé par défaut (401 + défi Basic), refus 400, 409,
    429 (`Retry-After`), 503 ;
  * les chiffres affichés sont ceux du compte et du code : clé réelle, boutique,
    plan, quota, fin d'essai datée dans la langue, compteurs, jauge, remise à zéro,
    économies dans la monnaie de la langue avec leur formule ;
  * les bandeaux d'état sont ceux que le code produit (403, 402, plan payant) ;
  * tout ce qui vient du visiteur est échappé, et aucune clé complète n'est écrite
    dans un gabarit.

Les mots de passe sont hachés au coût bcrypt minimal ici : le coût 12 est mesuré
par `tests/test_signup.py`, ces tests mesurent les pages.
"""

import base64
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import accounts, i18n, ratelimit
from app.config import get_settings
from app.main import app
from app.models import Customer, CustomerValidation
from app.routes import signup as route
from app.routes import tableau
from app import seo

client = TestClient(app)

MOT_DE_PASSE = "un-mot-de-passe-solide-12"
CLE = re.compile(r"sk_live_[A-Za-z0-9]{32}")
GABARITS = Path(route.GABARITS)
DOSSIERS = ("inscription", "inscription-cle", "compte")


def _auth(email: str, mot_de_passe: str = MOT_DE_PASSE) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{email}:{mot_de_passe}".encode()).decode()}


def _forme(lang: str, **champs) -> dict:
    return {"email": f"boutique-{lang}@example.re", "password": MOT_DE_PASSE, "shop_name": "Boutique Test",
            "platform": "shopify", **champs}


def _signup(lang: str) -> str:
    return i18n.chemin(lang, "signup")


def _compte(lang: str) -> str:
    return i18n.chemin(lang, "compte")


def _lu(reponse) -> str:
    """Le texte tel que le visiteur le lit (entités résolues)."""
    return unescape(reponse.text)


def _bloc(lang: str, dossier: str, nom: str) -> str:
    """Le texte d'un bloc `<!--@si:nom-->` du gabarit, tel que la maquette l'écrit."""
    gabarit = (GABARITS / dossier / f"{lang}.html").read_text(encoding="utf-8")
    return re.search(rf"<!--@si:{nom}-->(.*?)<!--@fin:{nom}-->", gabarit, re.S).group(1)


def _sans_ressource_externe(page: str, lang: str) -> None:
    page = seo.hors_bloc(page)  # D65 : le bloc de référencement a ses propres règles
    balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', page)
    assert balise and balise.group(1).split("-")[0] == lang
    assert bool(balise.group(2)) == (lang in i18n.RTL)
    for interdit in ("<script", "{{", "<!--@", "@import", "url("):
        assert interdit not in page, interdit
    # D91 : seuls les liens de paiement Stripe sortent du site (on y va, rien n'en est chargé).
    externes = re.findall(r"https?://[^\s\"'<>]+", page)
    assert all(u.startswith(("https://buy.stripe.com/", "https://billing.stripe.com/")) for u in externes), externes
    assert all('rel="alternate"' in lien for lien in re.findall(r"<link\b[^>]*>", page))
    assert page.count("<h1") == 1


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Base SQLite jetable, compteur de débit remis à zéro, bcrypt au coût minimal."""
    moteur = create_engine(f"sqlite:///{tmp_path / 'clients.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    monkeypatch.setattr(route, "SessionLocal", fabrique)
    monkeypatch.setattr(accounts, "BCRYPT_ROUNDS", 4)
    ratelimit.reset()
    yield fabrique
    ratelimit.reset()


def _inscrit(base, lang: str, **champs) -> tuple[str, str]:
    """Inscription réelle par la route de la langue ; renvoie (email, clé)."""
    forme = _forme(lang, **champs)
    reponse = client.post(_signup(lang), data=forme)
    assert reponse.status_code == 200, reponse.status_code
    return forme["email"], CLE.search(reponse.text).group(0)


# --------------------------------------------------------------------------- #
# Routes, formulaire, refus
# --------------------------------------------------------------------------- #


def test_les_routes_des_langues_sont_litterales():
    routes = {(r.path, m) for r in app.routes if hasattr(r, "methods") for m in r.methods}
    for lang in i18n.LANGUES:
        for chemin, methode in ((_signup(lang), "GET"), (_signup(lang), "POST"), (_compte(lang), "GET"),
                                (_compte(lang) + "/cle", "POST")):
            assert (chemin, methode) in routes, (chemin, methode)
    assert not [chemin for chemin, _ in routes if "{lang" in chemin]


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_le_formulaire_dans_chaque_langue(lang):
    reponse = client.get(_signup(lang) + "?plan=enterprise")
    page, settings = reponse.text, get_settings()

    assert reponse.status_code == 200
    assert reponse.headers["cache-control"] == "public, max-age=300"
    assert "form-action 'self'" in reponse.headers["content-security-policy"]
    assert 'name="robots" content="index, follow"' in page
    _sans_ressource_externe(page, lang)
    # Un seul formulaire, qui poste dans la langue de la page, avec les noms du code (D54 : pas de plan).
    assert re.findall(r'<form\b[^>]*>', page) == [f'<form class="signup" method="post" action="{_signup(lang)}">']
    assert re.findall(r'<(?:input|select|textarea)\b[^>]*\bname="(\w+)"', page) == ["email", "password", "shop_name", "platform"]
    assert re.findall(r'<option value="(\w+)"', page) == list(accounts.PLATEFORMES)
    assert f'minlength="{accounts.PASSWORD_MIN_LENGTH}"' in page
    # Les chiffres sont ceux du code et de la configuration.
    for valeur in (i18n.nombre(lang, settings.trial_days), i18n.nombre(lang, accounts.PLANS["trial"]),
                   i18n.prix(lang, "essai"), i18n.prix(lang, "starter"), i18n.prix(lang, "pro"),
                   i18n.nombre(lang, accounts.PLANS["starter"]), i18n.nombre(lang, accounts.PLANS["pro"]),
                   str(accounts.PASSWORD_MAX_BYTES), str(accounts.API_KEY_CHARS)):
        assert valeur in page, valeur
    assert i18n.mention_euros(lang) in page
    assert seo.hors_bloc(page).count(settings.contact_email) == 2
    # Sélecteur de langue et hreflang : la même page dans chaque langue ; navigation dans la langue.
    assert re.findall(r'<a href="([^"]*)" hreflang="([\w-]+)"', page) == [(_signup(autre), autre) for autre in i18n.LANGUES]
    for page_site in ("pricing", "guide", "validate-form", "compte"):
        assert f'href="{i18n.chemin(lang, page_site)}"' in page


def test_le_francais_garde_les_mots_d_avant():
    page = client.get("/signup").text
    assert "12 caractères minimum" in page
    textes = route.TEXTES["fr"]
    assert textes["mdp_court"].format(min=accounts.PASSWORD_MIN_LENGTH) == accounts.password_problem("court")
    assert textes["mdp_long"].format(max=accounts.PASSWORD_MAX_BYTES) == accounts.password_problem("x" * 73)


def test_chaque_langue_a_les_memes_messages_que_le_francais():
    """Une entrée oubliée, ou un champ `{…}` qui manque, casserait une page dans une seule langue."""

    def champs(valeur):
        if isinstance(valeur, dict):
            return {cle: champs(sous) for cle, sous in valeur.items()}
        if isinstance(valeur, tuple):
            return tuple(champs(sous) for sous in valeur)
        return set(re.findall(r"\{(\w+)\}", valeur))

    reference = champs(route.TEXTES["fr"])
    for lang in i18n.LANGUES:
        assert champs(route.TEXTES[lang]) == reference, lang


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_refus_parlent_la_langue_de_la_page(base, lang):
    textes = route.TEXTES[lang]
    mauvais = {"email": '<b>"x"</b>', "password": "court", "shop_name": "   ", "platform": "prestashop"}
    reponse = client.post(_signup(lang), data=mauvais)
    lu = _lu(reponse)

    assert reponse.status_code == 400
    assert reponse.headers["cache-control"] == "no-store"
    assert 'name="robots" content="noindex, nofollow"' in reponse.text
    for message in (textes["email_invalide"], textes["mdp_court"].format(min=accounts.PASSWORD_MIN_LENGTH),
                    textes["boutique_requise"], textes["plateforme"]):
        assert message in lu, message
    # La saisie revient échappée ; le mot de passe ne revient jamais.
    assert 'value="&lt;b&gt;&quot;x&quot;&lt;/b&gt;"' in reponse.text and "<b>\"x\"</b>" not in reponse.text
    assert 'value="court"' not in reponse.text
    _sans_ressource_externe(reponse.text, lang)

    trop_long = client.post(_signup(lang), data=_forme(lang, password="x" * (accounts.PASSWORD_MAX_BYTES + 1), platform="woocommerce"))
    assert trop_long.status_code == 400
    assert textes["mdp_long"].format(max=accounts.PASSWORD_MAX_BYTES) in _lu(trop_long)
    assert '<option value="woocommerce" selected>' in trop_long.text
    with base() as session:
        assert session.query(Customer).count() == 0


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_409_429_et_503_dans_la_langue(base, lang, monkeypatch):
    textes = route.TEXTES[lang]
    _inscrit(base, lang)
    doublon = client.post(_signup(lang), data=_forme(lang, shop_name="Autre"))
    assert doublon.status_code == 409 and doublon.headers["cache-control"] == "no-store"
    assert textes["email_pris"].format(compte=_compte(lang)) in _lu(doublon)

    monkeypatch.setattr(get_settings(), "signup_rate_limit_per_hour", 2)
    bloque = client.post(_signup(lang), data=_forme(lang, email=f"trop-{lang}@example.re"))
    assert bloque.status_code == 429
    assert bloque.headers["retry-after"] == str(route.FENETRE_INSCRIPTION)
    assert bloque.headers["cache-control"] == "no-store"
    assert textes["trop"][0].format(limite=2) in _lu(bloque)

    ratelimit.reset()

    def _panne():
        raise OperationalError("SELECT 1", {}, Exception("panne"))

    monkeypatch.setattr(route, "SessionLocal", _panne)
    panne = client.post(_signup(lang), data=_forme(lang, email=f"panne-{lang}@example.re"))
    assert panne.status_code == 503 and panne.headers["cache-control"] == "no-store"
    assert textes["indisponible"][0] in _lu(panne)
    assert f'value="panne-{lang}@example.re"' in panne.text  # la saisie n'est pas perdue
    espace = client.get(_compte(lang), headers=_auth("x@example.re"))
    assert espace.status_code == 503 and textes["indisponible"][0] in _lu(espace)


def test_le_plafond_vaut_pour_toutes_les_langues(base, monkeypatch):
    monkeypatch.setattr(get_settings(), "signup_rate_limit_per_hour", 1)
    assert client.post("/signup", data=_forme("fr")).status_code == 200
    # Changer de langue ne rouvre pas la fenêtre.
    assert client.post("/en/signup", data=_forme("en")).status_code == 429


# --------------------------------------------------------------------------- #
# La page de la clé
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_la_cle_est_montree_une_fois_dans_la_langue(base, lang, caplog):
    textes = route.TEXTES[lang]
    reponse = client.post(_signup(lang), data=_forme(lang, shop_name='<i>Kder & "Co"</i>', plan="enterprise"))
    page, lu = reponse.text, _lu(reponse)
    cle = CLE.search(page).group(0)

    assert reponse.status_code == 200
    assert reponse.headers["cache-control"] == "no-store"
    assert 'name="robots" content="noindex, nofollow"' in page
    _sans_ressource_externe(page, lang)
    # La vraie clé : dans le coffre (sélectionnable d'un clic) et dans l'en-tête du premier appel.
    prefixe_cle, secret = cle[: len(accounts.API_KEY_PREFIX)], cle[len(accounts.API_KEY_PREFIX):]
    assert f'<span class="kp">{prefixe_cle}</span><wbr><span class="ks">{secret}</span>' in page
    assert f'X-API-Key</span>: <span class="key-l">{cle}</span>' in page
    assert textes["cree"]["h1"] in lu and textes["cree"]["intro"] in lu
    # Les vraies valeurs du compte, échappées.
    assert "&lt;i&gt;Kder &amp; &quot;Co&quot;&lt;/i&gt;" in page and "<i>Kder" not in page
    with base() as session:
        compte = accounts.find_by_email(session, _forme(lang)["email"])
        assert compte.api_key_hash == accounts.api_key_hash(cle) and cle not in compte.api_key_hash
        assert compte.plan == accounts.PLAN_PAR_DEFAUT  # D54, même avec plan=enterprise posté
        fin = compte.trial_ends_at.replace(tzinfo=timezone.utc)
        assert f"{compte.api_key_prefix}…" in page
    empreinte = accounts.api_key_hash(cle)
    assert f"{empreinte[:8]}…{empreinte[-8:]}" in page
    assert textes["plans"]["trial"] in lu
    assert f'<dd class="big">{i18n.nombre(lang, accounts.PLANS["trial"])}</dd>' in page
    assert route._date(lang, fin, "longue") in lu
    assert '<div class="trial"' in page  # l'essai court : la frise aujourd'hui → fin
    assert i18n.mention_euros(lang) in page
    # Les liens mènent au guide et à l'espace client de la langue.
    assert f'href="{i18n.chemin(lang, "guide")}"' in page and f'href="{_compte(lang)}"' in page
    # Jamais journalisée.
    assert cle not in caplog.text


def test_les_dates_s_ecrivent_comme_dans_les_maquettes():
    jour = datetime(2026, 11, 12, tzinfo=timezone.utc)
    attendu = {
        "fr": ("12/11/2026", "12 nov.", "12/11", "novembre 2026"),
        "en": ("11/12/2026", "Nov 12", "11/12", "November 2026"),
        "zh": ("2026 年 11 月 12 日", "11 月 12 日", "11 月 12 日", "2026 年 11 月"),
        "hi": ("12/11/2026", "12 नव.", "12/11", "नवंबर 2026"),
        "es": ("12/11/2026", "12 nov.", "12/11", "noviembre de 2026"),
        "ar": ("12/11/2026", "12 نوفمبر", "12/11", "نوفمبر 2026"),
    }
    for lang, formes in attendu.items():
        assert tuple(route._date(lang, jour, forme) for forme in ("longue", "courte", "chiffres", "mois")) == formes


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_le_montant_suit_la_table_des_prix(lang):
    devise = i18n.DEVISE[lang]
    assert route._montant(lang, i18n.MONTANTS[devise]["colis_rate"]) == i18n.prix(lang, "colis_rate")
    if devise == "USD":
        # La base en dollars reste celle d'accounts (COUT_ECHEC_USD).
        assert route._montant(lang, 3 * i18n.MONTANTS["USD"]["colis_rate"]) == f"${accounts.savings_usd(3):g}"


# --------------------------------------------------------------------------- #
# L'espace client
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_l_espace_client_est_ferme_par_defaut(base, lang):
    textes = route.TEXTES[lang]
    email, _cle = _inscrit(base, lang)
    # D62 : sans session, la page de connexion de la langue, jamais le défi Basic.
    anonyme = client.get(_compte(lang), follow_redirects=False)
    assert anonyme.status_code == 303 and anonyme.headers["location"] == _compte(lang) + "/connexion"
    assert "www-authenticate" not in anonyme.headers and anonyme.headers["cache-control"] == "no-store"
    # Un script qui envoie Basic de lui-même garde ses réponses.
    for entetes, message in (({"Authorization": "Basic !!"}, textes["auth_requise"]),
                             (_auth(email, "mauvais"), textes["auth_refus"]),
                             (_auth("inconnu@example.re"), textes["auth_refus"])):
        refus = client.get(_compte(lang), headers=entetes)
        assert refus.status_code == 401 and refus.text == message
        assert refus.headers["www-authenticate"].lower().startswith("basic")
        assert refus.headers["cache-control"] == "no-store"
    assert client.post(_compte(lang) + "/cle", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_l_espace_client_montre_les_vraies_valeurs(base, lang):
    textes = route.TEXTES[lang]
    email, cle = _inscrit(base, lang, shop_name="Boutique <b>")
    with base() as session:
        compte = accounts.find_by_email(session, email)
        for issue in (True, True, True, False, False):
            accounts.record_validation(session, compte.id, "cle-de-cache", issue)
        prefixe = compte.api_key_prefix
    reponse = client.get(_compte(lang), headers=_auth(email))
    page, lu = reponse.text, _lu(reponse)
    nombre = lambda valeur: i18n.nombre(lang, valeur)  # noqa: E731

    assert reponse.status_code == 200 and reponse.headers["cache-control"] == "no-store"
    assert 'name="robots" content="noindex, nofollow"' in page
    _sans_ressource_externe(page, lang)
    assert "Boutique &lt;b&gt;" in page and email in page
    # Le préfixe seul, jamais la clé ; la régénération poste dans la langue de la page.
    assert f"{prefixe}…" in page and cle not in page and not CLE.search(page)
    assert f'<form method="post" action="{_compte(lang)}/cle">' in page
    # D64 : le mois en tête (compte réel, quota du plan), le total depuis la création, la formule.
    tab = tableau.TEXTES[lang]
    quota = accounts.PLANS["trial"]
    assert f"\u2066{nombre(quota - 5)} / {nombre(quota)}\u2069" in page
    assert tab["depuis"].format(v=nombre(5), i=nombre(2)) in lu
    assert route._pluriel(lang, 2, tab["titre_inv"]).format(n=nombre(2)) in lu
    assert route._date(lang, accounts.month_start(), "courte") in lu
    cout = i18n.prix(lang, "colis_rate")
    total = route._montant(lang, 2 * i18n.MONTANTS[i18n.DEVISE[lang]]["colis_rate"])
    assert textes["calcul"][1].format(n=nombre(2), cout=cout, total=total) in lu
    assert f">{total}<" in lu and f">{cout}<" in lu
    # Essai en cours : pas de bandeau d'état, la prochaine étape parle de l'essai.
    assert 'class="state ' not in page and tab["prochaine"] in lu
    assert _bloc(lang, "compte", "fact_essai") in page and _bloc(lang, "compte", "fact_payant") not in page
    assert i18n.mention_euros(lang) in page
    assert re.findall(r'<a href="([^"]*)" hreflang="([\w-]+)"', page) == [(_compte(autre), autre) for autre in i18n.LANGUES]


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_bandeaux_sont_ceux_que_le_code_produit(base, lang):
    textes = route.TEXTES[lang]
    email, cle = _inscrit(base, lang)
    veille = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)

    def espace():
        return client.get(_compte(lang), headers=_auth(email))

    # Quota du mois consommé : 402.
    with base() as session:
        compte = accounts.find_by_email(session, email)
        session.add_all([CustomerValidation(customer_id=compte.id, cache_key="q", validated=True)
                         for _ in range(accounts.PLANS["trial"])])
        session.commit()
        assert accounts.refusal_reason(session, compte) == accounts.REFUS_QUOTA_ATTEINT
    page = espace().text
    assert 'class="state s-quota"' in page and f'"reason": "{accounts.REFUS_QUOTA_ATTEINT}"' in unescape(page)
    assert textes["etats"]["quota"]["titre"] in unescape(page)

    # Essai terminé (et quota atteint) : c'est l'essai qui parle, comme refusal_reason : 403.
    with base() as session:
        compte = accounts.find_by_email(session, email)
        compte.trial_ends_at = veille
        session.commit()
        assert accounts.refusal_reason(session, compte) == accounts.REFUS_COMPTE_EXPIRE
    page = espace().text
    lu = unescape(page)
    assert 'class="state s-expired"' in page and 'class="state s-quota"' not in page
    assert textes["etats"]["expire"]["titre"] in lu and i18n.prix(lang, "starter") in lu

    # Plan payant accordé à la main, sans date d'essai : servi, et dit comme tel.
    with base() as session:
        compte = accounts.find_by_email(session, email)
        compte.plan, compte.trial_ends_at = "starter", None
        session.commit()
        assert accounts.refusal_reason(session, compte) is None
    page = espace().text
    lu = unescape(page)
    assert 'class="state s-paid"' in page and '<span class="code">Starter</span>' in page
    assert textes["essai_aucun"] in lu
    assert _bloc(lang, "compte", "fact_payant") in page and _bloc(lang, "compte", "fact_essai") not in page
    assert tableau.TEXTES[lang]["offre_mois"].format(quota=i18n.nombre(lang, accounts.PLANS["starter"])) in lu


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_la_regeneration_montre_la_nouvelle_cle_dans_la_langue(base, lang):
    textes = route.TEXTES[lang]
    email, ancienne = _inscrit(base, lang)
    reponse = client.post(_compte(lang) + "/cle", headers=_auth(email))
    lu = _lu(reponse)

    assert reponse.status_code == 200 and reponse.headers["cache-control"] == "no-store"
    _sans_ressource_externe(reponse.text, lang)
    assert textes["regeneree"]["h1"] in lu and textes["regeneree"]["intro"] in lu
    nouvelle = CLE.search(reponse.text).group(0)
    assert nouvelle != ancienne and ancienne not in reponse.text
    with base() as session:
        assert accounts.find_by_api_key(session, ancienne) is None
        assert accounts.find_by_api_key(session, nouvelle) is not None


def test_aucune_cle_ni_ressource_dans_les_gabarits():
    for dossier in DOSSIERS:
        for lang in i18n.LANGUES:
            gabarit = (GABARITS / dossier / f"{lang}.html").read_text(encoding="utf-8")
            assert not CLE.search(gabarit), (dossier, lang)
            assert not re.search(r"https?://|<script", gabarit), (dossier, lang)
