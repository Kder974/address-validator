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
    {"tx_hash": "0xA", "amount": 0.02, "address": "cle-a"},
    {"tx_hash": "0xB", "amount": 0.02, "address": "cle-a"},
    {"tx_hash": "0xC", "amount": 0.02, "address": "cle-b"},
]
#: Un paiement de 24 h, un autre du meme jour, un plus vieux que la fenetre 7 j.
DECALAGES = [timedelta(hours=1), timedelta(hours=2), timedelta(days=10)]


def _jeu(moment):
    return [
        {**ligne, "created_at": (moment - decalage).isoformat()}
        for ligne, decalage in zip(BASE, DECALAGES)
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


def test_revenue_reads_the_durable_ledger(tmp_path):
    """D31 : le chiffre vient du registre durable, pas de la memoire du processus."""
    moment = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    engine = create_engine(f"sqlite:///{tmp_path / 'ledger.sqlite'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE payment_transactions "
                "(tx_hash TEXT PRIMARY KEY, amount REAL, address TEXT, created_at TEXT)"
            )
        )
        for ligne in _jeu(moment):
            conn.execute(
                text(
                    "INSERT INTO payment_transactions (tx_hash, amount, address, created_at) "
                    "VALUES (:t, :a, :k, :c)"
                ),
                {
                    "t": ligne["tx_hash"],
                    "a": ligne["amount"],
                    "k": ligne["address"],
                    "c": ligne["created_at"],
                },
            )
    charge = revenue_stats(engine=engine, now=moment)
    assert charge["ledger"]["available"] is True
    assert charge["ledger"]["error"] is None
    assert charge["windows"]["24h"]["validations_paid"] == 2
    assert charge["price_per_validation_usdc"] == 0.02


def test_revenue_degrades_honestly_when_the_ledger_is_silent(monkeypatch):
    """D31 : une base muette doit se voir dans la reponse, pas lever."""

    def _panne(*args, **kwargs):
        raise RuntimeError("base muette")

    monkeypatch.setattr("app.routes.revenue.fetch_rows", _panne)
    charge = revenue_stats()
    assert charge["ledger"]["available"] is False
    assert "base muette" in charge["ledger"]["error"]
    assert charge["windows"] == {}


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
            "unique_proofs": 0,
            "unique_address_keys": 0,
        },
        "windows": {"24h": {"revenue_usdc": 0.04}, "7d": {"revenue_usdc": 0.06}},
        "recent": [
            {
                "tx_hash": "0xPORTE",
                "amount_usdc": 0.02,
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
    for motif in ("http://", "https://", "@import", "url(http"):
        assert motif not in page, f"ressource externe dans la page : {motif}"

