"""D95 : `/openapi.json` ne publie que l'API, et `/.well-known/x402` répond."""

from urllib.parse import urlparse

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import API_PUBLIEE, app

client = TestClient(app)


def test_openapi_ne_publie_que_l_api():
    chemins = set(client.get("/openapi.json").json()["paths"])
    assert chemins <= API_PUBLIEE
    for page in ("/validate-form", "/dashboard", "/prospection", "/compte", "/signup", "/pricing", "/guide"):
        assert page not in chemins
    assert {"/validate", "/mcp", "/manifest", "/.well-known/x402"} <= chemins


def test_les_pages_masquees_repondent_toujours():
    assert client.get("/pricing").status_code == 200
    assert client.get("/validate-form").status_code == 200


def test_manifeste_x402():
    reponse = client.get("/.well-known/x402")
    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("application/json")
    manifeste = reponse.json()
    assert manifeste["x402Version"] == 2 and manifeste["kind"] == "resource-server"
    hote = urlparse(get_settings().public_base_url).hostname
    urls = [r["url"] for r in manifeste["resources"]]
    assert urls and all(urlparse(u).scheme == "https" and urlparse(u).hostname == hote for u in urls)


def test_manifeste_et_402_citent_la_meme_ressource():
    offre = client.post("/validate", json={}).json()["accepts"][0]
    assert offre["resource"] in [r["url"] for r in client.get("/.well-known/x402").json()["resources"]]
