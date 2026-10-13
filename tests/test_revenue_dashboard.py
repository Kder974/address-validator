"""Tests du revenu agrege (/metrics/revenue) et du tableau de bord (/dashboard)."""

import base64
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.main import app
from app.routes.dashboard import _page
from app.routes.revenue import aggregate, revenue_stats

client = TestClient(app)

BASE = [
    {"tx_hash": "0xA", "amount": 0.02, "address": "cle-a", "test_mode": False},
    {"tx_hash": "0xB", "amount": 0.02, "address": "cle-a", "test_mode": False},
    {"tx_hash": "0xC", "amount": 0.02, "address": "cle-b", "test_mode": False},
]
#: Deux essais de recette de l'exploitant (D40), comme ceux du 11/10/2026
#: (06:30 et 06:56 UTC) : ils existent, mais ce ne sont pas des clients.
RECETTE = [
    {"tx_hash": "0xessai1", "amount": 0.02, "address": "cle-recette", "test_mode": True},
    {"tx_hash": "0xessai2", "amount": 0.02, "address": "cle-recette", "test_mode": True},
]
#: Un paiement de 24 h, un autre du meme jour, un plus vieux que la fenetre 7 j.
DECALAGES = [timedelta(hours=1), timedelta(hours=2), timedelta(days=10)]


def _jeu(moment):
    return [
        {**ligne, "created_at": (moment - decalage).isoformat()}
        for ligne, decalage in zip(BASE, DECALAGES)
    ]


def _recette(moment):
    """Les deux essais de recette, dates dans la fenetre 24 h."""
    return [
        {**ligne, "created_at": (moment - timedelta(hours=index + 1)).isoformat()}
        for index, ligne in enumerate(RECETTE)
    ]


def test_windows_bound_payments_and_sum_revenue():
    """D31 : les fenetres bornent, le cumul additionne, les preuves se dedupliquent."""
    moment = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    stats = aggregate(_jeu(moment), moment)
    assert stats["windows"]["24h"]["validations_paid"] == 2
    assert stats["windows"]["24h"]["revenue_usdc"] == 0.04
    assert stats["windows"]["24h"]["unique_address_keys"] == 1
    assert stats["windows"]["7d"]["validations_paid"] == 2
    assert stats["windows"]["30d"]["validations_paid"] == 3
    assert stats["validations_paid"] == 3
    assert stats["revenue_usdc"] == 0.06
    assert stats["unique_proofs"] == 3
    assert stats["unique_address_keys"] == 2


def test_recette_exclue_du_revenu_reel():
    """D40 : un essai de recette n'est pas du revenu client.

    C'est le cas mesure le 11/10/2026 : deux validations payees par
    l'exploitant lui-meme affichaient 0,04 USDC de chiffre d'affaires.
    """
    moment = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)
    stats = aggregate(_jeu(moment) + _recette(moment), moment)

    # Le reel ne bouge pas : la recette ne l'enfle pas.
    assert stats["real_validations_paid"] == 3
    assert stats["real_revenue_usdc"] == 0.06
    # Les alias de D31 valent le reel : personne ne lit un chiffre fictif.
    assert stats["validations_paid"] == stats["real_validations_paid"]
    assert stats["revenue_usdc"] == stats["real_revenue_usdc"]
    # Le registre garde tout, et l'ecart est chiffre.
    assert stats["total_validations_paid"] == 5
    assert stats["total_revenue_usdc"] == 0.1
    assert stats["test_validations_paid"] == 2
    assert stats["test_revenue_usdc"] == 0.04
    assert stats["windows"]["24h"]["real_revenue_usdc"] == 0.04
    assert stats["windows"]["24h"]["test_revenue_usdc"] == 0.04

    # Le cas exact du 11/10/2026 : recette seule, aucun client.
    recette_seule = aggregate(_recette(moment), moment)
    assert recette_seule["real_validations_paid"] == 0
    assert recette_seule["real_revenue_usdc"] == 0
    assert recette_seule["windows"]["24h"]["real_revenue_usdc"] == 0
    assert recette_seule["total_revenue_usdc"] == 0.04
    assert recette_seule["test_validations_paid"] == 2

    # Une ligne anterieure a la migration (colonne NULL) reste du reel : c'est
    # scripts/migrate_test_mode.py qui marque, jamais une supposition.
    ancienne = {
        "tx_hash": "0xvieille",
        "amount": 0.02,
        "address": "cle-x",
        "created_at": moment.isoformat(),
        "test_mode": None,
    }
    assert aggregate([ancienne], moment)["real_revenue_usdc"] == 0.02


