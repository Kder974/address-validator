"""Accès PostgreSQL.

Conforme à la spec (Phase 4). Ajouts documentés (D14) :
  * `connect_timeout` court : un /health ne doit pas pendre si la base est
    injoignable ;
  * `pool_pre_ping` : recycle les connexions coupées ;
  * `init_db()` est idempotent et appelé au démarrage, mais son échec est
    non bloquant (l'API reste up et le signale sur /health) ;
  * table `payment_transactions` ajoutée pour la couche paiement (D11) ;
  * colonne `test_mode` et son `ALTER TABLE ... IF NOT EXISTS` ajoutées pour
    séparer le revenu réel des essais de recette (D40).
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 2},
)
SessionLocal = sessionmaker(bind=engine)

DDL = """
CREATE TABLE IF NOT EXISTS address_validations (
    id SERIAL PRIMARY KEY,
    cache_key VARCHAR(64) UNIQUE NOT NULL,
    raw_address JSONB NOT NULL,
    standardized JSONB NOT NULL,
    validated BOOLEAN NOT NULL,
    deliverability VARCHAR(20),
    confidence FLOAT,
    latitude FLOAT,
    longitude FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    transaction_id VARCHAR(100)
);
CREATE INDEX IF NOT EXISTS idx_cache_key ON address_validations(cache_key);
CREATE INDEX IF NOT EXISTS idx_created_at ON address_validations(created_at);

CREATE TABLE IF NOT EXISTS payment_transactions (
    id SERIAL PRIMARY KEY,
    tx_hash VARCHAR(128) UNIQUE NOT NULL,
    amount FLOAT NOT NULL,
    currency VARCHAR(10) NOT NULL DEFAULT 'USDC',
    address VARCHAR(255),
    test_mode BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tx_hash ON payment_transactions(tx_hash);

-- D40 : un paiement encaisse et un essai de recette sont deux choses
-- differentes. La colonne `test_mode` le dit dans la donnee, pas dans un
-- commentaire : le revenu publie (real_*) exclut les essais, le registre les
-- conserve (total_*), et l'ecart entre les deux est toujours explicable.
CREATE INDEX IF NOT EXISTS idx_payment_test_mode ON payment_transactions(test_mode);

-- Migration idempotente : une base deja en service recoit la colonne au
-- demarrage (init_db est appele par le lifespan), une base neuve l'a ci-dessus.
ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS test_mode BOOLEAN NOT NULL DEFAULT FALSE;
"""


def check_db() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as e:
        return f"error: {e}"


def init_db() -> bool:
    """Crée les tables si nécessaire. Renvoie True en cas de succès."""
    try:
        with engine.connect() as conn:
            conn.execute(text(DDL))
            conn.commit()
        return True
    except Exception:
        return False
