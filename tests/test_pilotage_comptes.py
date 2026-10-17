"""Tests des comptes du tableau de bord (décision D74).

Ce qui est mesuré, et pourquoi :

  * seule une adresse de `PILOTAGE_COMPTES` reçoit un lien ; la page répond pareil pour une
    adresse inconnue (elle ne dit pas qui a un compte) et n'envoie rien ;
  * le lien est bâti sur `PUBLIC_BASE_URL`, vaut 30 minutes, ne sert qu'une fois ; la base n'en
    garde que l'empreinte ;
  * le mot de passe choisi suit les règles des clients et n'est gardé qu'en empreinte bcrypt ;
  * l'email et ce mot de passe ouvrent une session nominative (« Bonjour Fzed ») ; changer de
    mot de passe ferme les sessions d'avant ; une adresse retirée de la liste ne rentre plus ;
  * l'identifiant partagé marche toujours pendant la transition.
"""

import re
import tempfile
from datetime import timedelta
from html import unescape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import pilotage_comptes
from app.config import get_settings
from app.main import app
from app.models import Operator, OperatorToken
from app.routes import dashboard, mot_de_passe

client = TestClient(app)
CHROME = {"Origin": "null", "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def base(monkeypatch):
    moteur = create_engine(f"sqlite:///{Path(tempfile.mkdtemp()) / 'op.sqlite'}")
    Operator.__table__.create(moteur)
    OperatorToken.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    monkeypatch.setattr(dashboard, "_session", fabrique)
    reglages = get_settings()
    monkeypatch.setattr(reglages, "pilotage_comptes", "Fzed=fzed@exemple.re, Kder=Kder@Exemple.re")
    monkeypatch.setattr(reglages, "dashboard_password", "")
    envoyes = []
    monkeypatch.setattr(mot_de_passe, "envoyer_mail", lambda dest, sujet, corps: envoyes.append((dest, sujet, corps)))
    return fabrique, envoyes


def _ip(n):
    return {"X-Forwarded-For": f"198.19.0.{n}"}


def _demander(email, n):
    return client.post("/dashboard/mot-de-passe", data={"email": email}, headers={**CHROME, **_ip(n)})


def _lien(corps):
    return re.search(r"(https?://\S+/dashboard/nouveau-mot-de-passe\?jeton=(\S+))", corps).groups()


def test_les_adresses_autorisees():
    reglages = get_settings().model_copy(update={"pilotage_comptes": "Fzed=fzed@exemple.re, Kder = KDER@exemple.re ,pas-une-adresse, =x@y.re"})
    assert pilotage_comptes.autorises(reglages) == {"fzed@exemple.re": "Fzed", "kder@exemple.re": "Kder"}


def test_une_adresse_inconnue_recoit_la_meme_reponse_et_rien(base):
    _fabrique, envoyes = base
    inconnue, connue = _demander("intrus@exemple.re", 1), _demander("fzed@exemple.re", 2)
    assert inconnue.status_code == connue.status_code == 200
    assert unescape(inconnue.text).count("Si cette adresse a un compte") == 1 == unescape(connue.text).count("Si cette adresse a un compte")
    assert [dest for dest, _s, _c in envoyes] == ["fzed@exemple.re"]


def test_le_parcours_complet_jusqu_a_bonjour_fzed(base):
    fabrique, envoyes = base
    _demander("FZED@exemple.re", 3)
    lien, jeton = _lien(envoyes[0][2])
    assert lien.startswith(get_settings().public_base_url.rstrip("/")) and "30 minutes" in envoyes[0][2]
    with fabrique() as s:
        ligne = s.scalars(select(OperatorToken)).one()
        assert ligne.token_hash != jeton and len(ligne.token_hash) == 64
    page = client.get(f"/dashboard/nouveau-mot-de-passe?jeton={jeton}")
    assert page.status_code == 200 and "Bonjour Fzed" in page.text and 'type="password"' in page.text
    court = client.post("/dashboard/nouveau-mot-de-passe", data={"jeton": jeton, "password": "court", "confirmation": "court"}, headers=CHROME)
    assert court.status_code == 400 and "au moins" in unescape(court.text)
    differents = client.post("/dashboard/nouveau-mot-de-passe", data={"jeton": jeton, "password": "un-long-mot-de-passe", "confirmation": "un-autre-mot-de-passe"}, headers=CHROME)
    assert differents.status_code == 400 and "pas identiques" in unescape(differents.text)
    bon = client.post("/dashboard/nouveau-mot-de-passe", data={"jeton": jeton, "password": "un-long-mot-de-passe", "confirmation": "un-long-mot-de-passe"}, headers=CHROME, follow_redirects=False)
    assert bon.status_code == 303 and "__Host-av_pilotage=" in bon.headers["set-cookie"]
    with fabrique() as s:
        compte = s.scalars(select(Operator)).one()
        assert compte.password_hash.startswith("$2") and "un-long-mot-de-passe" not in compte.password_hash
        assert s.scalars(select(OperatorToken)).all() == []
    # Le lien ne resert pas.
    assert client.get(f"/dashboard/nouveau-mot-de-passe?jeton={jeton}").status_code == 410
    # Connexion par email : session nominative.
    connexion = client.post("/dashboard/connexion", data={"user": "fzed@exemple.re", "password": "un-long-mot-de-passe"}, headers={**CHROME, **_ip(4)}, follow_redirects=False)
    assert connexion.status_code == 303
    jeton_session = connexion.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    tableau = client.get("/dashboard", headers={"Accept": "text/html", **_ip(4), "Cookie": f"{dashboard.COOKIE}={jeton_session}"})
    assert tableau.status_code == 200 and "Bonjour Fzed, voici" in unescape(tableau.text)
    # Un mauvais mot de passe ne rentre pas.
    assert client.post("/dashboard/connexion", data={"user": "fzed@exemple.re", "password": "pas-le-bon-mot-de-passe"}, headers={**CHROME, **_ip(5)}).status_code == 401


def test_changer_de_mot_de_passe_ferme_les_sessions_et_le_retrait_de_la_liste_aussi(base, monkeypatch):
    fabrique, envoyes = base
    reglages = get_settings()
    _demander("kder@exemple.re", 6)
    _lien_, jeton = _lien(envoyes[-1][2])
    with fabrique() as s:
        compte = pilotage_comptes.definir_mot_de_passe(s, jeton, "premier-mot-de-passe")
        ancienne = dashboard.jeton_session(reglages, operateur=compte)
    assert dashboard.session_qui(ancienne, reglages) == "Kder"
    with fabrique() as s:
        jeton2, _nom = pilotage_comptes.creer_lien(s, "kder@exemple.re")
        pilotage_comptes.definir_mot_de_passe(s, jeton2, "second-mot-de-passe")
    assert dashboard.session_qui(ancienne, reglages) is None
    with fabrique() as s:
        compte = s.scalars(select(Operator)).one()
        nouvelle = dashboard.jeton_session(reglages, operateur=compte)
    monkeypatch.setattr(reglages, "pilotage_comptes", "Fzed=fzed@exemple.re")
    assert dashboard.session_qui(nouvelle, reglages) is None
    with fabrique() as s:
        assert pilotage_comptes.authentifier(s, "kder@exemple.re", "second-mot-de-passe") is None


def test_lien_echu_et_trois_liens_par_heure(base):
    fabrique, _envoyes = base
    with fabrique() as s:
        jeton, _nom = pilotage_comptes.creer_lien(s, "fzed@exemple.re")
        assert pilotage_comptes.operateur_du_lien(s, jeton, pilotage_comptes.maintenant() + timedelta(minutes=31)) is None
        for _ in range(2):
            assert pilotage_comptes.creer_lien(s, "fzed@exemple.re") is not None
        assert pilotage_comptes.creer_lien(s, "fzed@exemple.re") is None


def test_l_identifiant_partage_marche_toujours(base, monkeypatch):
    reglages = get_settings()
    monkeypatch.setattr(reglages, "dashboard_user", "kder")
    monkeypatch.setattr(reglages, "dashboard_password", "code-partage-de-transition")
    reponse = client.post("/dashboard/connexion", data={"user": "kder", "password": "code-partage-de-transition"}, headers={**CHROME, **_ip(7)}, follow_redirects=False)
    assert reponse.status_code == 303
    page = client.get("/dashboard/connexion")
    assert "Créer ou changer mon mot de passe" in page.text and 'name="user"' in page.text
