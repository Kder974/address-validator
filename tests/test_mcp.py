"""Tests du serveur MCP (`POST /mcp`) et de sa carte de decouverte (D37).

Les helpers de signature x402 sont importes de `tests/test_x402.py` : une seule
definition de la signature EIP-712, donc un seul comportement a maintenir.
"""

import json

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.routes.mcp import CARD_LAST_UPDATED, MCP_PROTOCOL_VERSION, tool_definition
from app.schemas import ValidationRequest, ValidationResponse
from tests.test_x402 import PAYLOAD, TX, _header

client = TestClient(app)
settings = get_settings()


def call(method: str, params: dict | None = None, request_id: int = 1):
    """POST /mcp d'un message JSON-RPC."""
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body)


def test_initialize_negotiates_the_requested_version():
    """D37 : la poignee de main est gratuite et nomme le serveur."""
    response = call("initialize", {"protocolVersion": MCP_PROTOCOL_VERSION})
    assert response.status_code == 200
    assert response.headers["MCP-Protocol-Version"] == MCP_PROTOCOL_VERSION
    result = response.json()["result"]
    assert result["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "address-validator"
    assert "tools" in result["capabilities"]
    assert result["instructions"]


def test_initialize_refuses_an_unknown_version_but_answers_its_own():
    """Une version inconnue n'est pas rejouee : on annonce la notre."""
    result = call("initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert result["protocolVersion"] == MCP_PROTOCOL_VERSION


def test_tools_list_is_free_and_derived_from_the_models():
    """D37 : decouvrir ne coute rien, et le schema ne peut pas deriver du code."""
    response = call("tools/list")
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert len(tools) == 1
    tool = tools[0]
    assert tool["name"] == "validate_address"
    assert tool["inputSchema"] == ValidationRequest.model_json_schema()
    assert tool["outputSchema"] == ValidationResponse.model_json_schema()
    assert tool["annotations"]["readOnlyHint"] is True


def test_tools_call_without_payment_returns_402_and_the_offer():
    """D37 + D34 : l'appel est payant, et le refus dit exactement comment payer."""
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "validate_address", "arguments": PAYLOAD}},
    )
    assert response.status_code == 402
    error = response.json()["error"]
    assert error["code"] == -32002
    assert error["data"]["accepts"][0]["maxAmountRequired"] == "20000"
    assert error["data"]["accepts"][0]["payTo"] == settings.cdp_wallet_address
    # Aucun paiement n'a ete presente : il n'y a donc rien a regler, et l'en-tete
    # de reglement (`X-PAYMENT-RESPONSE`) n'est pas emis a vide.
    assert "X-PAYMENT-RESPONSE" not in response.headers


def test_tools_call_with_a_bad_payment_reports_the_failed_settlement():
    """D34 : un paiement presente mais refuse est decrit dans l'en-tete spec."""
    import base64

    response = client.post(
        "/mcp",
        headers={"X-PAYMENT": base64.b64encode(b"{}").decode()},
        json={"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "validate_address", "arguments": PAYLOAD}},
    )
    assert response.status_code == 402
    receipt = json.loads(
        base64.b64decode(response.headers["X-PAYMENT-RESPONSE"] + "===")
    )
    assert receipt["success"] is False
    assert receipt["errorReason"]
    assert response.json()["error"]["code"] == -32002


def test_tools_call_with_bad_arguments_is_free_and_named():
    """Une entree mal formee est refusee sans facturation."""
    response = call("tools/call", {"name": "validate_address", "arguments": {}})
    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32602
    assert error["data"]["errors"]


def test_tools_call_paid_returns_structured_content(facilitator):
    """D37 : l'outil paye rend le meme contrat que la route HTTP."""
    response = client.post(
        "/mcp",
        headers={"X-PAYMENT": _header()},
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "validate_address", "arguments": PAYLOAD},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False
    structured = result["structuredContent"]
    assert structured["price_paid"] == 0.02
    assert structured["transaction_id"] == TX
    assert "standardized" in structured["result"]
    assert json.loads(result["content"][0]["text"]) == structured


def test_ledger_write_failure_is_refused_not_served(facilitator, monkeypatch):
    """W2/D41 : l'outil paye suit la meme regle que la route HTTP.

    Le reglement x402 a reussi mais la ligne de registre n'a pas pu etre ecrite :
    servir `structuredContent` ferait un revenu perdu et invisible (D40). La
    reponse est une erreur JSON-RPC interne en `500`, et le compteur monte.
    """
    import app.routes.mcp as mcp_route
    from app.routes.metrics import metrics

    monkeypatch.setattr(mcp_route, "record_transaction", lambda tx, amount, key: False)
    avant = metrics.snapshot()["ledger_write_failures_total"]
    response = client.post(
        "/mcp",
        headers={"X-PAYMENT": _header()},
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "validate_address", "arguments": PAYLOAD},
        },
    )
    apres = metrics.snapshot()["ledger_write_failures_total"]

    assert response.status_code == 500
    body = response.json()
    assert "result" not in body
    error = body["error"]
    assert error["code"] == -32603
    assert error["message"] == "paiement non enregistre : outil non servi"
    assert error["data"]["transaction"] == TX
    assert apres == avant + 1


