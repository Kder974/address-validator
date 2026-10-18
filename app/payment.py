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
      web3 ni authorization EIP-3009.

  W1  (durcissement du 12/10/2026, `AUDIT_TRAFIC_VPS.md` §5) La confirmation
      on-chain n'est plus *optionnelle* : elle est **active par défaut**
      (`X402_ONCHAIN_VERIFY=true`), parce que le défaut inverse était un
      fail-open — un déploiement qui omettait la variable servait le service
      contre une preuve synthétique. La voie héritée n'a donc plus que deux
      issues :
        * **production** (`TEST_MODE=false`) : le reçu du `tx_hash` cité doit
          être un transfert USDC confirmé vers le portefeuille d'encaissement ;
          une preuve HMAC seule vaut `402`, et une couche on-chain coupée vaut
          `402` aussi (`legacy_proof_result`), jamais un `200` ;
        * **recette déclarée** (`TEST_MODE=true`) : la preuve synthétique reste
          acceptée — c'est le harnais de l'exploitant, on ne paie pas 0,02 USDC
          réels à chaque contrôle. Ce `200`-là ne peut pas compter comme du
          revenu : `record_transaction` marque la ligne `test_mode` (D40) et
          `/metrics/revenue` l'exclut de `real_revenue_usdc`.

  W2  (durcissement du 12/10/2026) `record_transaction` ne leve toujours pas
      d'exception, mais son **echec n'est plus ignorable** : un encaissement qui
      n'a pas pu entrer au registre n'est pas servi — `/validate` repond `500`,
      `/mcp` une erreur JSON-RPC `-32603` — et l'incident est compte
      (`ledger_write_failures_total`). Un revenu reel non inscrit n'est ni
      mesurable (D29) ni publie (D40) : il n'existe pour personne.

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
    PAYMENT_SIGNATURE_HEADER,
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
            # D79 : le réseau au niveau racine aussi (les clients historiques le
            # lisent là, la spec x402 v1 le publie dans `accepts[].network`).
            "network": settings.x402_network,
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

    # Recette déclarée (W1/D40) : le harnais de l'exploitant ne peut pas payer
    # 0,02 USDC réels à chaque contrôle, la preuve synthétique reste donc
    # acceptée — mais la ligne écrite est marquée `test_mode` (D40), donc exclue
    # du revenu réel : un `200` sans encaissement ne peut pas devenir du chiffre
    # d'affaires. Ce chemin est tracé pour ne jamais être silencieux.
    if settings.test_mode:
        logger.warning(
            "Paiement accepte en RECETTE (TEST_MODE=true) sans encaissement verifie : %s",
            tx_hash,
        )
        return PaymentDecision(
            True,
            f"preuve HMAC valide pour {tx_hash} (recette)",
            protocol="hmac",
            transaction=tx_hash,
        )

    # Hors recette, la couche on-chain est la seule autorité (W1) : la couper
    # reviendrait à rendre le service contre une simple signature HMAC, c'est-à-
    # dire contre rien pour qui connaît `API_SECRET_KEY`.
    if not settings.x402_onchain_verify:
        logger.warning(
            "Preuve HMAC refusee : verification on-chain coupee hors recette (W1)"
        )
        return PaymentDecision(
            False,
            "verification on-chain desactivee hors recette : preuve a secret partage refusee",
            protocol="hmac",
        )

    # Fail-closed : l'absence de confirmation de la chaîne vaut refus.
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
    header = request.headers.get(PAYMENT_HEADER) or request.headers.get(PAYMENT_SIGNATURE_HEADER)
    if header:
        path = getattr(getattr(request, "url", None), "path", "") or "/validate"
        return authorize_and_settle(header, settings.x402_price, resource_url(path))
    return legacy_proof_result(request)


#: D82 : du motif interne d'un refus à un code court, dans l'ordre où on les teste. Le
#: tableau de bord sépare « aucun paiement présenté » (`absent`) des paiements refusés.
# ponytail: classement par morceaux du motif ; un code posé à la source si les motifs se multiplient.
MOTIFS_REFUS = (
    ("rejeu", ("deja reglee",)),
    ("montant", ("montant insuffisant",)),
    ("delai", ("pas encore valide", "expiree")),
    ("offre", ("offre acceptee differente", "destinataire incorrect", "reseau non supporte",
               "schema de paiement", "x402version non supportee", "transport mcp")),
    ("reglement", ("reglement",)),
    ("facilitateur", ("facilitateur",)),
    ("config", ("adresse d'encaissement",)),
    ("illisible", ("en-tete", "absent", "malforme", "non entier", "illisible", "json")),
    ("signature", ("signature",)),
)


def motif_refus(decision: PaymentDecision) -> str:
    """Code court d'un refus : `absent` sans paiement présenté, sinon ce qui a manqué."""
    if decision.protocol == "none":
        return "absent"
    if decision.protocol != "x402":
        return "preuve"
    texte = decision.reason.lower()
    return next((code for code, morceaux in MOTIFS_REFUS if any(m in texte for m in morceaux)), "autre")


def verify_payment(request: Request) -> bool:
    """Vérifie la preuve de paiement x402. Fail-closed : False par défaut."""
    return payment_result(request).paid


def record_transaction(
    tx_hash: str, amount: float, address: str, test_mode: bool | None = None
) -> bool:
    """Enregistre la transaction de paiement (sans jamais lever d'exception).

    Renvoie `False` quand la ligne n'a pas pu etre ecrite : hash vide, base
    injoignable, erreur SQL. Depuis D41, l'appelant ne peut plus l'ignorer :
    `/validate` et `/mcp` refusent alors de servir (500 / -32603) et comptent
    l'incident, parce qu'un encaissement non inscrit n'est ni mesurable (D29) ni
    publie (D40).

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
        logger.warning(
            "record_transaction a échoué (l'appelant doit refuser de servir, D41) : %s", exc
        )
        return False
