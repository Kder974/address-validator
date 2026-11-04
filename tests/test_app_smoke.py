"""Test de fumée : l'application démarre et la racine répond."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_returns_service_info():
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "Address Validator API"
    assert body["manifest"] == "/manifest"


def test_openapi_schema_is_exposed():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Address Validator API"


def test_docs_page_is_exposed():
    assert client.get("/docs").status_code == 200


def test_un_site_tiers_lit_l_api_mais_jamais_avec_les_cookies():
    """D105 : CORS ouvert pour les agents x402 dans un navigateur, fermé aux cookies du client."""
    tiers = {"Origin": "https://autre-site.example"}
    # Avec un cookie, Starlette renvoie l'origine plutot que `*` ; sans
    # `Allow-Credentials: true`, le navigateur ne donne quand meme pas la reponse au site tiers.
    avec_cookie = client.get("/robots.txt", headers={**tiers, "Cookie": "av_session=x"})
    assert "access-control-allow-credentials" not in avec_cookie.headers
    assert client.get("/robots.txt", headers=tiers).headers.get("access-control-allow-origin") == "*"
    preflight = client.options("/validate", headers={
        **tiers, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-payment"})
    assert preflight.status_code == 200
    assert "access-control-allow-credentials" not in preflight.headers
    assert "PAYMENT-RESPONSE" in client.get("/robots.txt", headers=tiers).headers.get("access-control-expose-headers", "")


def test_le_service_refuse_de_demarrer_en_production_avec_le_secret_par_defaut(monkeypatch):
    """D107 : un demarrage hors script de deploiement refuse aussi `change_me`."""
    import asyncio

    import pytest

    from app import main

    reglages = main.settings.model_copy(update={"environment": "production", "api_secret_key": "change_me"})
    monkeypatch.setattr(main, "settings", reglages)
    monkeypatch.setattr(main, "init_db", lambda: True)

    async def demarrer():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(RuntimeError, match="change_me"):
        asyncio.run(demarrer())


def test_chaque_reglage_est_documente_dans_env_example():
    """D107 : un reglage de app/config.py absent de .env.example est invisible a l'installation."""
    import re
    from pathlib import Path

    from app.config import Settings

    exemple = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    absents = [nom.upper() for nom in Settings.model_fields if not re.search(rf"^#?\s*{nom.upper()}=", exemple, re.M)]
    assert absents == []


def test_un_echec_de_init_db_dit_pourquoi(monkeypatch, caplog):
    """D107 : le demarrage reste non bloquant, mais la cause de l'echec est au journal."""
    from app import database

    class Moteur:
        def connect(self):
            raise RuntimeError("base injoignable")

    monkeypatch.setattr(database, "engine", Moteur())
    with caplog.at_level("WARNING", logger="app.database"):
        assert database.init_db() is False
    assert "base injoignable" in caplog.text
