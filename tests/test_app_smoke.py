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
