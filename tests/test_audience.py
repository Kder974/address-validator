"""Tests du suivi d'audience et d'usage, et du blocage du tableau de bord (D67).

Ce qui est mesuré, et pourquoi :

  * une visite d'une page publique laisse une ligne (page, pays, site d'origine,
    famille de robot) et rien d'autre : ni IP, ni adresse ; une page privée ou une
    route technique n'en laisse pas ;
  * un appel d'API garde son vrai statut, sa durée, sa méthode MCP, le pays de
    l'adresse, et le montant **seulement** s'il a été encaissé hors recette (D40) ;
  * la base des pays absente ne casse rien (pays inconnu) ;
  * l'écriture par lots survit à une base injoignable, et la purge respecte la
    conservation annoncée ;
  * cinq mots de passe faux bloquent le tableau de bord, même avec le bon ensuite.
"""

import base64
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import audience
from app.config import get_settings
from app.main import app
from app.models import ApiCall, PageVisit
from app.routes.metrics import metrics
from tests.test_x402 import PAYLOAD, _header

client = TestClient(app)


class _Base:
    """Faux lecteur DB-IP : un pays par adresse IP."""

    def __init__(self, pays):
        self.pays = pays

    def get(self, ip):
        code = self.pays.get(ip)
        return {"country": {"iso_code": code}} if code else None


@pytest.fixture
def file(monkeypatch, tmp_path):
    """La file des lignes, sans le fil d'écriture, avec une base des pays factice."""
    base = tmp_path / "pays.mmdb"
    base.write_bytes(b"x")
    monkeypatch.setattr(get_settings(), "geoip_db_path", str(base))
    monkeypatch.setattr(audience, "_lecteur", lambda chemin, version: _Base({"203.0.113.9": "FR", "198.51.100.4": "us"}))
    monkeypatch.setattr(audience, "_demarrer", lambda: None)
    audience._FILE.clear()
    yield audience._FILE
    audience._FILE.clear()


def _lignes(file, modele):
    return [ligne for ligne in file if isinstance(ligne, modele)]


def test_une_visite_laisse_page_pays_source_et_robot(file):
    client.get("/en/pricing", headers={
        "X-Forwarded-For": "203.0.113.9", "Referer": "https://www.google.com/search?q=adresse",
        "User-Agent": "Mozilla/5.0 (compatible; GPTBot/1.1; +https://openai.com/gptbot)",
    })
    [visite] = _lignes(file, PageVisit)
    assert (visite.path, visite.country, visite.referrer_host, visite.robot) == ("/en/pricing", "FR", "google.com", "GPTBot")
    # Rien d'autre que ces colonnes : ni IP, ni User-Agent complet.
    assert {c.name for c in PageVisit.__table__.columns} == {"id", "path", "country", "referrer_host", "robot", "created_at"}


def test_un_humain_sans_origine_et_les_pages_non_suivies(file):
    client.get("/agents", headers={"X-Forwarded-For": "198.51.100.4", "User-Agent": "Mozilla/5.0 Safari"})
    [visite] = _lignes(file, PageVisit)
    assert (visite.country, visite.referrer_host, visite.robot) == ("US", None, None)
    for chemin in ("/health", "/compte/connexion", "/manifest", "/page-inconnue"):
        client.get(chemin)
    # La racine ne compte que quand elle sert la vitrine, pas le JSON d'un programme.
    client.get("/", headers={"Accept": "application/json", "User-Agent": "curl/8"})
    assert len(_lignes(file, PageVisit)) == 1


def test_sans_base_des_pays_le_pays_est_inconnu(file, monkeypatch):
    monkeypatch.setattr(get_settings(), "geoip_db_path", "/nulle/part/pays.mmdb")
    assert audience.pays("203.0.113.9") is None
    client.get("/pricing", headers={"X-Forwarded-For": "203.0.113.9"})
    assert _lignes(file, PageVisit)[0].country is None


def test_un_refus_402_est_un_appel_compte(file):
    reponse = client.post("/validate", json=PAYLOAD, headers={"X-Forwarded-For": "203.0.113.9"})
    assert reponse.status_code == 402
    [appel] = _lignes(file, ApiCall)
    assert (appel.endpoint, appel.status, appel.country, appel.amount_usdc) == ("validate", 402, "FR", None)
    assert appel.duration_ms >= 0


@pytest.mark.parametrize("recette", [True, False])
def test_le_montant_n_est_note_que_hors_recette(file, facilitator, monkeypatch, recette):
    monkeypatch.setattr(get_settings(), "test_mode", recette)
    reponse = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": _header()})
    assert reponse.status_code == 200
    [appel] = _lignes(file, ApiCall)
    assert appel.mode == "x402" and appel.address_country == "US" and appel.cache_hit is not None
    assert appel.amount_usdc == (None if recette else get_settings().x402_price)


def test_mcp_et_essai_gratuit(file):
    client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    client.post("/en/validate-form", data={"street": "1 Main St", "city": "Austin", "state": "TX", "zip": "78701", "country": "us"})
    mcp, essai = _lignes(file, ApiCall)
    assert (mcp.endpoint, mcp.method, mcp.status) == ("mcp", "tools/list", 200)
    assert (essai.endpoint, essai.mode, essai.address_country) == ("validate-form", "essai", "US")


