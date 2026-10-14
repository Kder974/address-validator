"""Tests de la page de connexion et de la session de l'espace client (décision D62).

Ce qui est mesuré, et pourquoi :

  * sans session, `/compte` renvoie vers la page de connexion de sa langue :
    plus jamais la fenêtre grise du défi Basic pour un navigateur ;
  * la page de connexion est privée (`no-store`, `noindex`), sans script, et
    écrit les trois réglages qu'elle promet (durée de session, échecs, blocage)
    tels que la configuration les applique ;
  * le cookie ne porte qu'un jeton aléatoire, avec `__Host-`, `Secure`,
    `HttpOnly`, `SameSite=Lax` ; la base n'en garde que l'empreinte ;
  * cinq mots de passe faux bloquent la connexion quinze minutes, par IP **et**
    par compte, même avec le bon mot de passe ensuite ;
  * une session échue ou fermée ne vaut plus rien, même si le cookie revient ;
  * un formulaire posté depuis un autre site est refusé ;
  * régénérer la clé depuis une session redemande le mot de passe.

Base SQLite jetable, Redis absent : le repli local d'`app/ratelimit.py` compte.
"""

import base64
import hashlib
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import accounts, i18n, ratelimit
from app.config import get_settings
from app.main import app
from app.models import Customer, CustomerSession, CustomerValidation
from app.routes import connexion as connexion_route
from app.routes import signup as signup_route

MOT_DE_PASSE = "un-mot-de-passe-solide-12"
EMAIL = "boutique@example.re"


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Base SQLite jetable, branchée sur les deux modules, et plafonds remis à zéro."""
    moteur = create_engine(f"sqlite:///{tmp_path / 'clients.sqlite'}")
    for modele in (Customer, CustomerValidation, CustomerSession):
        modele.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    monkeypatch.setattr(signup_route, "SessionLocal", fabrique)
    monkeypatch.setattr(connexion_route, "SessionLocal", fabrique)
    ratelimit.reset()
    yield fabrique
    ratelimit.reset()


@pytest.fixture
def client(base):
    """Un navigateur neuf (cookies vides), en HTTPS : le cookie `Secure` y circule."""
    navigateur = TestClient(app, base_url="https://testserver")
    reponse = navigateur.post(
        "/signup",
        data={"email": EMAIL, "password": MOT_DE_PASSE, "shop_name": "Boutique Test", "platform": "shopify"},
    )
    assert reponse.status_code == 200
    navigateur.cle = re.search(r"sk_live_[A-Za-z0-9]{32}", reponse.text).group(0)
    return navigateur


def _connexion(lang: str) -> str:
    return i18n.chemin(lang, "compte") + "/connexion"


def _connecter(navigateur, lang="fr", email=EMAIL, mot_de_passe=MOT_DE_PASSE, **entetes):
    return navigateur.post(
        _connexion(lang), data={"email": email, "password": mot_de_passe}, headers=entetes, follow_redirects=False
    )


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_sans_session_la_page_de_connexion_de_la_langue(client, lang):
    anonyme = client.get(i18n.chemin(lang, "compte"), follow_redirects=False)
    assert anonyme.status_code == 303 and anonyme.headers["location"] == _connexion(lang)
    assert "www-authenticate" not in anonyme.headers

    page = client.get(_connexion(lang))
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert 'name="robots" content="noindex, nofollow"' in page.text
    balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', page.text)
    assert balise.group(1).split("-")[0] == lang and bool(balise.group(2)) == (lang in i18n.RTL)
    assert f'method="post" action="{_connexion(lang)}"' in page.text
    assert re.findall(r'<input\b[^>]*\bname="(\w+)"', page.text) == ["email", "password"]
    for interdit in ("<script", "{{", "<!--@"):
        assert interdit not in page.text
    assert not re.search(r"https?://", page.text)


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_la_page_ecrit_les_reglages_appliques(base, monkeypatch, lang):
    """D48 : changer la configuration change la page ; rien n'est écrit en dur."""
    reglages = get_settings().model_copy(update={"session_hours": 7, "login_max_failures": 9, "login_lock_minutes": 23})
    monkeypatch.setattr(connexion_route, "get_settings", lambda: reglages)
    texte = TestClient(app, base_url="https://testserver").get(_connexion(lang)).text
    for valeur in (7, 9, 23):
        assert re.search(rf"(?<![\d.,]){valeur}(?![\d.,])", texte), valeur
    # Et le gabarit n'écrit pas lui-même les valeurs par défaut (12 h, 15 min).
    brut = re.sub(r"<style>.*?</style>|<svg\b.*?</svg>", "", (connexion_route.GABARITS / f"{lang}.html").read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"(?<![\d.,#:])(12|15)(?![\d.,%])", brut)


def test_connexion_pose_un_cookie_sur_et_ouvre_l_espace(client, base):
    reponse = _connecter(client)
    assert reponse.status_code == 303 and reponse.headers["location"] == "/compte"
    cookie = reponse.headers["set-cookie"]
    jeton = re.match(r"__Host-av_session=([^;]+);", cookie).group(1)
    for attribut in ("HttpOnly", "Secure", "Path=/", "SameSite=lax", f"Max-Age={12 * 3600}"):
        assert attribut.lower() in cookie.lower(), attribut
    assert "domain=" not in cookie.lower()

    # La base garde l'empreinte, jamais le jeton.
    with base() as session:
        lignes = session.query(CustomerSession).all()
        assert [ligne.token_hash for ligne in lignes] == [hashlib.sha256(jeton.encode()).hexdigest()]

    espace = client.get("/compte")
    assert espace.status_code == 200 and espace.headers["cache-control"] == "no-store"
    assert "Boutique Test" in espace.text and client.cle not in espace.text and jeton not in espace.text
    assert 'action="/compte/deconnexion"' in espace.text
    # Déjà connecté : la page de connexion renvoie vers l'espace.
    assert client.get("/compte/connexion", follow_redirects=False).headers["location"] == "/compte"


