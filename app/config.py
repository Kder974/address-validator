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

    # Vérification on-chain du paiement (D27). **Active par défaut** depuis le
    # durcissement W1 (12/10/2026, AUDIT_TRAFIC_VPS.md §5) : la preuve HMAC de
    # D15 n'est qu'un secret partagé, forgeable par qui le connaît, et le défaut
    # `false` laissait un déploiement qui oublie la variable servir le service
    # sans encaissement vérifiable. Quand elle est active, le reçu de la
    # transaction citée dans `X-Payment-Proof` doit contenir un transfert USDC
    # confirmé vers `cdp_wallet_address` (app/onchain.py) ; tout échec est
    # fail-closed. La couper (`X402_ONCHAIN_VERIFY=false`) n'est légitime que
    # dans une **recette déclarée** (`TEST_MODE=true`) : hors recette, la preuve
    # à secret partagé est alors refusée (`app/payment.py`, W1).
    # `base_rpc_url` est un nœud JSON-RPC public de Base (chaîne 8453) ;
    # `x402_usdc_contract` est le contrat USDC natif de Base.
    x402_onchain_verify: bool = True
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

    # Pages publiques de la campagne commerciale (D43 : `/pricing`, puis
    # `/signup` et `/validate-form`). **Sans effet** depuis le dessin validé des
    # tarifs (six langues) : `/pricing` n'a plus de section témoignages, quelle
    # que soit la valeur. Les seuls qui existaient étaient des exemples, et
    # présenter des retours inventés comme réels serait une pratique commerciale
    # trompeuse. Le réglage reste lu pour qu'un `.env` existant reste valide.
    pricing_show_testimonials: bool = False

    # CRM de prospection (Tâche 7, `app/routes/prospection.py`). Chemin de la base
    # SQLite tenue par `prospection/scripts/import_prospects.py`. Le dossier
    # `prospection/` n'est pas déployé avec le service : sur le VPS, `/prospection`
    # répond donc `503` avec la commande à lancer, jamais une page vide. Même
    # garde que `/dashboard` (mot de passe absent = route fermée).
    prospection_db: str = "prospection/data/prospects.db"

    # Inscription self-service (D44). Plafond exigé par la spec : 5 tentatives par
    # IP et par heure. La valeur vit ici — réglable, et le repli local sans Redis
    # est compté (`app/ratelimit.py`) pour que le garde-fou réel soit visible.
    signup_rate_limit_per_hour: int = 5
    # Essai gratuit : durée et quota. Ils sont **publiés** sur `/pricing`
    # (« 14 jours, 100 validations ») et **appliqués** à l'inscription ; une offre
    # annoncée qui ne serait pas appliquée serait un mensonge.
    trial_days: int = 14
    # Plafond du formulaire public de validation (élément 3, `POST /validate-form`) :
    # 10 essais gratuits par IP et par 24 h.
    validate_form_rate_limit_per_day: int = 10
    # Connexion à l'espace client (D62) : une session dure `session_hours` au plus ;
    # `login_max_failures` mots de passe faux bloquent `login_lock_minutes`, par IP
    # et par compte. Ces trois valeurs sont **écrites** sur la page de connexion.
    session_hours: int = 12
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    # Mot de passe oublié (D63) : le lien envoyé par mail vaut `password_reset_minutes`
    # et ne sert qu'une fois ; `password_reset_per_hour` demandes par IP et par heure
    # (trois par compte et par heure, en plus). Le mail part par le SMTP ci-dessous ;
    # `smtp_host` vide = aucun mail envoyé (la page répond pareil, le journal le dit).
    password_reset_minutes: int = 30
    password_reset_per_hour: int = 5
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # Suivi d'audience et d'usage (D67). Base des pays DB-IP Lite (CC BY 4.0), tenue
    # à jour chaque mois par `scripts/maj_geoip.sh` : fichier absent = pays inconnu,
    # jamais une erreur. Les lignes de `page_visits` et `api_calls` sont purgées
    # après `audience_retention_days` jours.
    geoip_db_path: str = "/var/lib/address-validator/dbip-country.mmdb"
    audience_retention_days: int = 400
    # Coûts d'exploitation (D68), en euros par mois, lus par le tableau de bord :
    # serveur, forfait du fournisseur d'adresses, envoi de mails, et prix d'un appel
    # au fournisseur (en dollars) quand il est facturé à l'appel. Tous à 0 = coûts
    # non renseignés : le résultat du mois n'est pas calculé.
    # Comptes du tableau de bord (D74) : « Nom=email » séparés par des virgules, par exemple
    # « Fzed=fzed@exemple.re, Kder=kder@exemple.re ». Seules ces adresses peuvent recevoir un
    # lien pour créer leur mot de passe. Vide = seul l'identifiant partagé DASHBOARD_USER marche.
    pilotage_comptes: str = ""
    cost_server_eur: float = 0.0
    cost_smarty_eur: float = 0.0
    cost_smarty_per_call_usd: float = 0.0
    cost_mail_eur: float = 0.0

    # Environnement
    environment: str = "development"
    log_level: str = "INFO"

    # Contact de l'exploitant : publie dans GET /manifest et dans l'en-tete
    # User-Agent du geocodeur (politique d'usage Nominatim). Surchargeable par
    # la variable CONTACT_EMAIL du .env.
    contact_email: str = "address-validator@kder-ai.com"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
