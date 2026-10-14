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

-- D44 : comptes clients (campagne commerciale). Trois remarques de conception.
--
--   * `api_key_hash` + `api_key_prefix` remplacent la colonne `api_key` de la
--     spec : une cle API stockee en clair est un mot de passe en clair. La base
--     ne garde que l'empreinte SHA-256 (index de recherche) et un prefixe
--     affichable ; la cle n'existe qu'entre sa generation et son affichage, une
--     fois. Une cle perdue se regenere, elle ne se relit pas.
--   * `password_hash` contient une empreinte bcrypt (sel inclus) : le mot de
--     passe en clair n'est jamais ecrit, ni en base, ni dans un journal.
--   * `plan` et `trial_ends_at` vivent dans la donnee : « essai gratuit de
--     14 jours » doit etre verifiable, pas suppose.
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    shop_name VARCHAR(255) NOT NULL,
    platform VARCHAR(40) NOT NULL,
    api_key_hash VARCHAR(64) UNIQUE NOT NULL,
    api_key_prefix VARCHAR(16) NOT NULL,
    plan VARCHAR(20) NOT NULL DEFAULT 'trial',
    trial_ends_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS idx_customers_api_key_hash ON customers(api_key_hash);

-- D44 : une validation servie a un compte. C'est la source des statistiques du
-- tableau de bord client (validations, adresses invalides, economies estimees) :
-- un compteur tenu a la main deriverait desormais du service rendu.
-- D62 : connexions ouvertes a l'espace client. La base garde l'empreinte
-- SHA-256 du jeton du cookie, jamais le jeton ; fermer une session = effacer sa
-- ligne. `expires_at` borne la session (12 h par defaut, SESSION_HOURS).
CREATE TABLE IF NOT EXISTS customer_sessions (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    token_hash VARCHAR(64) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_customer_sessions_customer ON customer_sessions(customer_id);
CREATE TABLE IF NOT EXISTS customer_validations (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    cache_key VARCHAR(64),
    validated BOOLEAN NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_customer_validations_customer
    ON customer_validations(customer_id);
CREATE INDEX IF NOT EXISTS idx_customer_validations_created_at
    ON customer_validations(created_at);
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
