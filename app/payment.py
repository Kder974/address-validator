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

  D34 Le protocole x402 **standard** est implémenté (app/x402.py) : le corps du
      `402` publie `accepts[]`, le client paie par l'en-tête `X-PAYMENT`, et la
      réponse porte `X-PAYMENT-RESPONSE` avec le hash du règlement. `/validate`
      connaît donc trois issues : paiement x402 réglé, preuve HMAC héritée
      (D15 — outillage de l'exploitant), refus.
  D35 La signature EIP-712 (`TransferWithAuthorization`, EIP-3009) est
      récupérée **hors ligne** et confrontée à l'offre publiée avant tout appel
      réseau : destination, montant, jeton, réseau, fenêtre de validité.
  D36 Le règlement passe par un facilitateur (`/verify` puis `/settle`) dont le
      hash de transaction alimente le registre durable (D29). Un règlement en
      échec vaut un refus : jamais de service contre une autorisation non
      encaissée.

  Limite assumée : la preuve HMAC (D15) reste un secret partagé. Elle est
  conservée pour l'outillage de l'exploitant et les clients déjà intégrés, mais
  elle n'est plus la seule voie : un agent autonome peut désormais payer seul.

  D40 Une transaction de recette n'est pas du revenu. `record_transaction`
    enregistre donc `test_mode` : vrai quand `TEST_MODE=true` (recette en cours,
    le temps d'un test de bout en bout payé), faux sinon. La ligne reste au
    registre — la tracer est utile — mais /metrics/revenue l'exclut de `real_*`
    et la compte dans `test_*`. Sans cette distinction, deux validations de
    recette du 11/10/2026 apparaissaient comme 0,04 USDC de chiffre d'affaires.
"""

import hashlib
import hmac
import logging
import time

from fastapi import Request
from sqlalchemy import text

from app.config import get_settings
from app.onchain import check_usdc_transfer
from app.x402 import (
    PAYMENT_HEADER,
    PAYMENT_RESPONSE_HEADER,
    PaymentDecision,
    authorize_and_settle,
    payment_required_body,
    resource_url,
)

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


def generate_payment_required_response(price: float, error: str | None = None) -> dict:
    """Corps du `402` : x402 v1 (`x402Version` + `accepts[]`) + clés historiques.

    Les clés hors spec (`price`, `currency`, `wallet`, `protocol`, `version`)
    restent publiées : l'outillage interne les lit, et le manifeste D33 les
    montre en exemple. Un client x402 les ignore sans se plaindre.
    """
    body = payment_required_body(price, error)
    body.update(
        {
            "price": price,
            "currency": "USDC",
            "wallet": settings.cdp_wallet_address,
            "protocol": "x402",
            "version": "1.0",
        }
    )
    return body


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


def tx_hash_from_proof(proof: str | None) -> str | None:
    """Extrait le hash de transaction d'une preuve bien formée, sinon None.

    D29 : c'est ce hash qui identifie le paiement dans le registre durable
    (`payment_transactions`). Le lire ici évite de dépendre d'un en-tête
    optionnel (`X-Transaction-Id`) pour compter ce qui a été réellement payé.
    """
    if not proof:
        return None
    parts = proof.split(":")
    if len(parts) != 3 or not parts[0]:
        return None
    return parts[0]


def legacy_proof_result(request: Request) -> PaymentDecision:
    """Preuve à secret partagé (D7, D15, D27) — chemin de compatibilité.

    Conservée pour l'outillage de l'exploitant et les clients déjà intégrés :
    `X-Payment-Proof: tx_hash:timestamp:hmac_sha256(tx_hash:timestamp)`.
    """
    proof = request.headers.get(PROOF_HEADER)
    if not proof:
        return PaymentDecision(False, "aucune preuve de paiement fournie", protocol="none")

    try:
        parts = proof.split(":")
        if len(parts) != 3:
            return PaymentDecision(False, "preuve malformee", protocol="hmac")
        tx_hash, timestamp, signature = parts
        if not tx_hash or not signature:
            return PaymentDecision(False, "preuve malformee", protocol="hmac")

        ts = float(timestamp)

        # Fenêtre de fraîcheur symétrique : ni périmé, ni venu du futur (anti-rejeu).
        if abs(time.time() - ts) > PROOF_MAX_AGE_SECONDS:
            return PaymentDecision(False, "preuve hors fenetre de fraicheur", protocol="hmac")

        expected = sign_proof(tx_hash, timestamp)
        if not hmac.compare_digest(expected, signature):
            return PaymentDecision(False, "signature HMAC invalide", protocol="hmac")
    except Exception:
        return PaymentDecision(False, "preuve illisible", protocol="hmac")

    # Confirmation on-chain (D27) : désactivée par défaut, D15 s'applique alors.
    # Activée, elle est *fail-closed* : un refus de la chaîne vaut un refus.
    if settings.x402_onchain_verify:
        confirmed, reason = check_usdc_transfer(tx_hash)
        if not confirmed:
            logger.warning("Preuve de paiement refusee (on-chain) : %s", reason)
            return PaymentDecision(False, reason, protocol="hmac")

    return PaymentDecision(
        True,
        f"preuve HMAC valide pour {tx_hash}",
        protocol="hmac",
        transaction=tx_hash,
    )


def payment_result(request: Request) -> PaymentDecision:
    """Verdict de paiement : x402 standard d'abord, preuve HMAC ensuite.

    L'ordre est explicite et vaut contrat : un agent autonome présente
    `X-PAYMENT` ; l'en-tête hérité `X-Payment-Proof` n'est examiné qu'en son
    absence, ce qui interdit de faire passer une preuve HMAC pour un paiement
    x402 — et inversement.
    """
    header = request.headers.get(PAYMENT_HEADER) or request.headers.get("PAYMENT-SIGNATURE")
    if header:
        path = getattr(getattr(request, "url", None), "path", "") or "/validate"
        return authorize_and_settle(header, settings.x402_price, resource_url(path))
    return legacy_proof_result(request)


def verify_payment(request: Request) -> bool:
    """Vérifie la preuve de paiement x402. Fail-closed : False par défaut."""
    return payment_result(request).paid


def record_transaction(
    tx_hash: str, amount: float, address: str, test_mode: bool | None = None
) -> bool:
    """Enregistre la transaction de paiement (meilleur effort, jamais bloquant).

    `test_mode=None` (défaut) suit `TEST_MODE` : un service en production compte
    ce qu'il encaisse, une recette se marque explicitement. Une ligne marquée
    reste au registre mais sort du revenu réel publié (D40).
    """
    if not tx_hash:
        return False
    marque = settings.test_mode if test_mode is None else bool(test_mode)
    try:
        from app.database import engine

        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO payment_transactions (tx_hash, amount, address, test_mode) "
                    "VALUES (:tx_hash, :amount, :address, :test_mode) "
                    "ON CONFLICT (tx_hash) DO NOTHING"
                ),
                {
                    "tx_hash": tx_hash,
                    "amount": amount,
                    "address": address,
                    "test_mode": marque,
                },
            )
            conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("record_transaction a échoué (non bloquant) : %s", exc)
        return False
