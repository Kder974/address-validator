"""Modèles ORM SQLAlchemy — miroir du DDL de `app/database.py:init_db()`.

Le DDL de référence (spec Phase 4) est écrit en SQL brut dans `database.py`.
Ces modèles existent pour typer les écritures Python et rester alignés sur la
table réelle. Toute modification de schéma doit être répercutée des deux côtés
(décision D13, voir DECISIONS.md).
"""

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, func
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
    #: D40 : vrai pour un essai de recette (jamais du revenu client). La colonne
    #: existe aussi dans le DDL de `app/database.py` (D13 : les deux cotes).
    test_mode = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.current_timestamp(), index=True)


class Customer(Base):
    """Compte client (décision D44) — miroir du DDL de `app/database.py`.

    La colonne demandée par la spec s'appelait `api_key` : elle s'appelle ici
    `api_key_hash` plus `api_key_prefix`, parce qu'une clé API stockée en clair
    est un mot de passe en clair. L'empreinte identifie la clé présentée, le
    préfixe permet de la reconnaître dans une liste (« la clé qui commence par
    `sk_live_ab12` »), et la clé elle-même n'existe qu'au moment où on l'affiche.
    """

    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    shop_name = Column(String(255), nullable=False)
    platform = Column(String(40), nullable=False)
    api_key_hash = Column(String(64), unique=True, nullable=False, index=True)
    api_key_prefix = Column(String(16), nullable=False)
    plan = Column(String(20), nullable=False, default="trial")
    trial_ends_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.current_timestamp(), index=True)


class CustomerSession(Base):
    """Une connexion ouverte à l'espace client (D62).

    La base garde l'**empreinte** SHA-256 du jeton, jamais le jeton : une copie de
    la base ne permet d'ouvrir aucune session. Fermer une session, c'est effacer
    sa ligne ; une ligne échue ne vaut plus rien, même si le cookie revient.
    """

    __tablename__ = "customer_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, server_default=func.current_timestamp())
    expires_at = Column(DateTime, nullable=False)


class CustomerValidation(Base):
    """Une validation servie à un compte (D44) : source des statistiques.

    Sans cette table, le tableau de bord d'un client n'aurait que des zéros
    décoratifs. Chaque validation servie par une clé API laisse une ligne :
    c'est ce qui rend « nombre de validations », « adresses invalides détectées »
    et « économies estimées » calculables au lieu d'inventés.
    """

    __tablename__ = "customer_validations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    cache_key = Column(String(64), index=True)
    validated = Column(Boolean, nullable=False)
    created_at = Column(DateTime, server_default=func.current_timestamp(), index=True)
