"""Configuration applicative, chargée depuis l'environnement / .env.

Conforme à la spec (Phase 2). Seule addition : `extra = "ignore"` afin qu'une
variable d'environnement non déclarée (ex. celles du VPS) ne fasse pas échouer
le démarrage. Décision D8 (voir DECISIONS.md).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # API
    # Défaut `127.0.0.1` (et non `0.0.0.0`) : le service est publié par le reverse
    # proxy (D21), pas directement sur Internet. Cohérent avec la ligne ExecStart
    # du service systemd (`address-validator.service`).
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_secret_key: str = "change_me"

    # Base de données
    database_url: str = "postgresql://user:password@localhost:5432/address_validator"
    redis_url: str = "redis://localhost:6379/0"

    # Fournisseur de validation
    smarty_auth_id: str = ""
    smarty_auth_token: str = ""

    # Paiement x402
    cdp_api_key_id: str = ""
    cdp_api_key_secret: str = ""
    cdp_wallet_address: str = ""
    x402_price: float = 0.02

    # Vérification on-chain du paiement (D27). Désactivée par défaut : la preuve
    # HMAC de D15 reste celle du service. Quand elle est activée, le reçu de la
    # transaction citée dans `X-Payment-Proof` doit contenir un transfert USDC
    # confirmé vers `cdp_wallet_address` (app/onchain.py) ; tout échec est
    # fail-closed. `base_rpc_url` est un nœud JSON-RPC public de Base (chaîne
    # 8453) ; `x402_usdc_contract` est le contrat USDC natif de Base.
    x402_onchain_verify: bool = False
    base_rpc_url: str = "https://mainnet.base.org"
    x402_usdc_contract: str = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    x402_min_confirmations: int = 1

    # Environnement
    environment: str = "development"
    log_level: str = "INFO"

    # Contact de l'exploitant : publie dans GET /manifest et dans l'en-tete
    # User-Agent du geocodeur (politique d'usage Nominatim). Surchargeable par
    # la variable CONTACT_EMAIL du .env.
    contact_email: str = "Kdera597420@gmail.com"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
