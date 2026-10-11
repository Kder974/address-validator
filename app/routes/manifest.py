"""GET /manifest — description du service pour la découverte x402 Bazaar / MCP.

Conforme à la spec (Phase 3).
"""

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter()
settings = get_settings()


@router.get("/manifest")
async def manifest():
    return {
        "name": "AddressValidator",
        "description": "Valide les adresses de livraison et retourne un score de livrabilité",
        "version": "1.0.0",
        "category": "data",
        "tags": ["address", "validation", "delivery", "logistics", "ecommerce"],
        "price": settings.x402_price,
        "currency": "USDC",
        "protocol": "x402",
        "endpoints": {
            "validate": {
                "method": "POST",
                "path": "/validate",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "address": {
                            "type": "object",
                            "properties": {
                                "street": {"type": "string"},
                                "city": {"type": "string"},
                                "state": {"type": "string"},
                                "zip": {"type": "string"},
                                "country": {"type": "string", "default": "US"},
                            },
                        }
                    },
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "validated": {"type": "boolean"},
                        "standardized": {"type": "object"},
                        "latitude": {"type": "number"},
                        "longitude": {"type": "number"},
                        "deliverability": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                },
            }
        },
        "contact": {
            "email": settings.contact_email,
        },
        "license": "MIT",
    }
