"""Tests des routes `/signup` et `/compte` (décision D44).

Ce qui est mesuré, et pourquoi :

  * l'inscription crée un compte et **montre la clé une seule fois** — la page
    est `no-store`, la base ne contient qu'une empreinte, et la clé ne
    réapparaît nulle part ensuite ;
  * un mot de passe faible, un email déjà pris, une plateforme inconnue sont
    refusés **avant** toute écriture ;
  * le plafond par IP (5 / heure) est réellement appliqué et annoncé
    (`Retry-After`) ;
  * `/compte` est fermé par défaut (401 + défi Basic) et dit la même chose pour
    un email inconnu et un mauvais mot de passe ;
  * la base injoignable donne un `503` explicite, pas des zéros décoratifs ;
  * les statistiques affichées viennent des validations réellement servies.

La base est un SQLite jetable branché à la place de PostgreSQL, et Redis absent :
le repli local de `app/ratelimit.py` est donc lui aussi mesuré, en même temps que
la route.
"""

import base64
import re
from html import unescape

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import accounts, ratelimit
from app.main import app
from app.models import Customer, CustomerValidation
from app.routes import signup as signup_route

client = TestClient(app)

MOT_DE_PASSE = "un-mot-de-passe-solide-12"
FORME = {
    "email": "boutique@example.re",
    "password": MOT_DE_PASSE,
    "shop_name": "Boutique Test",
    "platform": "shopify",
    "plan": "trial",
}


