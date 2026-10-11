"""POST /validate — cœur payant de l'API.

Conforme à la spec (Phase 3) avec un ajout : le **cache Redis** (décision D5).
La spec exigeait « le cache réduit les appels SmartyStreets de 80 % » sans
brancher le cache dans la route ; on l'intègre donc ici.

Clé de cache = SHA-256 de l'adresse brute normalisée en JSON trié, ce qui
permet de répondre avant tout appel réseau.
"""

import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.cache import get_cached_result, store_in_cache
from app.config import get_settings
from app.payment import (
    generate_payment_required_response,
    record_transaction,
    verify_payment,
)
from app.routes.metrics import metrics
from app.schemas import AddressOutput, ValidationRequest, ValidationResponse
from app.validation import validate_full

router = APIRouter()
settings = get_settings()


def cache_key(address: dict) -> str:
    payload = json.dumps(address, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


@router.post("/validate", response_model=ValidationResponse)
async def validate_address(request: Request, body: ValidationRequest):
    metrics.incr("requests_total")

    # --- Couche paiement : 402 tant qu'aucune preuve valide n'est fournie ---
    if not verify_payment(request):
        metrics.incr("unpaid_requests")
        return JSONResponse(
            status_code=402,
            content=generate_payment_required_response(settings.x402_price),
        )

    metrics.incr("paid_requests")

    raw = body.address.model_dump()
    key = cache_key(raw)

    # --- Couche cache (D5) ---
    cached = get_cached_result(key)
    if cached is not None:
        metrics.incr("cache_hits")
        result = cached
    else:
        metrics.incr("cache_misses")
        result = validate_full(raw)
        store_in_cache(key, result)
        if result.get("degraded"):
            metrics.incr("provider_errors")
            metrics.incr("degraded_results")

    transaction_id = request.headers.get("X-Transaction-Id")
    if transaction_id:
        record_transaction(transaction_id, settings.x402_price, key)

    output = AddressOutput(
        validated=result["validated"],
        standardized=result["standardized"],
        latitude=result["latitude"],
        longitude=result["longitude"],
        deliverability=result["deliverability"],
        confidence=result["confidence"],
        degraded=result["degraded"],
        cache_key=key,
    )

    return ValidationResponse(
        result=output,
        price_paid=settings.x402_price,
        transaction_id=transaction_id,
    )