def test_meme_refus_pour_email_inconnu_et_mauvais_mot_de_passe(client):
    faux = _connecter(client, mot_de_passe="mauvais-mot-de-passe")
    inconnu = _connecter(client, email="inconnu@example.re")
    for refus in (faux, inconnu):
        assert refus.status_code == 400 and "set-cookie" not in refus.headers
        assert "Email ou mot de passe incorrect." in refus.text
    # La saisie est réaffichée, échappée.
    piege = _connecter(client, email='"><script>x</script>@a.re')
    assert "<script>x" not in piege.text and "&lt;script&gt;" in piege.text


def test_cinq_echecs_bloquent_meme_le_bon_mot_de_passe(client):
    for _ in range(5):
        assert _connecter(client, mot_de_passe="mauvais-mot-de-passe").status_code == 400
    bloque = _connecter(client)
    assert bloque.status_code == 429 and "set-cookie" not in bloque.headers
    assert 0 < int(bloque.headers["retry-after"]) <= 15 * 60
    assert "Réessayez dans" in bloque.text
    # Le compte reste bloqué depuis une autre adresse IP…
    assert _connecter(client, **{"X-Forwarded-For": "198.51.100.9"}).status_code == 429
    # … et l'adresse IP reste bloquée pour un autre compte.
    assert _connecter(client, email="autre@example.re").status_code == 429


def test_le_blocage_vaut_aussi_pour_basic(client):
    jeton = base64.b64encode(f"{EMAIL}:mauvais".encode()).decode()
    for _ in range(5):
        assert client.get("/compte", headers={"Authorization": f"Basic {jeton}"}).status_code == 401
    bon = base64.b64encode(f"{EMAIL}:{MOT_DE_PASSE}".encode()).decode()
    refus = client.get("/compte", headers={"Authorization": f"Basic {bon}"})
    assert refus.status_code == 429 and int(refus.headers["retry-after"]) > 0


def test_une_session_echue_ne_vaut_plus_rien(client, base):
    _connecter(client)
    with base() as session:
        ligne = session.query(CustomerSession).one()
        ligne.expires_at = ligne.expires_at - timedelta(hours=13)
        session.commit()
    assert client.get("/compte", follow_redirects=False).headers["location"] == "/compte/connexion"
    with base() as session:
        assert session.query(CustomerSession).count() == 0


def test_se_deconnecter_ferme_la_session(client, base):
    jeton = re.match(r"__Host-av_session=([^;]+);", _connecter(client).headers["set-cookie"]).group(1)
    sortie = client.post("/compte/deconnexion", follow_redirects=False)
    assert sortie.status_code == 303 and sortie.headers["location"] == "/compte/connexion"
    assert 'max-age=0' in sortie.headers["set-cookie"].lower()
    with base() as session:
        assert session.query(CustomerSession).count() == 0
    # Le cookie recopié ne rouvre rien.
    copie = TestClient(app, base_url="https://testserver", cookies={connexion_route.COOKIE: jeton})
    assert copie.get("/compte", follow_redirects=False).status_code == 303


def test_un_formulaire_d_un_autre_site_est_refuse(client, base):
    etranger = {"Origin": "https://pirate.example"}
    refus = _connecter(client, **etranger)
    assert refus.status_code == 403 and "set-cookie" not in refus.headers
    assert _connecter(client, Origin="https://testserver").status_code == 303

    cle = client.post("/compte/cle", data={"password": MOT_DE_PASSE}, headers=etranger, follow_redirects=False)
    assert cle.status_code == 403
    sortie = client.post("/compte/deconnexion", headers=etranger, follow_redirects=False)
    assert sortie.status_code == 403
    assert client.get("/compte").status_code == 200, "la session a ete fermee par un autre site"


def test_regenerer_depuis_la_session_redemande_le_mot_de_passe(client, base):
    _connecter(client)
    for mot in ("", "mauvais-mot-de-passe"):
        refus = client.post("/compte/cle", data={"password": mot})
        assert refus.status_code == 400 and "Mot de passe incorrect" in refus.text
        assert not re.search(r"sk_live_[A-Za-z0-9]{32}", refus.text)
    with base() as session:
        assert accounts.find_by_api_key(session, client.cle) is not None

    nouvelle = client.post("/compte/cle", data={"password": MOT_DE_PASSE})
    assert nouvelle.status_code == 200 and nouvelle.headers["cache-control"] == "no-store"
    cle = re.search(r"sk_live_[A-Za-z0-9]{32}", nouvelle.text).group(0)
    with base() as session:
        assert accounts.find_by_api_key(session, client.cle) is None
        assert accounts.find_by_api_key(session, cle) is not None


@pytest.mark.parametrize("lang", i18n.PREFIXEES)
def test_la_connexion_repond_dans_sa_langue(client, lang):
    refus = _connecter(client, lang, mot_de_passe="mauvais-mot-de-passe")
    assert refus.status_code == 400 and connexion_route.TEXTES[lang]["identifiants"] in refus.text
    entree = _connecter(client, lang)
    assert entree.headers["location"] == i18n.chemin(lang, "compte")
    espace = client.get(i18n.chemin(lang, "compte"))
    assert espace.status_code == 200 and f'<html lang="{lang}' in espace.text
    assert f'action="{i18n.chemin(lang, "compte")}/deconnexion"' in espace.text
