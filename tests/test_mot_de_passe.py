"""Tests du mot de passe oublié (décision D63).

Ce qui est mesuré, et pourquoi :

  * la demande répond **mot pour mot pareil** que l'adresse ait un compte ou non :
    la page ne sert pas à savoir qui est client ;
  * le lien part par mail, fabriqué avec `public_base_url` même si la requête
    porte un `Host` falsifié ; la base ne garde que l'empreinte du jeton, et le
    jeton n'apparaît dans aucun journal ;
  * le lien vaut trente minutes et une seule fois ; le nouveau mot de passe ferme
    toutes les sessions, l'ancien mot de passe ne marche plus ;
  * les plafonds tiennent : trois mails par compte et par heure, cinq demandes
    par IP et par heure ; un formulaire d'un autre site est refusé ;
  * l'envoi passe par le SMTP configuré, en STARTTLS ; sans SMTP, rien ne casse.

Base SQLite jetable, Redis absent, envoi de mail remplacé par un relevé.
"""

import logging
import re
from html import unescape
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import accounts, i18n, ratelimit
from app.config import get_settings
from app.main import app
from app.models import Customer, CustomerSession, CustomerValidation, PasswordReset
from app.routes import connexion as connexion_route
from app.routes import mot_de_passe as route
from app.routes import signup as signup_route

MOT_DE_PASSE = "un-mot-de-passe-solide-12"
NOUVEAU = "une-toute-nouvelle-phrase-secrete"
EMAIL = "boutique@example.re"


@pytest.fixture
def base(tmp_path, monkeypatch):
    moteur = create_engine(f"sqlite:///{tmp_path / 'clients.sqlite'}")
    for modele in (Customer, CustomerValidation, CustomerSession, PasswordReset):
        modele.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    for module in (signup_route, connexion_route, route):
        monkeypatch.setattr(module, "SessionLocal", fabrique)
    ratelimit.reset()
    yield fabrique
    ratelimit.reset()


@pytest.fixture
def mails(monkeypatch):
    """Relevé des mails « envoyés » : (destinataire, sujet, corps)."""
    envoyes: list[tuple[str, str, str]] = []
    monkeypatch.setattr(route, "envoyer_mail", lambda *mail: envoyes.append(mail))
    return envoyes


@pytest.fixture
def client(base):
    navigateur = TestClient(app, base_url="https://testserver")
    reponse = navigateur.post(
        "/signup", data={"email": EMAIL, "password": MOT_DE_PASSE, "shop_name": "Boutique Test", "platform": "shopify"}
    )
    assert reponse.status_code == 200
    return navigateur


def _jeton(mail) -> str:
    return re.search(r"\?jeton=([\w-]+)", mail[2]).group(1)


@pytest.mark.parametrize("lang", i18n.LANGUES)
def test_les_pages_sont_privees_et_dans_leur_langue(client, lang):
    for chemin in (route.chemin_oubli(lang), route.chemin_nouveau(lang) + "?jeton=faux"):
        page = client.get(chemin)
        assert page.headers["cache-control"] == "no-store"
        assert 'name="robots" content="noindex, nofollow"' in page.text
        balise = re.search(r'<html lang="([\w-]+)"( dir="rtl")?>', page.text)
        assert balise.group(1).split("-")[0] == lang and bool(balise.group(2)) == (lang in i18n.RTL)
        for interdit in ("<script", "{{", "<!--@"):
            assert interdit not in page.text
        assert not re.search(r"https?://", page.text)
    oubli = client.get(route.chemin_oubli(lang))
    assert oubli.status_code == 200 and f'method="post" action="{route.chemin_oubli(lang)}"' in oubli.text
    assert f'href="{route.chemin_oubli(lang)}"' in client.get(connexion_route.chemin_connexion(lang)).text


def test_meme_reponse_que_le_compte_existe_ou_non(client, mails):
    existe = client.post("/compte/mot-de-passe-oublie", data={"email": EMAIL})
    inconnu = client.post("/compte/mot-de-passe-oublie", data={"email": "inconnu@example.re"})
    assert existe.status_code == inconnu.status_code == 200
    assert existe.text == inconnu.text
    assert "C'est envoyé." in unescape(existe.text)
    assert [mail[0] for mail in mails] == [EMAIL]


def test_le_lien_part_avec_l_adresse_officielle_et_seule_l_empreinte_est_gardee(client, base, mails, caplog):
    with caplog.at_level(logging.DEBUG):
        client.post("/en/compte/mot-de-passe-oublie", data={"email": EMAIL}, headers={"Host": "pirate.example"})
    destinataire, sujet, corps = mails[0]
    assert destinataire == EMAIL and sujet == route.TEXTES["en"]["sujet"]
    attendu = get_settings().public_base_url.rstrip("/") + "/en/compte/nouveau-mot-de-passe?jeton="
    assert attendu in corps and "pirate.example" not in corps
    assert f"{get_settings().password_reset_minutes} minutes" in corps
    jeton = _jeton(mails[0])
    assert jeton not in caplog.text
    with base() as session:
        assert [ligne.token_hash for ligne in session.query(PasswordReset).all()] == [accounts._empreinte_jeton(jeton)]