def test_classements():
    assert audience.robot("Mozilla/5.0 (compatible; bingbot/2.0)") == "Bingbot"
    assert audience.robot("Mozilla/5.0 (compatible; UnBot/1.0)") == "Autre robot"
    assert audience.robot("Mozilla/5.0 (Macintosh) Safari/605") is None
    assert audience.source("https://chatgpt.com/c/123?x=1") == "chatgpt.com"
    assert audience.source("pas une adresse") is None and audience.source("") is None
    assert audience.code_pays("fr") == "FR" and audience.code_pays("USA") is None and audience.code_pays(None) is None


def _fabrique(tmp_path):
    moteur = create_engine(f"sqlite:///{tmp_path / 'a.sqlite'}")
    PageVisit.__table__.create(moteur)
    ApiCall.__table__.create(moteur)
    return sessionmaker(bind=moteur)


def test_ecriture_par_lots_et_base_injoignable(tmp_path, monkeypatch):
    monkeypatch.setattr(audience, "_demarrer", lambda: None)
    audience._FILE.clear()
    fabrique = _fabrique(tmp_path)
    audience._mettre(PageVisit(path="/", created_at=audience._maintenant()))
    audience._mettre(ApiCall(endpoint="mcp", status=200, duration_ms=3, created_at=audience._maintenant()))
    assert audience.vider(fabrique) == 2 and not audience._FILE
    with fabrique() as session:
        assert len(session.scalars(select(PageVisit)).all()) == 1

    def _panne():
        raise RuntimeError("base injoignable")

    avant = metrics.snapshot().get("audience_lignes_perdues", 0)
    audience._mettre(PageVisit(path="/", created_at=audience._maintenant()))
    assert audience.vider(_panne) == 0 and not audience._FILE
    assert metrics.snapshot()["audience_lignes_perdues"] == avant + 1


def test_la_purge_garde_la_conservation_annoncee(tmp_path):
    fabrique = _fabrique(tmp_path)
    maintenant = datetime(2026, 10, 16, 12)
    jours = get_settings().audience_retention_days
    with fabrique() as session:
        session.add_all([
            PageVisit(path="/vieux", created_at=maintenant - timedelta(days=jours + 1)),
            PageVisit(path="/recent", created_at=maintenant - timedelta(days=jours - 1)),
            ApiCall(endpoint="mcp", status=200, duration_ms=1, created_at=maintenant - timedelta(days=jours + 1)),
        ])
        session.commit()
    assert audience.purger(fabrique, maintenant)
    with fabrique() as session:
        assert [v.path for v in session.scalars(select(PageVisit))] == ["/recent"]
        assert session.scalars(select(ApiCall)).all() == []


def _basic(mot_de_passe: str) -> str:
    return "Basic " + base64.b64encode(f"kder:{mot_de_passe}".encode()).decode()


def test_cinq_mots_de_passe_faux_bloquent_le_tableau_de_bord(monkeypatch):
    reglages = get_settings()
    monkeypatch.setattr(reglages, "dashboard_user", "kder")
    monkeypatch.setattr(reglages, "dashboard_password", "le-bon-mot-de-passe")
    ip = {"X-Forwarded-For": "192.0.2.77"}
    # Le premier passage d'un navigateur, sans identifiants, n'est pas un échec.
    for _ in range(reglages.login_max_failures + 2):
        assert client.get("/dashboard", headers=ip).status_code == 401
    for _ in range(reglages.login_max_failures):
        assert client.get("/dashboard", headers={**ip, "Authorization": _basic("faux")}).status_code == 401
    bloque = client.get("/dashboard", headers={**ip, "Authorization": _basic("le-bon-mot-de-passe")})
    assert bloque.status_code == 429 and int(bloque.headers["retry-after"]) > 0
    assert client.get("/prospection", headers={**ip, "Authorization": _basic("le-bon-mot-de-passe")}).status_code == 429
    # Une autre adresse IP n'est pas bloquée.
    assert client.get("/dashboard", headers={"X-Forwarded-For": "192.0.2.78", "Authorization": _basic("le-bon-mot-de-passe")}).status_code != 429


def test_une_route_qui_plante_est_comptee_en_500(file, monkeypatch):
    from app.routes import mcp

    def _plante(*args, **kwargs):
        raise RuntimeError("panne")

    monkeypatch.setattr(mcp, "_dispatch", _plante)
    with pytest.raises(RuntimeError):
        client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    [appel] = _lignes(file, ApiCall)
    assert (appel.endpoint, appel.status) == ("mcp", 500)


def test_navigation_interne_et_programmes(file):
    # Sous no-referrer, la navigation interne n'a pas de Referer : Sec-Fetch-Site la reconnaît.
    client.get("/pricing", headers={"Sec-Fetch-Site": "same-origin", "User-Agent": "Mozilla/5.0 Safari"})
    client.get("/pricing", headers={"Sec-Fetch-Site": "none", "User-Agent": "Mozilla/5.0 Safari"})
    client.get("/pricing", headers={"User-Agent": "curl/8.4.0"})
    interne, directe, programme = _lignes(file, PageVisit)
    assert interne.referrer_host == "kder-ai.com" and directe.referrer_host is None
    assert programme.robot == "Programme" and interne.robot is None


def test_connexion_avec_accents_ne_plante_pas(monkeypatch):
    reglages = get_settings()
    monkeypatch.setattr(reglages, "dashboard_user", "kder")
    monkeypatch.setattr(reglages, "dashboard_password", "le-bon-mot-de-passe")
    ip = {"X-Forwarded-For": "192.0.2.90"}
    assert client.post("/dashboard/connexion", data={"user": "Frédéric", "password": "é"}, headers=ip).status_code == 401
    assert client.get("/dashboard", headers={**ip, "Authorization": _basic("é")}).status_code == 401
