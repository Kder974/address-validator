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

Depuis D78, le paiement suit le **transport MCP x402 v2**
(`specs/transports-v2/mcp.md`) : le client met son `PaymentPayload` dans
`params._meta["x402/payment"]`, un appel sans paiement rend un **résultat
d'outil** `isError: true` dont `structuredContent` est le `PaymentRequired` v2
(et `content[0].text` le même document en JSON), et le règlement revient dans
`result._meta["x402/payment-response"]`. Un `402` JSON-RPC a été la première
forme (D37) ; il rendait l'outil illisible pour les clients x402 MCP, qui lisent
un résultat, pas un statut HTTP. La compatibilité est gardée : un client v1
paie toujours par l'en-tête `X-PAYMENT` et reçoit son reçu d'en-tête.

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
    payment_result,
    record_transaction,
)
from app.routes.metrics import metrics
from app.routes.validate import build_response, perform_validation
from app.schemas import ValidationRequest, ValidationResponse
from app.x402 import (
    MCP_PAYMENT_META_KEY,
    MCP_PAYMENT_RESPONSE_META_KEY,
    InvalidPayment,
    PaymentDecision,
    parse_payment_payload,
    payment_required_mcp,
    settle_payment,
    settlement_response,
)

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
    "validation (0.02 USDC sur Base, reglee en x402 v2 dans "
    "params._meta['x402/payment'], ou par l'en-tete X-PAYMENT d'un client v1)."
)

# Codes d'erreur JSON-RPC 2.0. Depuis D78, un paiement absent ou refusé n'est
# plus une erreur JSON-RPC : c'est un résultat d'outil `isError` qui porte
# l'offre (le transport MCP x402 v2 se lit ainsi). Ces codes restent ceux des
# erreurs de protocole et du registre (D41).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


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


def _json(payload: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    """Réponse JSON-RPC : jamais de `Cache-Control` implicite, jamais de SSE."""
    entetes = {
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Cache-Control": "no-store",
    }
    if headers:
        # Reçu de règlement en-tête d'un client v1 (D34) : conservé à l'identique.
        entetes.update(headers)
    return JSONResponse(status_code=status, content=payload, headers=entetes)


def _result(request_id: Any, result: Any, headers: dict[str, str] | None = None) -> JSONResponse:
    return _json({"jsonrpc": "2.0", "id": request_id, "result": result}, headers=headers)


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
            "coute 0.02 USDC sur le reseau Base, regle par le protocole x402. "
            "Client x402 v2 (MCP) : mettre le PaymentPayload dans "
            "params._meta[\"x402/payment\"] ; le recu revient dans "
            "result._meta[\"x402/payment-response\"]. Client v1 : presenter "
            "l'en-tete X-PAYMENT sur la requete HTTP. Un appel sans paiement "
            "rend un resultat d'outil isError=true dont structuredContent est "
            "le PaymentRequired v2 (reseau eip155:8453, montant, adresse de "
            "destination, jeton). Decouvrir (tools/list) est gratuit."
        ),
    }


def _paiement_meta(valeur: Any) -> PaymentDecision:
    """Règle un paiement reçu dans `params._meta["x402/payment"]` (D78).

    Le transport MCP est **v2** : le payload doit déclarer `x402Version: 2` (le
    SDK officiel n'envoie que cela). Un payload v1 est refusé explicitement
    plutôt que servi avec un reçu v2 qui ne correspondrait pas à sa signature.
    """
    if not isinstance(valeur, dict):
        return PaymentDecision(
            paid=False,
            reason="champ _meta['x402/payment'] illisible : objet JSON attendu",
            protocol="x402",
        )
    if valeur.get("x402Version") != 2:
        return PaymentDecision(
            paid=False,
            reason="le transport MCP attend un PaymentPayload x402 v2",
            protocol="x402",
        )
    try:
        payload = parse_payment_payload(valeur)
    except InvalidPayment as exc:
        return PaymentDecision(paid=False, reason=str(exc), protocol="x402")
    return settle_payment(payload, settings.x402_price)


def _refus_paiement(request_id: Any, request: Request, decision) -> JSONResponse:
    """Refus de paiement du transport MCP v2 : un **résultat d'outil** `isError`.

    Le client x402 MCP lit `result.isError`, puis le `PaymentRequired` dans
    `result.structuredContent` (ou `content[0].text`, même document) : un `402`
    HTTP lui donnerait une erreur de transport, pas une offre à payer. C'est la
    forme que publie `specs/transports-v2/mcp.md` et que produit le SDK officiel.

    Un paiement **présenté puis refusé** joint en plus son reçu d'échec
    (`x402/payment-response`), comme le fait le SDK pour un règlement refusé ;
    un appel sans aucun moyen de paiement ne joint rien (il n'y a pas eu de
    règlement).
    """
    log_unpaid_attempt(request, settings.x402_price, decision.reason)
    document = payment_required_mcp(
        TOOL_NAME,
        settings.x402_price,
        None if decision.protocol == "none" else decision.reason,
    )
    if decision.protocol != "none":
        document[MCP_PAYMENT_RESPONSE_META_KEY] = settlement_response(
            None, None, success=False, error_reason=decision.reason
        )
    return _json(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "isError": True,
                "structuredContent": document,
                "content": [
                    {"type": "text", "text": json.dumps(document, ensure_ascii=False)}
                ],
            },
        },
        headers=decision.response_headers,
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

    # D78 : le transport MCP v2 porte le paiement dans `params._meta` ; l'en-tête
    # (X-PAYMENT / PAYMENT-SIGNATURE / X-Payment-Proof) reste accepté (D34/D37).
    meta = params.get("_meta")
    paiement = meta.get(MCP_PAYMENT_META_KEY) if isinstance(meta, dict) else None
    decision = _paiement_meta(paiement) if paiement is not None else payment_result(request)
    if not decision.paid:
        return _refus_paiement(request_id, request, decision)

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
    tool_result: dict[str, Any] = {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
        ],
        "structuredContent": payload,
        "isError": False,
    }
    if decision.protocol == "x402":
        # D78 : règlement rendu dans `result._meta` (SettlementResponse, réseau CAIP-2).
        tool_result["_meta"] = {
            MCP_PAYMENT_RESPONSE_META_KEY: settlement_response(
                decision.transaction, decision.payer
            )
        }
    return _result(request_id, tool_result, headers=decision.response_headers)


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