def _auth(email: str, mot_de_passe: str) -> dict:
    jeton = base64.b64encode(f"{email}:{mot_de_passe}".encode()).decode()
    return {"Authorization": f"Basic {jeton}"}


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Base SQLite jetable + compteur de débit remis à zéro."""
    moteur = create_engine(f"sqlite:///{tmp_path / 'clients.sqlite'}")
    Customer.__table__.create(moteur)
    CustomerValidation.__table__.create(moteur)
    fabrique = sessionmaker(bind=moteur)
    monkeypatch.setattr(signup_route, "SessionLocal", fabrique)
    ratelimit.reset()
    yield fabrique
    ratelimit.reset()


def test_inscription_montre_la_cle_une_fois(base):
    reponse = client.post("/signup", data=FORME)
    assert reponse.status_code == 200
    assert reponse.headers["cache-control"] == "no-store"
    assert 'name="robots" content="noindex, nofollow"' in reponse.text

    trouvees = re.findall(r"sk_live_[A-Za-z0-9]{32}", reponse.text)
    assert trouvees, "la cle n'est pas affichee a la creation"
    cle = trouvees[0]

    with base() as session:
        compte = accounts.find_by_email(session, FORME["email"])
        assert compte is not None
        assert compte.api_key_hash == accounts.api_key_hash(cle)
        assert cle not in compte.api_key_hash
        assert compte.api_key_prefix == cle[:12]
        assert compte.password_hash.startswith("$2b$12$")
        assert MOT_DE_PASSE not in compte.password_hash
        assert accounts.verify_password(MOT_DE_PASSE, compte.password_hash)
        assert compte.plan == "trial"
        assert compte.trial_ends_at is not None

        prefixe = compte.api_key_prefix

    # La page de l'espace client ne reaffiche jamais la cle, ni le mot de passe.
    espace = client.get("/compte", headers=_auth(FORME["email"], MOT_DE_PASSE))
    assert espace.status_code == 200
    assert espace.headers["cache-control"] == "no-store"
    assert cle not in espace.text
    assert MOT_DE_PASSE not in espace.text
    assert prefixe in espace.text
    assert "Aucune facturation pour l'instant" in espace.text


def test_formulaire_public_et_indexable(base):
    page = client.get("/signup")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "public, max-age=300"
    assert "form-action 'self'" in page.headers["content-security-policy"]
    for champ in ('name="email"', 'name="password"', 'name="shop_name"', 'name="platform"'):
        assert champ in page.text, champ
    assert "12 caractères minimum" in page.text
    # Le plan vient de /pricing (?plan=enterprise) ; une valeur inconnue retombe
    # sur l'essai plutot que d'ouvrir un plan hors offre.
    assert 'value="enterprise"' in client.get("/signup?plan=enterprise").text
    assert 'value="inconnu"' not in client.get("/signup?plan=inconnu").text


def test_mot_de_passe_court_et_champs_invalides_refuses(base):
    court = {**FORME, "password": "court", "email": "court@example.re"}
    reponse = client.post("/signup", data=court)
    assert reponse.status_code == 400
    assert "12 caractères" in reponse.text
    # Le mot de passe n'est jamais reaffiche dans la page (aucune valeur).
    assert 'value="court"' not in reponse.text

    mauvais = {**FORME, "email": "sans-arobase", "platform": "prestashop"}
    reponse = client.post("/signup", data=mauvais)
    assert reponse.status_code == 400
    assert "email ne semble pas valide" in reponse.text
    assert "Shopify, WooCommerce ou Autre" in reponse.text

    vide = {**FORME, "email": "vide@example.re", "shop_name": "   "}
    assert client.post("/signup", data=vide).status_code == 400

    with base() as session:
        # Aucune ecriture n'a eu lieu pour ces trois tentatives.
        assert accounts.find_by_email(session, "court@example.re") is None
        assert accounts.find_by_email(session, "vide@example.re") is None


def test_email_deja_pris_est_dit_clairement(base):
    assert client.post("/signup", data=FORME).status_code == 200
    doublon = {**FORME, "shop_name": "Autre boutique"}
    reponse = client.post("/signup", data=doublon)
    assert reponse.status_code == 409
    assert "existe déjà" in reponse.text
    assert reponse.headers["cache-control"] == "no-store"
    with base() as session:
        assert (
            session.query(Customer)
            .filter(Customer.email == FORME["email"])
            .count()
            == 1
        )


def test_plafond_par_ip_annonce_et_applique(base, monkeypatch):
    """5 inscriptions par IP et par heure : la 6ᵉ est refusée avec `Retry-After`."""
    monkeypatch.setattr(
        signup_route.get_settings(), "signup_rate_limit_per_hour", 5
    )
    for index in range(5):
        reponse = client.post("/signup", data={**FORME, "email": f"client{index}@example.re"})
        assert reponse.status_code == 200, index

    refus = client.post("/signup", data={**FORME, "email": "sixieme@example.re"})
    assert refus.status_code == 429
    assert refus.headers["retry-after"] == "3600"
    assert "Trop de tentatives" in refus.text
    assert ratelimit.fallback_count() > 0  # repli local mesure (Redis absent)

    with base() as session:
        assert accounts.find_by_email(session, "sixieme@example.re") is None


def test_espace_client_ferme_par_defaut(base):
    client.post("/signup", data=FORME)

    anonyme = client.get("/compte")
    assert anonyme.status_code == 401
    assert anonyme.headers["www-authenticate"].lower().startswith("basic")
    assert anonyme.headers["cache-control"] == "no-store"
    assert MOT_DE_PASSE not in anonyme.text

    for entete in (
        _auth(FORME["email"], "mauvais-mot-de-passe"),
        _auth("inconnu@example.re", MOT_DE_PASSE),
    ):
        refus = client.get("/compte", headers=entete)
        assert refus.status_code == 401
        # Meme message pour un email inconnu et un mauvais mot de passe.
        assert "Email ou mot de passe incorrect" in refus.text


def test_statistiques_reelles_dans_l_espace_client(base):
    """Les compteurs affichés viennent des validations réellement servies."""
    client.post("/signup", data=FORME)
    with base() as session:
        compte = accounts.find_by_email(session, FORME["email"])
        for issue in (True, True, False, False, False):
            accounts.record_validation(session, compte.id, "cle-de-cache", issue)

    page = client.get("/compte", headers=_auth(FORME["email"], MOT_DE_PASSE)).text
    assert "sur 100 incluses — reste 95" in page
    # D45 : le quota publie est mensuel, et la page le dit.
    assert "ce mois-ci :" in page
    assert "3 adresses invalides ×" in page
    assert "51.00 $" in page  # 3 x 17 $, borne basse du cout d'un colis en echec
    assert "17 $ (borne basse" in page  # la formule est montree, pas seulement le total


def test_regenerer_la_cle_invalide_l_ancienne(base):
    creation = client.post("/signup", data=FORME)
    ancienne = re.search(r"sk_live_[A-Za-z0-9]{32}", creation.text).group(0)

    reponse = client.post(
        "/compte/cle", headers=_auth(FORME["email"], MOT_DE_PASSE)
    )
    assert reponse.status_code == 200
    assert reponse.headers["cache-control"] == "no-store"
    # Le bloc nouvelle cle vient d'un `html.escape` : on mesure le texte lu.
    assert "L'ancienne clé ne fonctionne plus" in unescape(reponse.text)
    nouvelle = re.search(r"sk_live_[A-Za-z0-9]{32}", reponse.text).group(0)
    assert nouvelle != ancienne

    with base() as session:
        assert accounts.find_by_api_key(session, ancienne) is None
        assert accounts.find_by_api_key(session, nouvelle) is not None

    # Sans mot de passe, la regeneration est refusee : une cle API ne suffit pas.
    assert client.post("/compte/cle").status_code == 401


def test_base_indisponible_donne_503(base, monkeypatch):
    """Base injoignable : un `503` qui explique, jamais des zéros décoratifs."""
    def _panne():
        raise OperationalError("SELECT 1", {}, Exception("connexion refusée"))

    monkeypatch.setattr(signup_route, "SessionLocal", _panne)
    reponse = client.post("/signup", data=FORME)
    assert reponse.status_code == 503
    assert "Base de données indisponible" in reponse.text
    assert reponse.headers["cache-control"] == "no-store"
    assert client.get("/compte", headers=_auth(FORME["email"], MOT_DE_PASSE)).status_code == 503
