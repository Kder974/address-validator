"""Schémas Pydantic (contrats d'entrée/sortie de l'API).

Conforme à la spec (Phase 2).
"""

from typing import Optional

from pydantic import BaseModel, Field


class AddressInput(BaseModel):
    street: str = Field(..., description="Numéro et nom de rue")
    city: str = Field(..., description="Ville")
    state: str = Field(..., description="État ou région")
    zip: str = Field(..., description="Code postal")
    country: str = Field(default="US", description="Code pays ISO 2 lettres")


class AddressOutput(BaseModel):
    validated: bool
    standardized: dict
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    deliverability: str
    confidence: float
    degraded: bool = False
    cache_key: str


class ValidationRequest(BaseModel):
    address: AddressInput


class ValidationResponse(BaseModel):
    result: AddressOutput
    price_paid: float
    transaction_id: Optional[str] = None
