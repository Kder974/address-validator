"""Couche paiement x402 (CDP SDK) + vérification de la preuve de paiement.

La spec (Phase 3) fournit un squelette. Décisions de durcissement :

  D6  L'import et la configuration du SDK CDP sont **défensifs** : une clé
      absente ou un SDK au mauvais niveau d'API ne doivent pas empêcher l'API
      (et ses tests) de démarrer. En cas d'indisponibilité, la vérification
      reste *fail-closed* (aucun paiement deviné) — jamais fail-open.

  D7  `verify_payment` du squelette acceptait n'importe quelle chaîne
      `tx:ts:sig`. On ajoute (a) une fenêtre de fraîcheur symétrique de 300 s
      (rejette le périmé ET le futur — anti-rejeu) et (b) une signature
      HMAC-SHA256 sur `tx_hash:timestamp` comparée en temps constant. Le
      message `tx_hash:timestamp` n'était donc plus vérifiable avec un simple
      faux couple ; le test fourni par la spec utilisait par ailleurs un
      timestamp de 2009 (2009-02-13) qui aurait échoué — corrigé côté test.

  D27 Le squelette prévoyait un facilitateur x402 via le SDK CDP. Le SDK épinglé
      (`cdp-sdk==1.0.1`) expose une autre API (`CdpClient` sans `configure` :
      constaté au démarrage du VPS), donc la confirmation du paiement passe par
      une **lecture JSON-RPC** du reçu sur Base (`app/onchain.py`), sans paquet
      web3 ni authorization EIP-3009. Elle est *optionnelle* et désactivée par
      défaut : D15 reste la référence du service, `verify_payment` accepte la
      preuve HMAC seule tant que `X402_ONCHAIN_VERIFY` n'est pas vrai.

  Limite assumée : ceci est une preuve à secret partagé, pas une vérification
  on-chain de l'autorisation EIP-3009. Le chemin de mise à niveau vers un
  facilitateur x402 réel est décrit dans README.md §7 et DECISIONS.md (D15).
"""

import hashlib
import hmac
import logging
import time

from fastapi import Request
from sqlalchemy import text

from app.config import get_settings
from app.onchain import check_usdc_transfer

logger = logging.getLogger(__name__)

settings = get_settings()

PROOF_HEADER = "X-Payment-Proof"
PROOF_MAX_AGE_SECONDS = 300

# --- Import défensif du SDK CDP (D6) -----------------------------------------
try:  # pragma: no cover - dépend de l'environnement
    from cdp import Cdp  # API 0.x, utilisée par la spec

    _CDP_AVAILABLE = True
except Exception:  # noqa: BLE001
    Cdp = None  # type: ignore[assignment]
    try:
        from cdp import CdpClient  # API 1.x

        Cdp = CdpClient  # type: ignore[assignment]
        _CDP_AVAILABLE = True
    except Exception:  # noqa: BLE001
        Cdp = None  # type: ignore[assignment]
        _CDP_AVAILABLE = False


def configure_cdp() -> bool:
    """Configure le SDK CDP si possible. Ne lève jamais (D6)."""
    if not _CDP_AVAILABLE:
        logger.warning("SDK CDP indisponible : paiement x402 en mode preuve seule.")
        return False
    if not (settings.cdp_api_key_id and settings.cdp_api_key_secret):
        logger.info("CDP non configuré (clés absentes) : mode preuve seule.")
        return False
    try:  # pragma: no cover - nécessite de vraies clés
        Cdp.configure(settings.cdp_api_key_id, settings.cdp_api_key_secret)
        logger.info("SDK CDP configuré.")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Échec de Cdp.configure : %s", exc)
        return False


CDP_CONFIGURED = configure_cdp()


def generate_payment_required_response(price: float) -> dict:
    return {
        "error": "Payment Required",
        "price": price,
        "currency": "USDC",
        "wallet": settings.cdp_wallet_address,
        "protocol": "x402",
        "version": "1.0",
    }


def sign_proof(tx_hash: str, timestamp: str) -> str:
    """HMAC-SHA256 de `tx_hash:timestamp` avec le secret partagé de l'API."""
    message = f"{tx_hash}:{timestamp}".encode()
    return hmac.new(
        settings.api_secret_key.encode(), message, hashlib.sha256
    ).hexdigest()


def build_payment_proof(tx_hash: str, timestamp: float | None = None) -> str:
    """Construit une preuve valide. Utilisé par les clients et les tests."""
    ts = str(timestamp if timestamp is not None else time.time())
    return f"{tx_hash}:{ts}:{sign_proof(tx_hash, ts)}"


def verify_payment(request: Request) -> bool:
    """Vérifie la preuve de paiement x402. Fail-closed : False par défaut."""
    proof = request.headers.get(PROOF_HEADER)

    if not proof:
        return False

    try:
        parts = proof.split(":")
        if len(parts) != 3:
            return False

        tx_hash, timestamp, signature = parts
        if not tx_hash or not signature:
            return False

        ts = float(timestamp)

        # Fenêtre de fraîcheur symétrique : ni périmé, ni venu du futur (anti-rejeu).
        if abs(time.time() - ts) > PROOF_MAX_AGE_SECONDS:
            return False

        expected = sign_proof(tx_hash, timestamp)
        if not hmac.compare_digest(expected, signature):
            return False
    except Exception:
        return False

    # Confirmation on-chain (D27) : désactivée par défaut, D15 s'applique alors.
    # Activée, elle est *fail-closed* : un refus de la chaîne vaut un refus.
    if settings.x402_onchain_verify:
        confirmed, reason = check_usdc_transfer(tx_hash)
        if not confirmed:
            logger.warning("Preuve de paiement refusee (on-chain) : %s", reason)
            return False

    return True


def record_transaction(tx_hash: str, amount: float, address: str) -> bool:
    """Enregistre la transaction de paiement (meilleur effort, jamais bloquant)."""
    if not tx_hash:
        return False
    try:
        from app.database import engine

        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO payment_transactions (tx_hash, amount, address) "
                    "VALUES (:tx_hash, :amount, :address) "
                    "ON CONFLICT (tx_hash) DO NOTHING"
                ),
                {"tx_hash": tx_hash, "amount": amount, "address": address},
            )
            conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("record_transaction a échoué (non bloquant) : %s", exc)
        return False
