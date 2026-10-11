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

    # Recette contre revenu (D40). `TEST_MODE=true` marque `test_mode` les
    # paiements enregistres pendant une recette de l'exploitant : ils restent au
    # registre (tracabilite, audit) mais sont exclus de `real_revenue_usdc` et de
    # `real_validations_paid` publies par /metrics/revenue. Defaut `false` : un
    # service en production compte ce qu'il encaisse.
    test_mode: bool = False
    # Instant de bascule (UTC, ISO 8601) : `scripts/migrate_test_mode.py` marque
    # toute transaction *anterieure* a cette date — les essais de recette du
    # 11/10/2026 (06:30 et 06:56 UTC). Le seuil vit ici, date et modifiable,
    # jamais enfoui dans un script.
    test_mode_cutoff_utc: str = "2026-10-11T12:00:00+00:00"

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

    # Paiement x402 **standard** (D34 a D36, app/x402.py). Le protocole v1 est
    # decrit par `specs/transports-v1/http.md` du depot coinbase/x402 :
    #   * corps 402 = {"x402Version": 1, "error": ..., "accepts": [ ... ]} ;
    #   * le client renvoie sa signature dans l'en-tete `X-PAYMENT` (base64) ;
    #   * la reponse porte `X-PAYMENT-RESPONSE` (base64) avec le hash reglé.
    #
    # Reseau annonce : le nom historique v1 ("base") ; la variante CAIP-2
    # ("eip155:8453") est acceptee en entree pour les clients v2.
    x402_network: str = "base"
    x402_chain_id: int = 8453
    # Domaine EIP-712 du jeton : `name`/`version` doivent etre ceux du contrat,
    # sinon la signature d'un client honnete est rejetee. Valeurs du FiatTokenV2
    # (USDC) sur Base, verifiables on-chain : `scripts/test_x402_payment.py
    # --check-domain`. Un ecart est un refus, jamais un service rendu.
    x402_domain_name: str = "USD Coin"
    x402_domain_version: str = "2"
    # Facilitateur : /verify (droit de payer) et /settle (encaissement). Defaut
    # = celui de Coinbase (CDP), authentifie par un JWT signe avec la cle CDP.
    x402_facilitator_url: str = "https://api.cdp.coinbase.com/platform/v2/x402"
    x402_facilitator_verify: bool = True
    # `true` (defaut) : un paiement n'est accepte que *regle* par le
    # facilitateur. `false` rendrait le service contre une autorisation signee
    # non encaissee : reserve au diagnostic local, jamais a la production.
    x402_require_settlement: bool = True
    # URL publique du service, citee dans `accepts[].resource` et dans la carte
    # MCP : un agent doit voir exactement l'URL qu'il a appelee.
    public_base_url: str = "https://kder-ai.com"
    mcp_public_url: str = "https://kder-ai.com/mcp"

    # Observabilité de la découverte (D30). Chaque requête refusée en 402 est
    # tracée pour mesurer si l'API est *trouvée* (et par qui), sans jamais
    # stocker l'adresse validée. L'IP est **pseudonymisée** par défaut
    # (sha256 + sel) : le dépôt pseudonymise déjà les adresses (PRIVACY.md §1),
    # la même minimisation s'applique au client réseau.
    discovery_log_enabled: bool = True
    # Chemin du journal JSONL. Vide = journalisation applicative seule (journald
    # en production) : aucune écriture disque n'est imposée à l'exploitant.
    discovery_log_path: str = ""
    # False (défaut) = IP pseudonymisée ; true = IP en clair, pour une enquête
    # d'abus limitée dans le temps (à justifier, cf. PRIVACY.md §3).
    discovery_log_raw_ip: bool = False
    # Sel dédié. Vide = dérivé de API_SECRET_KEY (dépannage) ; un sel propre est
    # préférable car il survit à une rotation du secret.
    discovery_log_salt: str = ""
    # Purge automatique : le rapport quotidien supprime les entrées plus vieilles
    # que ce délai. Une conservation annoncée mais non appliquée serait un
    # mensonge (PRIVACY.md §3).
    discovery_log_retention_days: int = 30

    # Tableau de bord d'exploitation (D32). Mot de passe vide = endpoint
    # *fermé* (503), jamais ouvert : un tableau de bord public exposerait le
    # chiffre d'affaires et les dernières transactions.
    dashboard_user: str = "kder"
    dashboard_password: str = ""

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
