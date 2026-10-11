"""POST /validate — cœur payant de l'API.

Conforme à la spec (Phase 3) avec un ajout : le **cache Redis** (décision D5).
La spec exigeait « le cache réduit les appels SmartyStreets de 80 % » sans
brancher le cache dans la route ; on l'intègre donc ici.

Deux ajouts du chantier de découvrabilité :

  D29  le paiement accepté est enregistré sous le hash de sa **preuve**
       (`X-Payment-Proof`), et non plus seulement sous un en-tête maison
       optionnel : le registre durable compte ainsi ce qui est réellement payé.
  D30  une requête refusée en `402` est journalisée (IP pseudonymisée) : c'est
       la seule façon de mesurer si l'API est *trouvée*.

Ajout du Lot B :

  D34  le corps du `402` est un `PaymentRequired` x402 v1 (`x402Version` +
       `accepts[]`) et un paiement x402 réglé reçoit `X-PAYMENT-RESPONSE`. Le
       registre (D29) est désormais identifié par le hash du **règlement** pour
       un paiement x402 : ce qui est compté est ce qui a été encaissé.

Clé de cache = SHA-256 de l'adresse brute normalisée en JSON trié, ce qui
permet de répondre avant tout appel réseau.
"""

import hashlib
import json

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.cache import get_cached_result, store_in_cache
from app.config import get_settings
from app.observability import log_unpaid_attempt
from app.payment import (
    PAYMENT_RESPONSE_HEADER,
    PROOF_HEADER,
    generate_payment_required_response,
    payment_result,
    record_transaction,
    tx_hash_from_proof,
)
from app.routes.metrics import metrics
from app.schemas import AddressOutput, ValidationRequest, ValidationResponse
from app.validation import validate_full

router = APIRouter()
settings = get_settings()


def cache_key(address: dict) -> str:
    payload = json.dumps(address, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def perform_validation(raw: dict) -> tuple[dict, str]:
    """Pipeline de validation **partagé** (route HTTP et outil MCP).

    Renvoie `(résultat, clé de cache)`. Les noms appelés ici sont des globaux du
    module (`validate_full`, `get_cached_result`, `store_in_cache`) : les oracles
    et les tests les remplacent par `mock.patch` sans toucher au code.
    """
    key = cache_key(raw)
    cached = get_cached_result(key)
    if cached is not None:
        metrics.incr("cache_hits")
        return cached, key

    metrics.incr("cache_misses")
    result = validate_full(raw)
    store_in_cache(key, result)
    if result.get("degraded"):
        metrics.incr("provider_errors")
        metrics.incr("degraded_results")
    return result, key


def build_response(
    result: dict, key: str, transaction_id: str | None = None
) -> ValidationResponse:
    """Réponse publique, construite à partir du résultat interne."""
    return ValidationResponse(
        result=AddressOutput(
            validated=result["validated"],
            standardized=result["standardized"],
            latitude=result["latitude"],
            longitude=result["longitude"],
            deliverability=result["deliverability"],
            confidence=result["confidence"],
            degraded=result["degraded"],
            cache_key=key,
        ),
        price_paid=settings.x402_price,
        transaction_id=transaction_id,
    )


@router.post("/validate", response_model=ValidationResponse)
async def validate_address(request: Request, body: ValidationRequest, response: Response):
    metrics.incr("requests_total")

    # --- Couche paiement : 402 tant qu'aucun paiement valide n'est fourni ---
    # D34 : deux voies sont acceptées — x402 standard (`X-PAYMENT`, signature
    # EIP-3009 + règlement) et la preuve HMAC historique (`X-Payment-Proof`,
    # D15/D27). `payment_result` choisit, `/validate` ne décide pas du protocole.
    decision = payment_result(request)
    if not decision.paid:
        metrics.incr("unpaid_requests")
        # D30 : tracer la tentative non payée. L'entrée ne contient que des
        # métadonnées de requête (aucune adresse, IP pseudonymisée par défaut),
        # et un journal indisponible ne doit jamais casser la réponse.
        log_unpaid_attempt(request, settings.x402_price, decision.reason)
        headers = {}
        if decision.response_header:
            # Spec x402 v1 : l'échec de paiement est aussi porté par
            # `X-PAYMENT-RESPONSE` (`success: false`, `errorReason`).
            headers[PAYMENT_RESPONSE_HEADER] = decision.response_header
        return JSONResponse(
            status_code=402,
            headers=headers,
            content=generate_payment_required_response(settings.x402_price, decision.reason),
        )

    metrics.incr("paid_requests")
    if decision.protocol == "x402":
        metrics.incr("x402_payments")

    raw = body.address.model_dump()

    # D29 : c'est la preuve de paiement qui identifie ce qui a été payé. En x402,
    # c'est le hash du **règlement** (donc ce qui a réellement été encaissé) ;
    # en HMAC, le hash cité par la preuve ; `X-Transaction-Id` reste un repli
    # d'outillage. `ON CONFLICT DO NOTHING` côté SQL rend l'écriture idempotente :
    # une preuve rejouée ne compte pas deux fois.
    transaction_id = request.headers.get("X-Transaction-Id") or decision.transaction
    ledger_key = (
        decision.transaction
        or tx_hash_from_proof(request.headers.get(PROOF_HEADER))
        or transaction_id
    )

    result, key = perform_validation(raw)
    if ledger_key:
        record_transaction(ledger_key, settings.x402_price, key)

    if decision.response_header:
        response.headers[PAYMENT_RESPONSE_HEADER] = decision.response_header

    return build_response(result, key, transaction_id)
