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