def test_mcp_and_http_share_the_same_pipeline_and_cache(facilitator):
    """Une seule verite : meme entree, meme cle de cache que /validate."""
    from app.payment import build_payment_proof

    http = client.post(
        "/validate", json=PAYLOAD, headers={"X-Payment-Proof": build_payment_proof("0xSHARED")}
    )
    assert http.status_code == 200

    from tests.test_x402 import _authorization, _sign

    authorization = _authorization(nonce="0x" + "77" * 32)
    payload = {
        "x402Version": 1,
        "scheme": "exact",
        "network": settings.x402_network,
        "payload": {"signature": _sign(authorization), "authorization": authorization},
    }
    import base64

    response = client.post(
        "/mcp",
        headers={"X-PAYMENT": base64.b64encode(json.dumps(payload).encode()).decode()},
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "validate_address", "arguments": PAYLOAD},
        },
    )
    assert response.status_code == 200
    assert (
        response.json()["result"]["structuredContent"]["result"]["cache_key"]
        == http.json()["result"]["cache_key"]
    )


def test_notification_gets_no_body_and_a_202():
    """JSON-RPC 2.0 : une notification ne recoit pas de reponse."""
    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert response.status_code == 202
    assert response.content == b""


def test_unknown_method_is_method_not_found():
    response = call("tools/teleport")
    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32601


def test_broken_json_is_a_parse_error():
    response = client.post(
        "/mcp", content=b"{pas du json", headers={"Content-Type": "application/json"}
    )
    assert response.json()["error"]["code"] == -32700


def test_batch_requests_are_answered_in_order():
    """Le transport accepte un lot JSON-RPC (2 reponses pour 2 requetes)."""
    response = client.post(
        "/mcp",
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [1, 2]
    assert body[0]["result"] == {}
    assert body[1]["result"]["tools"][0]["name"] == "validate_address"


def test_get_on_mcp_is_explicitly_405():
    """Pas de flux SSE : le client est renvoye vers POST, pas vers un 404."""
    response = client.get("/mcp")
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"
    assert "POST /mcp" in response.json()["detail"]


def test_well_known_card_describes_the_live_endpoint():
    """D37 : la carte est servie par l'app, donc elle ne peut pas mentir."""
    response = client.get("/.well-known/mcp-server")
    assert response.status_code == 200
    card = response.json()
    assert card["mcp_version"] == MCP_PROTOCOL_VERSION
    assert card["endpoint"] == settings.mcp_public_url
    assert card["transport"] == "http"
    assert card["auth"] == {"required": True, "methods": ["x402"]}
    assert card["capabilities"] == ["tools"]
    assert card["coverage"] == "US", "la couverture reelle est annoncee"
    assert card["contact"] == settings.contact_email
    assert card["last_updated"] == CARD_LAST_UPDATED
    assert card["crawl"] is True and card["cache_ttl"] > 0


def test_card_publishes_only_documented_fields():
    """Aucun champ invente : le brouillon mcp1 definit cet ensemble precis.

    Contre-epreuve de D28 : une carte qui porterait des champs inventes ferait
    echouer ce test, ce qui force a documenter une source avant de publier.
    """
    card = client.get("/.well-known/mcp-server").json()
    assert set(card) == {
        "mcp_version",
        "name",
        "description",
        "endpoint",
        "transport",
        "auth",
        "capabilities",
        "categories",
        "languages",
        "coverage",
        "contact",
        "docs",
        "last_updated",
        "cache_ttl",
        "crawl",
    }
    # Les capacites annoncees sont celles reellement servies : un seul outil.
    assert client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ).json()["result"]["tools"] == [tool_definition()]
