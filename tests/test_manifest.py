"""Tests de l'endpoint de découverte /manifest (format x402 Bazaar)."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_manifest_is_exposed():
    response = client.get("/manifest")
    assert response.status_code == 200


def test_manifest_declares_x402_pricing():
    body = client.get("/manifest").json()
    assert body["name"] == "AddressValidator"
    assert body["protocol"] == "x402"
    assert body["currency"] == "USDC"
    assert body["price"] == 0.02


def test_manifest_describes_validate_endpoint():
    body = client.get("/manifest").json()
    assert "validate" in body["endpoints"]
    endpoint = body["endpoints"]["validate"]
    assert endpoint["method"] == "POST"
    assert endpoint["path"] == "/validate"
    assert "input_schema" in endpoint
    assert "output_schema" in endpoint


def test_manifest_has_discovery_metadata():
    body = client.get("/manifest").json()
    assert body["category"] == "data"
    assert "address" in body["tags"]
    assert body["license"] == "MIT"


def test_manifest_schemas_are_derived_from_the_models():
    """D33 : un schema recopie derive du contrat des la premiere evolution."""
    from app.schemas import ValidationRequest, ValidationResponse

    endpoint = client.get("/manifest").json()["endpoints"]["validate"]
    assert endpoint["input_schema"] == ValidationRequest.model_json_schema()
    assert endpoint["output_schema"] == ValidationResponse.model_json_schema()


def test_manifest_402_example_is_the_body_the_route_returns():
    """D33 : l'exemple d'erreur est genere par la meme fonction que la route."""
    from app.payment import generate_payment_required_response

    endpoint = client.get("/manifest").json()["endpoints"]["validate"]
    assert endpoint["errors"]["402"]["body_example"] == generate_payment_required_response(
        endpoint["price"]
    )


def test_manifest_examples_are_valid_calls_including_degraded_mode():
    """D33 : chaque exemple doit etre un appel reel, mode degrade compris."""
    from app.schemas import ValidationRequest, ValidationResponse

    exemples = client.get("/manifest").json()["endpoints"]["validate"]["examples"]
    assert len(exemples) >= 2
    for exemple in exemples:
        ValidationRequest.model_validate(exemple["request"])
        ValidationResponse.model_validate(exemple["response_200"])
    assert any(e["response_200"]["result"]["degraded"] for e in exemples)


def test_manifest_states_its_limits_and_payment_reality():
    """D33/D34 : les limites nomment le mode degrade, l'appel MCP et x402."""
    body = client.get("/manifest").json()
    assert len(body["limitations"]) >= 5
    texte = " ".join(body["limitations"]).lower()
    for attendu in ("x402", "degrade", "mcp", "international"):
        assert attendu in texte

    standard = body["payment"]["x402_standard"]
    assert standard["compliant"] is True, (
        "le manifeste doit declarer conforme un protocole reellement implemente"
    )
    assert standard["missing"] == []
    assert len(standard["verified"]) >= 4
    assert body["payment"]["header"] == "X-PAYMENT"
    assert body["payment"]["response_header"] == "X-PAYMENT-RESPONSE"
    assert body["contact"]["email"]
    assert {"documentation", "health", "manifest", "mcp"} <= set(body["discovery"])


def test_manifest_accepts_is_the_offer_the_402_returns():
    """D34 : une seule offre, produite par une seule fonction.

    Un manifeste qui publierait une offre differente du corps 402 ferait payer
    un montant que le serveur refuse ensuite.
    """
    from app.payment import generate_payment_required_response

    body = client.get("/manifest").json()
    facture = client.post("/validate", json={"address": {"street": "1 A St", "city": "X", "state": "CA", "zip": "90001"}})
    assert facture.status_code == 402
    assert body["payment"]["accepts"] == facture.json()["accepts"]
    assert body["payment"]["accepts"] == generate_payment_required_response(
        body["price"]
    )["accepts"]


def test_manifest_describes_the_mcp_endpoint():
    """D37 : le manifeste publie aussi le serveur MCP, schemas compris."""
    from app.schemas import ValidationRequest, ValidationResponse

    endpoint = client.get("/manifest").json()["endpoints"]["mcp"]
    assert endpoint["path"] == "/mcp"
    assert endpoint["protocol"] == "json-rpc-2.0"
    assert endpoint["tool"] == "validate_address"
    assert endpoint["input_schema"] == ValidationRequest.model_json_schema()
    assert endpoint["output_schema"] == ValidationResponse.model_json_schema()
    assert "tools/list" in endpoint["free_methods"]
    assert endpoint["paid_methods"] == ["tools/call"]


def test_manifest_explains_every_deliverability_value():
    body = client.get("/manifest").json()
    valeurs = body["deliverability_values"]
    assert set(valeurs) == {"residential", "commercial", "unknown", "invalid"}
    assert all(len(texte) > 20 for texte in valeurs.values())
