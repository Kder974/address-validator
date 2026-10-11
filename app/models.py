"""Modèles ORM SQLAlchemy — miroir du DDL de `app/database.py:init_db()`.

Le DDL de référence (spec Phase 4) est écrit en SQL brut dans `database.py`.
Ces modèles existent pour typer les écritures Python et rester alignés sur la
table réelle. Toute modification de schéma doit être répercutée des deux côtés
(décision D13, voir DECISIONS.md).
"""

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class AddressValidation(Base):
    __tablename__ = "address_validations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(64), unique=True, nullable=False, index=True)
    raw_address = Column(JSONB, nullable=False)
    standardized = Column(JSONB, nullable=False)
    validated = Column(Boolean, nullable=False)
    deliverability = Column(String(20))
    confidence = Column(Float)
    latitude = Column(Float)
    longitude = Column(Float)
    created_at = Column(DateTime, server_default=func.current_timestamp(), index=True)
    transaction_id = Column(String(100))


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tx_hash = Column(String(128), unique=True, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(10), nullable=False, default="USDC")
    address = Column(String(255))
    created_at = Column(DateTime, server_default=func.current_timestamp(), index=True)