def test_revenue_reads_the_durable_ledger(tmp_path):
    """D31 : le chiffre vient du registre durable, pas de la memoire du processus."""
    moment = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE payment_transactions "
                "(tx_hash TEXT PRIMARY KEY, amount REAL, address TEXT, created_at TEXT, "
                "test_mode BOOLEAN)"
            )
        )
        for ligne in _jeu(moment) + _recette(moment):
            conn.execute(
                text(
                    "INSERT INTO payment_transactions "
                    "(tx_hash, amount, address, created_at, test_mode) "
                    "VALUES (:t, :a, :k, :c, :m)"
                ),
                {
                    "t": ligne["tx_hash"],
                    "a": ligne["amount"],
                    "k": ligne["address"],
                    "c": ligne["created_at"],
                    "m": ligne["test_mode"],
                },
            )
    charge = revenue_stats(engine=engine, now=moment, include_recent=True)
    assert charge["ledger"]["available"] is True
    assert charge["ledger"]["error"] is None
    assert charge["windows"]["24h"]["validations_paid"] == 2
    assert charge["price_per_validation_usdc"] == 0.02
    # D40 : la marque de recette est lue en base, jamais devinee depuis la date.
    assert charge["ledger"]["real_validations_paid"] == 3
    assert charge["ledger"]["total_validations_paid"] == 5
    assert charge["windows"]["24h"]["test_revenue_usdc"] == 0.04
    # `recording_now` publie si *ce processus* tourne en recette : il suit
    # `TEST_MODE`, jamais la table. La suite se déclare en recette (conftest.py,
    # W1), donc il doit valoir vrai — c'est le signal qui doit rester visible
    # quand on confond un essai avec un encaissement (D40).
    assert charge["test_mode"]["recording_now"] is True
    assert {ligne["tx_hash"]: ligne["test_mode"] for ligne in charge["recent"]} == {
        "0xessai1": True,
        "0xessai2": True,
        "0xA": False,
        "0xB": False,
        "0xC": False,
    }


def test_revenue_degrades_honestly_when_the_ledger_is_silent(monkeypatch):
    """D31 : une base muette doit se voir dans la reponse, pas lever."""

    def _panne(*args, **kwargs):
        raise RuntimeError("base muette")

    monkeypatch.setattr("app.routes.revenue.fetch_rows", _panne)
    charge = revenue_stats()
    assert charge["ledger"]["available"] is False
    assert "base muette" in charge["ledger"]["error"]
    assert charge["windows"] == {}


def test_recording_now_follows_the_declared_mode(monkeypatch):
    """D40/W1 : l'état de recette publié est celui du processus, pas de la table."""
    mode = get_settings()
    monkeypatch.setattr(mode, "test_mode", False)
    assert revenue_stats()["test_mode"]["recording_now"] is False
    monkeypatch.setattr(mode, "test_mode", True)
    assert revenue_stats()["test_mode"]["recording_now"] is True


def test_revenue_endpoint_answers_even_without_a_database():
    """D31 : /metrics/revenue repond toujours 200, avec son etat de registre."""
    reponse = client.get("/metrics/revenue")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["ledger"]["available"] in (True, False)
    assert corps["price_per_validation_usdc"] == 0.02
    assert "session" in corps
    assert set(corps["windows"]) <= {"24h", "7d", "30d"}


def _entete(couple):
    return {"Authorization": "Basic " + base64.b64encode(couple.encode()).decode()}


def test_dashboard_is_closed_without_a_password(monkeypatch):
    """D32 : « oublie de mettre un mot de passe » doit fermer, pas ouvrir."""
    monkeypatch.setattr(get_settings(), "dashboard_password", "")
    reponse = client.get("/dashboard")
    assert reponse.status_code == 503
    assert "DASHBOARD_PASSWORD" in reponse.text
    assert reponse.headers["cache-control"] == "no-store"


def test_dashboard_requires_basic_auth(monkeypatch):
    """D32 : mauvais couple refuse, bon couple servi, page non cachable."""
    monkeypatch.setattr(get_settings(), "dashboard_user", "kder")
    monkeypatch.setattr(get_settings(), "dashboard_password", "secret-de-test")

    anonyme = client.get("/dashboard")
    assert anonyme.status_code == 401
    assert anonyme.headers["www-authenticate"].lower().startswith("basic")
    assert "secret-de-test" not in anonyme.text

    assert client.get("/dashboard", headers=_entete("kder:faux")).status_code == 401
    assert client.get("/dashboard", headers=_entete("intrus:secret-de-test")).status_code == 401

    page = client.get("/dashboard", headers=_entete("kder:secret-de-test"))
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    assert "content-security-policy" in page.headers


def test_dashboard_page_is_self_contained_and_escapes_errors():
    """D32 : aucune ressource externe, et le message d'erreur n'est pas injecte."""
    stats = {
        "generated_at": "2026-10-10T12:00:00+00:00",
        "price_per_validation_usdc": 0.02,
        "source": "postgresql:payment_transactions",
        "ledger": {
            "available": False,
            "error": "<script>alert(1)</script>",
            "validations_paid": 0,
            "revenue_usdc": 0.0,
            "real_validations_paid": 0,
            "real_revenue_usdc": 0.0,
            "test_validations_paid": 2,
            "test_revenue_usdc": 0.04,
            "total_validations_paid": 2,
            "total_revenue_usdc": 0.04,
            "unique_proofs": 0,
            "unique_address_keys": 0,
        },
        "windows": {
            "24h": {"revenue_usdc": 0.04, "real_revenue_usdc": 0.04},
            "7d": {"revenue_usdc": 0.06, "real_revenue_usdc": 0.06},
        },
        "recent": [
            {
                "tx_hash": "0xPORTE",
                "amount_usdc": 0.02,
                "test_mode": True,
                "created_at": "2026-10-10T11:00:00+00:00",
            }
        ],
    }
    page = _page(stats)
    assert "<script" not in page.lower()
    assert "&lt;script&gt;" in page
    assert 'class="bar"' in page
    assert "0xPORTE" in page
    assert "0.04" in page
    # D40 : le revenu mis en avant est le reel, la recette est comptee a part.
    assert "Revenu réel" in page
    assert "recette, exclue" in page
    assert "0.04 USDC" in page
    for motif in ("http://", "https://", "@import", "url(http"):
        assert motif not in page, f"ressource externe dans la page : {motif}"

