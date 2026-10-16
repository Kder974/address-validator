"""Serveur MCP (Model Context Protocol) — décision D37.

`POST /mcp` expose un serveur MCP en **JSON-RPC 2.0** sur HTTP (transport
« streamable HTTP », spécification MCP 2025-06-18) avec un seul outil payant :
`validate_address`.

Pourquoi un serveur MCP : un agent qui parle MCP n'a pas besoin de lire un
README. Il appelle `tools/list`, découvre `validate_address` avec le schéma
d'entrée **dérivé des modèles Pydantic** (D33 : un schéma recopié dérive), puis
`tools/call`. Le paiement est le même qu'en HTTP : x402 (`X-PAYMENT`), donc un
agent qui sait payer un service x402 sait payer cet outil.

Découvrir est **gratuit** (`initialize`, `ping`, `tools/list`, `resources/list`,
`prompts/list`) : c'est la découverte qui est le sujet de ce chantier, la
facturer reviendrait à cacher la porte d'entrée. Appeler l'outil est **payant**.
Un appel non payé reçoit `402` avec une erreur JSON-RPC qui porte `data.accepts`
— de quoi payer et rejouer sans documentation.

Choix d'implémentation :

* serveur **sans état** : aucune session (`Mcp-Session-Id`) n'est émise ; la
  spécification le permet et cela évite de stocker un état par client ;
* réponse toujours en `application/json` (jamais SSE) : le service n'a rien à
  pousser, et un flux SSE serait une promesse de plus à tenir ;
* `GET /mcp` répond `405` + `Allow: POST` : un client qui tente un flux SSE
  reçoit une réponse explicite plutôt qu'un `404` ambigu ;
* les paramètres sont validés **avant** de demander le paiement : une requête
  mal formée n'est jamais facturée.

La carte de découverte `GET /.well-known/mcp-server` est **servie par
l'application**, et non par un fichier statique copié pour Nginx : elle décrit
l'endpoint réel, elle doit donc disparaître avec lui. Un alias statique
survivrait à l'arrêt du service et ferait déclarer une URL morte (D28).

D41 : un encaissement qui n'a pas pu être inscrit au registre n'est pas servi.
L'outil est le même service que `/validate`, il suit donc la même règle — erreur
JSON-RPC `-32603` (statut HTTP `500`) et compteur `ledger_write_failures_total`.
Servir une réponse sans ligne au registre, c'est encaisser sans trace : le
client a payé, le revenu publié ne le verra jamais (D40).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app import audience
from app.config import get_settings
from app.observability import log_unpaid_attempt
from app.payment import (
    PAYMENT_RESPONSE_HEADER,
    generate_payment_required_response,
    payment_result,
    record_transaction,
)
from app.routes.metrics import metrics
from app.routes.validate import build_response, perform_validation
from app.schemas import ValidationRequest, ValidationResponse
from app.x402 import payment_requirements

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)

#: Version du protocole MCP servie, et versions acceptées en entrée.
MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26")

SERVER_NAME = "address-validator"
SERVER_VERSION = "1.0.0"
TOOL_NAME = "validate_address"
TOOL_DESCRIPTION = (
    "Valide une adresse de livraison et renvoie sa forme standardisee, ses "
    "coordonnees et un score de livrabilite (residential / commercial / "
    "unknown / invalid) avec un niveau de confiance. Une reponse 200 avec "
    "degraded=true signale que le fournisseur etait injoignable : la reponse "
    "est alors incomplete et le champ confidence est bas. Cout : 1 "
    "validation (0.02 USDC sur Base, reglee en x402)."
)

# Codes d'erreur JSON-RPC 2.0 et un code d'application pour le paiement.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
PAYMENT_REQUIRED = -32002


def tool_definition() -> dict[str, Any]:
    """Description de l'outil, schémas compris, dérivée des modèles Pydantic.

    Rien n'est recopié : le jour où `ValidationRequest` change, `tools/list`
    change avec lui (D33, appliquée ici au protocole MCP).
    """
    return {
        "name": TOOL_NAME,
        "title": "Valider une adresse de livraison",
        "description": TOOL_DESCRIPTION,
        "inputSchema": ValidationRequest.model_json_schema(),
        "outputSchema": ValidationResponse.model_json_schema(),
        "annotations": {
            "title": "Valider une adresse de livraison",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    }


def _json(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    """Réponse JSON-RPC : jamais de `Cache-Control` implicite, jamais de SSE."""
    return JSONResponse(
        status_code=status,
        content=payload,
        headers={
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "Cache-Control": "no-store",
        },
    )


def _result(request_id: Any, result: Any) -> JSONResponse:
    return _json({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(
    request_id: Any, code: int, message: str, data: Any | None = None, status: int = 200
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return _json({"jsonrpc": "2.0", "id": request_id, "error": error}, status=status)


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    """Poignée de main : version négociée + capacités réellement servies."""
    requested = params.get("protocolVersion")
    version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSION
    return {
        "protocolVersion": version,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {},
            "prompts": {},
        },
        "serverInfo": {
            "name": SERVER_NAME,
            "title": "Address Validator API",
            "version": SERVER_VERSION,
        },
        "instructions": (
            "Serveur d'un seul outil payant : validate_address. Appeler l'outil "
            "coute 0.02 USDC sur le reseau Base, regle par le protocole x402 : "
            "presenter l'en-tete X-PAYMENT (base64 d'un PaymentPayload) sur la "
            "requete HTTP. Un appel non paye recoit une erreur JSON-RPC 402 "
            "dont data.accepts porte l'offre exacte (reseau, jeton, montant, "
            "adresse de destination). Decouvrir (tools/list) est gratuit."
        ),
    }


def _payment_error(request_id: Any, request: Request, decision) -> JSONResponse:
    """Refus 402 : erreur JSON-RPC **et** offre lisible par un client x402."""
    log_unpaid_attempt(request, settings.x402_price, decision.reason)
    headers = {"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}
    if decision.response_header:
        headers[PAYMENT_RESPONSE_HEADER] = decision.response_header
    return JSONResponse(
        status_code=402,
        headers=headers,
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": PAYMENT_REQUIRED,
                "message": f"Payment required: {decision.reason}",
                "data": generate_payment_required_response(
                    settings.x402_price, decision.reason
                ),
            },
        },
    )


async def _call_tool(request: Request, request_id: Any, params: dict[str, Any]) -> Response:
    """Exécute `validate_address` : validation des arguments, puis paiement."""
    from pydantic import ValidationError

    name = params.get("name")
    if name != TOOL_NAME:
        return _error(request_id, INVALID_PARAMS, f"outil inconnu : {name!r}")

    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(request_id, INVALID_PARAMS, "arguments doit etre un objet JSON")

    try:
        body = ValidationRequest.model_validate(arguments)
    except ValidationError as exc:
        # Une entrée mal formée est gratuite : on ne facture pas une erreur 4xx.
        return _error(
            request_id,
            INVALID_PARAMS,
            "arguments invalides",
            {"errors": exc.errors(include_url=False)},
        )

    decision = payment_result(request)
    if not decision.paid:
        return _payment_error(request_id, request, decision)

    result, key = perform_validation(body.address.model_dump())
    if decision.transaction and not record_transaction(
        decision.transaction, settings.x402_price, key
    ):
        # D41 : meme regle qu'en HTTP. L'outil n'est pas servi, l'incident est
        # compte, et l'appelant recoit le hash du reglement qu'il a paye pour
        # pouvoir le rapprocher (meme statut 500 que /validate, mais en JSON-RPC).
        metrics.incr("ledger_write_failures_total")
        return _error(
            request_id,
            INTERNAL_ERROR,
            "paiement non enregistre : outil non servi",
            {
                "detail": "l'inscription au registre a echoue ; encaissement a rapprocher",
                "transaction": decision.transaction,
                "price": settings.x402_price,
            },
            status=500,
        )

    audience.noter(mode=decision.protocol, amount_usdc=None if settings.test_mode or not decision.transaction else settings.x402_price)
    payload = build_response(result, key, decision.transaction).model_dump()
    return _result(
        request_id,
        {
            "content": [
                {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
            ],
            "structuredContent": payload,
            "isError": False,
        },
    )


async def _dispatch(request: Request, message: dict[str, Any]) -> Response | None:
    """Route un message JSON-RPC. `None` = notification (pas de réponse)."""
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(
            message.get("id"),
            INVALID_REQUEST,
            'requete JSON-RPC invalide : jsonrpc="2.0" et method attendus',
        )

    method = str(message["method"])
    request_id = message.get("id")
    # D67 : découvrir (initialize, tools/list) et appeler l'outil se comptent à part.
    # ponytail: dans un lot JSON-RPC, seule la dernière méthode reste sur la ligne.
    audience.noter(method=method[:40])
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, INVALID_PARAMS, "params doit etre un objet JSON")

    if request_id is None and method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _result(request_id, _initialize(params))
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [tool_definition()]})
    if method == "tools/call":
        return await _call_tool(request, request_id, params)
    if method == "resources/list":
        return _result(request_id, {"resources": []})
    if method == "resources/templates/list":
        return _result(request_id, {"resourceTemplates": []})
    if method == "prompts/list":
        return _result(request_id, {"prompts": []})
    if method == "logging/setLevel":
        return _result(request_id, {})
    return _error(request_id, METHOD_NOT_FOUND, f"methode inconnue : {method}")


@router.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    """Transport « streamable HTTP » : un message JSON-RPC par POST."""
    raw = await request.body()
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error(None, PARSE_ERROR, "corps de requete non JSON")

    if payload is None:
        return _error(None, INVALID_REQUEST, "corps de requete vide")
    if not isinstance(payload, (dict, list)):
        return _error(None, INVALID_REQUEST, "objet JSON-RPC attendu")

    if isinstance(payload, list):
        if not payload:
            return _error(None, INVALID_REQUEST, "lot JSON-RPC vide")
        answers: list[dict[str, Any]] = []
        for message in payload:
            if not isinstance(message, dict):
                answers.append(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": INVALID_REQUEST,
                            "message": "entree de lot invalide",
                        },
                    }
                )
                continue
            answer = await _dispatch(request, message)
            if answer is not None:
                answers.append(json.loads(answer.body))
        if not answers:
            return Response(
                status_code=202, headers={"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}
            )
        return _json(answers)

    answer = await _dispatch(request, payload)
    if answer is None:
        return Response(
            status_code=202, headers={"MCP-Protocol-Version": MCP_PROTOCOL_VERSION}
        )
    return answer


@router.get("/mcp")
async def mcp_get() -> Response:
    """Pas de flux SSE : le refus est explicite, pas un 404 ambigu."""
    return Response(
        status_code=405,
        media_type="application/json",
        headers={"Allow": "POST", "MCP-Protocol-Version": MCP_PROTOCOL_VERSION},
        content=json.dumps(
            {
                "error": "method not allowed",
                "detail": (
                    "Ce serveur MCP est sans etat : envoyer les messages JSON-RPC "
                    "en POST /mcp. Aucun flux SSE n'est servi."
                ),
            }
        ),
    )


#: Dernière révision de la carte de découverte. À mettre à jour quand l'offre
#: change : un `last_updated` figé devient une information fausse.
CARD_LAST_UPDATED = "2026-10-11T00:00:00Z"


@router.get("/.well-known/mcp-server")
async def mcp_server_card() -> Response:
    """Carte de découverte d'un serveur MCP (draft-serra-mcp-discovery-uri-04).

    Champs publiés, tels que définis par le brouillon et repris de son
    implémentation de référence : `mcp_version`, `name`, `description`,
    `endpoint`, `transport`, `auth`, `capabilities`, `categories`, `languages`,
    `coverage`, `contact`, `docs`, `last_updated`, `cache_ttl`, `crawl`.

    La carte est **servie par l'application** : elle ne peut donc pas décrire un
    endpoint éteint, puisqu'elle disparaît avec lui (D28). L'offre x402 (réseau,
    jeton, montant, destination) n'est pas recopiée ici : l'agent la reçoit dans
    `data.accepts` du premier `402`, ce qui évite deux vérités à maintenir.

    `trust_class`, `compliance` et `logging` sont **volontairement omis** : le
    brouillon impose des sous-champs par classe de confiance que ce dépôt ne peut
    pas prouver, et publier une classe non tenue serait une fausse déclaration.
    """
    card = {
        "mcp_version": MCP_PROTOCOL_VERSION,
        "name": "Address Validator API",
        "description": (
            "Validation d'adresses de livraison : parsing, standardisation, "
            "verification fournisseur, geocodage, score de livrabilite. Un seul "
            "outil, paye a l'appel par le protocole x402 (USDC sur Base)."
        ),
        "endpoint": settings.mcp_public_url,
        "transport": "http",
        "auth": {"required": True, "methods": ["x402"]},
        "capabilities": ["tools"],
        "categories": ["address", "validation", "logistics", "data"],
        "languages": ["fr", "en"],
        # Le fournisseur est optimise pour les Etats-Unis : annoncer « WW »
        # laisserait croire a une couverture mondiale qui n'existe pas.
        "coverage": "US",
        "contact": settings.contact_email,
        "docs": f"{settings.public_base_url.rstrip('/')}/llms.txt",
        "last_updated": CARD_LAST_UPDATED,
        "cache_ttl": 300,
        "crawl": True,
    }
    return JSONResponse(
        content=card,
        headers={
            "Cache-Control": "public, max-age=300",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        },
    )