def test_nouveau_mot_de_passe_ferme_tout_et_ne_sert_qu_une_fois(client, base, mails):
    # Une session ouverte avant : elle doit tomber.
    avant = TestClient(app, base_url="https://testserver")
    assert avant.post("/compte/connexion", data={"email": EMAIL, "password": MOT_DE_PASSE}, follow_redirects=False).status_code == 303

    client.post("/compte/mot-de-passe-oublie", data={"email": EMAIL})
    jeton = _jeton(mails[0])
    page = client.get(f"/compte/nouveau-mot-de-passe?jeton={jeton}")
    assert page.status_code == 200 and EMAIL in page.text and f'value="{jeton}"' in page.text

    # Saisies refusées : le lien reste bon.
    differents = client.post("/compte/nouveau-mot-de-passe", data={"jeton": jeton, "password": NOUVEAU, "password2": NOUVEAU + "x"})
    assert differents.status_code == 400 and "ne sont pas identiques" in differents.text
    court = client.post("/compte/nouveau-mot-de-passe", data={"jeton": jeton, "password": "court", "password2": "court"})
    assert court.status_code == 400 and "au moins" in court.text

    fait = client.post(
        "/compte/nouveau-mot-de-passe", data={"jeton": jeton, "password": NOUVEAU, "password2": NOUVEAU}, follow_redirects=False
    )
    assert fait.status_code == 303 and fait.headers["location"] == "/compte"
    assert "__Host-av_session=" in fait.headers["set-cookie"]
    assert client.get("/compte").status_code == 200

    # L'ancienne session est fermée, l'ancien mot de passe ne marche plus, le lien est usé.
    assert avant.get("/compte", follow_redirects=False).status_code == 303
    neuf = TestClient(app, base_url="https://testserver")
    assert neuf.post("/compte/connexion", data={"email": EMAIL, "password": MOT_DE_PASSE}, follow_redirects=False).status_code == 400
    assert neuf.post("/compte/connexion", data={"email": EMAIL, "password": NOUVEAU}, follow_redirects=False).status_code == 303
    reutilise = client.post("/compte/nouveau-mot-de-passe", data={"jeton": jeton, "password": NOUVEAU, "password2": NOUVEAU})
    assert reutilise.status_code == 400 and 'name="password"' not in reutilise.text


def test_un_lien_echu_ne_vaut_plus_rien(client, base, mails):
    client.post("/compte/mot-de-passe-oublie", data={"email": EMAIL})
    with base() as session:
        ligne = session.query(PasswordReset).one()
        ligne.expires_at = ligne.expires_at - timedelta(minutes=31)
        session.commit()
    perime = client.get(f"/compte/nouveau-mot-de-passe?jeton={_jeton(mails[0])}")
    assert perime.status_code == 400 and 'name="password"' not in perime.text
    assert 'href="/compte/mot-de-passe-oublie"' in perime.text


def test_les_plafonds_tiennent(client, mails):
    for _ in range(4):
        assert client.post("/compte/mot-de-passe-oublie", data={"email": EMAIL}).status_code == 200
    assert len(mails) == route.PAR_COMPTE, "plus de trois mails par compte et par heure"
    assert client.post("/compte/mot-de-passe-oublie", data={"email": "autre@example.re"}).status_code == 200
    trop = client.post("/compte/mot-de-passe-oublie", data={"email": EMAIL})
    assert trop.status_code == 429 and int(trop.headers["retry-after"]) > 0


def test_un_formulaire_d_un_autre_site_est_refuse(client, mails):
    refus = client.post("/compte/mot-de-passe-oublie", data={"email": EMAIL}, headers={"Origin": "https://pirate.example"})
    assert refus.status_code == 403 and mails == []


def test_l_envoi_passe_par_le_smtp_en_starttls(monkeypatch):
    journal = []

    class FauxSMTP:
        def __init__(self, hote, port, timeout):
            journal.append(("connexion", hote, port))

        def starttls(self, context):
            journal.append(("starttls",))

        def login(self, utilisateur, secret):
            journal.append(("login", utilisateur))

        def send_message(self, message):
            journal.append(("envoi", message["From"], message["To"], message["Subject"]))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    reglages = get_settings().model_copy(update={"smtp_host": "smtp.example", "smtp_user": "u", "smtp_password": "p", "smtp_from": ""})
    monkeypatch.setattr(route, "get_settings", lambda: reglages)
    monkeypatch.setattr(route.smtplib, "SMTP", FauxSMTP)
    route.envoyer_mail(EMAIL, "Sujet", "Corps")
    assert journal == [("connexion", "smtp.example", 587), ("starttls",), ("login", "u"), ("envoi", reglages.contact_email, EMAIL, "Sujet")]

    # Sans SMTP : aucun envoi, aucune exception.
    monkeypatch.setattr(route, "get_settings", lambda: get_settings().model_copy(update={"smtp_host": ""}))
    route.envoyer_mail(EMAIL, "Sujet", "Corps")
    assert len(journal) == 4
