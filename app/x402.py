"""Paiement x402 **standard** (v1) — décisions D34, D35, D36.

Jusqu'ici le service facturait par une preuve à secret partagé (D15) :
l'exploitant signait `tx_hash:timestamp` en HMAC et l'agent rejouait cette
preuve. Cela ne permet pas à un agent acheteur de payer seul — il doit
demander une preuve à l'exploitant. Ce module ajoute le chemin standard, tel
que décrit par `specs/transports-v1/http.md` du dépôt `coinbase/x402` :

* corps du `402` = `{"x402Version": 1, "error": ..., "accepts": [ ... ]}`, avec
  pour chaque offre `scheme`, `network`, `maxAmountRequired`, `asset`, `payTo`,
  `resource`, `description`, `mimeType`, `maxTimeoutSeconds`, `extra` (D34) ;
* le client renvoie sa signature dans l'en-tête **`X-PAYMENT`** : base64 d'un
  `PaymentPayload` `{x402Version, scheme, network, payload: {signature,
  authorization: {from, to, value, validAfter, validBefore, nonce}}}` ;
* le serveur répond avec **`X-PAYMENT-RESPONSE`** : base64 d'un
  `SettlementResponse` `{success, transaction, network, payer}`.

Deux contrôles **indépendants**, dans cet ordre (D35, D36) :

1. **local** — la signature EIP-712 `TransferWithAuthorization` (EIP-3009) est
   récupérée hors ligne, puis comparée à `authorization.from` ; destination,
   montant, réseau, jeton et fenêtre de validité sont confrontés à l'offre
   publiée. Aucun octet ne sort avant que ce contrôle passe ;
2. **règlement** — le facilitateur (`/verify` puis `/settle`) fait payer le gaz
   et déplace réellement les USDC. C'est le règlement qui rend le paiement
   irréversible, et c'est son hash de transaction qui alimente le registre
   durable (D29). Un règlement qui échoue vaut un refus : jamais de service
   rendu contre une autorisation non encaissée.

Le facilitateur par défaut est celui de Coinbase (CDP), authentifié par un JWT
signé avec la clé CDP (`cdp.auth.generate_jwt` : ES256 ou EdDSA selon la clé).
Le module reste **défensif** (D6) : clé absente, SDK absent, facilitateur
injoignable ou réponse illisible produisent un refus *motivé* — jamais une
exception dans la réponse, jamais un service gratuit.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings
from app.onchain import ADDRESS_RE, USDC_DECIMALS, usdc_units

logger = logging.getLogger(__name__)

settings = get_settings()

#: Version du protocole annoncée (v1 : `accepts[]` + `X-PAYMENT`).
X402_VERSION = 1
#: Schéma de paiement : « exact » = montant exact, transfert unique.
SCHEME = "exact"
#: En-tête client (base64 du PaymentPayload) et en-tête serveur (règlement).
PAYMENT_HEADER = "X-PAYMENT"
PAYMENT_RESPONSE_HEADER = "X-PAYMENT-RESPONSE"
#: Réseau accepté en entrée : nom v1 et identifiant CAIP-2 (clients v2).
NETWORKS = ("base", "eip155:8453")

#: Timeout borné, comme le fournisseur (D23) : le règlement ne doit pas
#: transformer une requête payante en attente longue.
FACILITATOR_TIMEOUT = httpx.Timeout(connect=2.0, read=6.0, write=6.0, pool=2.0)

USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"

#: Anti-rejeu en mémoire (défense en profondeur). La garantie dure est le nonce
#: EIP-3009 consommé par la chaîne au premier règlement ; ce cache borné évite
#: seulement de re-régler deux fois dans la vie du processus.
_SETTLED: "OrderedDict[str, float]" = OrderedDict()
_SETTLED_LOCK = threading.Lock()
_SETTLED_MAX = 4096


def resource_url(path: str = "/validate") -> str:
    """URL publique de la ressource facturée (jamais une URL devinée)."""
    return f"{settings.public_base_url.rstrip('/')}{path}"


def payment_requirements(price: float, resource: str | None = None) -> dict[str, Any]:
    """Offre de paiement x402 v1, dérivée de la configuration réelle.

    `maxAmountRequired` est en unités atomiques (USDC = 6 décimales) : 0,02
    USDC s'écrit `"20000"`. Un agent qui lit cette offre peut construire sa
    signature sans aucune information extérieure.
    """
    return {
        "scheme": SCHEME,
        "network": settings.x402_network,
        "maxAmountRequired": str(usdc_units(price)),
        "resource": resource or resource_url(),
        "description": (
            "Validation d'une adresse de livraison (parsing, standardisation, "
            "verification fournisseur, geocodage, score de livrabilite)"
        ),
        "mimeType": "application/json",
        "outputSchema": None,
        "payTo": settings.cdp_wallet_address,
        "maxTimeoutSeconds": 300,
        "asset": settings.x402_usdc_contract,
        "extra": {
            "name": settings.x402_domain_name,
            "version": settings.x402_domain_version,
        },
    }


def payment_required_body(
    price: float, error: str | None = None, resource: str | None = None
) -> dict[str, Any]:
    """Corps du `402` au format x402 v1 (`x402Version` + `accepts[]`)."""
    return {
        "x402Version": X402_VERSION,
        "error": error or "Payment required to access this resource",
        "accepts": [payment_requirements(price, resource)],
    }


class InvalidPayment(ValueError):
    """En-tête `X-PAYMENT` illisible ou hors schéma : refus immédiat."""


def _b64decode(value: str) -> bytes:
    """Base64 tolérant au remplissage manquant, jamais tolérant au reste."""
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidPayment(f"en-tete X-PAYMENT illisible : {exc}") from exc


def parse_payment_header(value: str | None) -> dict[str, Any]:
    """Décode `X-PAYMENT` et renvoie le payload **brut** (v1 ou v2).

    Le payload brut est conservé tel quel : c'est lui qui part au facilitateur.
    Une variante v2 (`accepted` + `amount`, en-tête `PAYMENT-SIGNATURE`) est
    acceptée et normalisée, mais la réponse du service reste en v1 : une seule
    version annoncée, donc aucune ambiguïté sur ce qui a été facturé.
    """
    if not value:
        raise InvalidPayment("en-tete X-PAYMENT absent")
    try:
        payload = json.loads(_b64decode(value.strip()))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidPayment(f"en-tete X-PAYMENT non JSON : {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidPayment("en-tete X-PAYMENT : objet JSON attendu")

    version = payload.get("x402Version")
    if version not in (1, 2):
        raise InvalidPayment(f"x402Version non supportee : {version!r}")

    if version == 2:
        accepted = payload.get("accepted") or {}
        normalized = dict(payload)
        normalized["scheme"] = accepted.get("scheme", payload.get("scheme"))
        normalized["network"] = accepted.get("network", payload.get("network"))
    else:
        normalized = payload

    if normalized.get("scheme") != SCHEME:
        raise InvalidPayment(f"schema de paiement non supporte : {normalized.get('scheme')!r}")
    network = str(normalized.get("network", ""))
    if network not in NETWORKS:
        raise InvalidPayment(f"reseau non supporte : {network!r}")

    payload_data = normalized.get("payload")
    if not isinstance(payload_data, dict):
        raise InvalidPayment("champ payload absent du PaymentPayload")
    return normalized


def authorization_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Champs EIP-3009 d'un payload : `signature` + `authorization`."""
    data = payload.get("payload") or {}
    signature = data.get("signature")
    authorization = data.get("authorization")
    if not signature or not isinstance(authorization, dict):
        raise InvalidPayment("payload.signature ou payload.authorization absent")
    for field in ("from", "to", "value", "validAfter", "validBefore", "nonce"):
        if field not in authorization:
            raise InvalidPayment(f"authorization.{field} absent")
    return {"signature": str(signature), "authorization": authorization}


def _usdc(units: int) -> str:
    """Montant lisible (« 0.020000 USDC ») pour les journaux et refus."""
    return f"{units / 10**USDC_DECIMALS:.6f}"


DOMAIN_TYPE = (
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
AUTHORIZATION_TYPE = (
    "TransferWithAuthorization(address from,address to,uint256 value,"
    "uint256 validAfter,uint256 validBefore,bytes32 nonce)"
)


def domain_separator(requirements: dict[str, Any]) -> bytes:
    """`domainSeparator` EIP-712 du jeton annoncé (nom, version, chaîne, contrat)."""
    from eth_abi import encode
    from eth_hash.auto import keccak

    return keccak(
        encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                keccak(DOMAIN_TYPE.encode()),
                keccak(settings.x402_domain_name.encode()),
                keccak(settings.x402_domain_version.encode()),
                int(settings.x402_chain_id),
                requirements["asset"],
            ],
        )
    )


def authorization_struct_hash(
    requirements: dict[str, Any], authorization: dict[str, Any]
) -> bytes:
    """`hashStruct(TransferWithAuthorization)` — comparé à `eth_account` (VPS)."""
    from eth_abi import encode
    from eth_hash.auto import keccak

    return keccak(
        encode(
            ["bytes32", "address", "address", "uint256", "uint256", "uint256", "bytes32"],
            [
                keccak(AUTHORIZATION_TYPE.encode()),
                authorization["from"],
                authorization["to"],
                int(authorization["value"]),
                int(authorization["validAfter"]),
                int(authorization["validBefore"]),
                bytes.fromhex(str(authorization["nonce"])[2:]),
            ],
        )
    )


def eip712_digest(requirements: dict[str, Any], authorization: dict[str, Any]) -> bytes:
    """Digest EIP-712 `\\x19\\x01 || domainSeparator || structHash` (EIP-3009).

    Pourquoi ce calcul explicite plutôt que `eth_account` : le paquet
    `eth_account` importe `eth_keyfile`, qui importe `Crypto.Protocol.KDF` de
    pycryptodome. Quand le module natif de pycryptodome n'est pas chargeable
    (mesuré : politique de contrôle d'application Windows bloquant
    `_keccak.pyd`), pycryptodome bascule sur son repli cffi et l'import échoue
    (`multiple declarations of function Salsa20_8_core`). Cette vérification est
    la porte de sûreté du paiement : elle ne doit dépendre ni d'un magasin de
    clés ni d'un compilateur à la volée. Trois primitives suffisent, épinglées
    explicitement (D38) :

    * `eth_hash.auto.keccak` — keccak-256 (Ethereum) ;
    * `eth_abi.encode` — encodage ABI strict des champs ;
    * `eth_keys` — récupération de la clé publique (backend natif Python).

    Preuve externe (tests/test_x402.py) : ce digest est le **seul** qui redonne
    l'adresse `from` en récupérant la clé publique de la signature publiée par
    la spécification x402 ; les deux étapes intermédiaires sont en plus
    comparées à `eth_account` exécuté sur le VPS.
    """
    from eth_hash.auto import keccak

    return keccak(
        b"\x19\x01"
        + domain_separator(requirements)
        + authorization_struct_hash(requirements, authorization)
    )


def recover_signer(
    requirements: dict[str, Any], authorization: dict[str, Any], signature: str
) -> str:
    """Adresse qui a signé l'autorisation EIP-712. Lève si irrécupérable."""
    from eth_hash.auto import keccak
    from eth_keys import keys

    raw = str(signature)
    raw = raw[2:] if raw.startswith("0x") else raw
    try:
        signature_bytes = bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(f"signature non hexadecimale : {exc}") from exc
    if len(signature_bytes) != 65:
        raise ValueError(f"signature de {len(signature_bytes)} octets au lieu de 65")

    # Convention `v` : les portefeuilles émettent 27/28 (ethers, MetaMask) ou 0/1
    # (certains clients). On accepte les deux, on n'en devine aucune.
    if signature_bytes[64] >= 27:
        signature_bytes = signature_bytes[:64] + bytes([signature_bytes[64] - 27])

    public_key = keys.Signature(signature_bytes).recover_public_key_from_msg_hash(
        eip712_digest(requirements, authorization)
    )
    # `to_bytes()` rend la cle publique **sans** le prefixe 0x04 (64 octets) :
    # l'adresse est keccak-256 de ces 64 octets, tronquee aux 20 derniers. Le
    # premier essai decoupait `[1:]` — erreur mesuree par le vecteur externe, qui
    # rendait alors une adresse quelconque.
    return "0x" + keccak(public_key.to_bytes())[12:].hex()


def _nonce_seen(nonce: str) -> bool:
    """Vrai si ce nonce a **déjà été réglé** dans la vie de ce processus."""
    with _SETTLED_LOCK:
        if nonce in _SETTLED:
            return True
        _SETTLED[nonce] = time.time()
        while len(_SETTLED) > _SETTLED_MAX:
            _SETTLED.popitem(last=False)
        return False


def reset_nonce_cache() -> None:
    """Vide le cache anti-rejeu (tests et réinitialisation d'exploitation)."""
    with _SETTLED_LOCK:
        _SETTLED.clear()


def verify_authorization(
    payload: dict[str, Any], requirements: dict[str, Any]
) -> tuple[bool, str, dict[str, Any]]:
    """Vérifie **hors ligne** l'autorisation : `(ok, raison, détails)`.

    Fail-closed : toute anomalie (destination, montant, fenêtre, jeton, réseau,
    signature non attribuable) renvoie `False` avec la raison exacte. Aucun
    appel réseau n'a lieu ici : ce contrôle doit être insensible à une panne du
    facilitateur et au déni de service par lenteur.
    """
    try:
        signed = authorization_of(payload)
    except InvalidPayment as exc:
        return False, str(exc), {}

    authorization = signed["authorization"]
    pay_to = str(requirements.get("payTo", ""))
    to = str(authorization.get("to", ""))
    if not ADDRESS_RE.match(pay_to):
        return False, "payTo absent ou malforme dans l'offre publiee", {}
    if not ADDRESS_RE.match(to):
        return False, "authorization.to absent ou malforme", {}
    if to.lower() != pay_to.lower():
        return False, f"destinataire incorrect : {to} != {pay_to}", {}

    try:
        value = int(authorization["value"])
        minimum = int(requirements["maxAmountRequired"])
        valid_after = int(authorization["validAfter"])
        valid_before = int(authorization["validBefore"])
    except (KeyError, TypeError, ValueError):
        return False, "montant ou fenetre de validite non entier", {}

    if value < minimum:
        return False, (
            f"montant insuffisant : {_usdc(value)} USDC < {_usdc(minimum)} USDC"
        ), {}

    now = int(time.time())
    if now < valid_after:
        return False, f"autorisation pas encore valide (validAfter={valid_after})", {}
    if now >= valid_before:
        return False, f"autorisation expiree (validBefore={valid_before})", {}

    if str(payload.get("network", "")) not in NETWORKS:
        return False, f"reseau non supporte : {payload.get('network')!r}", {}

    try:
        signer = recover_signer(requirements, authorization, signed["signature"])
    except Exception as exc:  # noqa: BLE001 - signature illisible = refus
        return False, f"signature EIP-712 illisible : {type(exc).__name__}: {exc}", {}

    expected = str(authorization.get("from", ""))
    if not ADDRESS_RE.match(expected) or signer.lower() != expected.lower():
        return False, f"signature non attribuable a authorization.from ({signer})", {}

    return True, f"autorisation EIP-3009 valide de {expected}", {
        "payer": expected,
        "value": value,
        "nonce": str(authorization["nonce"]),
        "amount_usdc": _usdc(value),
    }


def facilitator_headers(method: str, endpoint: str) -> dict[str, str]:
    """En-têtes du facilitateur : JSON, plus le JWT CDP s'il est signable.

    Le JWT est signé par `cdp.auth.generate_jwt` (SDK épinglé, D1) : ES256 pour
    une clé PEM/EC, EdDSA pour une clé Ed25519 encodée en base64 — c'est la
    forme de la clé fournie pour ce service. Une clé illisible n'est pas
    contournée : la requête part sans jeton et le facilitateur répond 401, ce
    qui devient un refus motivé (fail-closed).
    """
    headers = {"Content-Type": "application/json"}
    if not (settings.cdp_api_key_id and settings.cdp_api_key_secret):
        return headers

    parsed = urlparse(endpoint)
    try:
        from cdp.auth import JwtOptions, generate_jwt

        token = generate_jwt(
            JwtOptions(
                api_key_id=settings.cdp_api_key_id,
                api_key_secret=settings.cdp_api_key_secret,
                request_method=method,
                request_host=parsed.netloc,
                request_path=parsed.path,
            )
        )
        headers["Authorization"] = f"Bearer {token}"
    except Exception as exc:  # noqa: BLE001 - l'absence de jeton est visible en 401
        logger.warning("JWT CDP non signable (%s) : la requete part sans jeton", exc)
    return headers


def _post_facilitator(suffix: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST JSON vers le facilitateur. Lève en cas d'échec de transport."""
    endpoint = f"{settings.x402_facilitator_url.rstrip('/')}{suffix}"
    with httpx.Client(timeout=FACILITATOR_TIMEOUT) as client:
        response = client.post(
            endpoint, json=body, headers=facilitator_headers("POST", endpoint)
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"facilitateur {suffix} HTTP {response.status_code} : {response.text[:200]}"
        )
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"facilitateur {suffix} : objet JSON attendu")
    return data


def _facilitator_body(payload: dict[str, Any], requirements: dict[str, Any]) -> dict[str, Any]:
    """Corps commun `/verify` et `/settle` : version + payload brut + offre.

    Les clés sont celles du SDK x402 (`VerifyRequest`/`SettleRequest` du dépôt
    coinbase/x402) : `x402Version`, `paymentPayload`, `paymentRequirements`.
    """
    return {
        "x402Version": int(payload.get("x402Version", X402_VERSION)),
        "paymentPayload": payload,
        "paymentRequirements": requirements,
    }


def facilitator_verify(
    payload: dict[str, Any], requirements: dict[str, Any]
) -> tuple[bool, str]:
    """`POST /verify` : le facilitateur confirme que le paiement est acceptable."""
    try:
        data = _post_facilitator("/verify", _facilitator_body(payload, requirements))
    except Exception as exc:  # noqa: BLE001
        return False, f"verification du facilitateur impossible : {type(exc).__name__}: {exc}"
    if data.get("isValid") is not True:
        reason = data.get("invalidReason") or data.get("invalidMessage") or "raison non fournie"
        return False, f"facilitateur : paiement invalide ({reason})"
    return True, "facilitateur : paiement valide"


def facilitator_settle(
    payload: dict[str, Any], requirements: dict[str, Any]
) -> tuple[bool, str, str | None, str | None]:
    """`POST /settle` : encaissement réel. Renvoie `(ok, raison, tx, payer)`."""
    try:
        data = _post_facilitator("/settle", _facilitator_body(payload, requirements))
    except Exception as exc:  # noqa: BLE001
        return False, f"reglement impossible : {type(exc).__name__}: {exc}", None, None
    if data.get("success") is not True:
        reason = data.get("errorReason") or data.get("errorMessage") or "raison non fournie"
        return False, f"reglement refuse par le facilitateur ({reason})", None, None
    transaction = data.get("transaction") or None
    if not transaction:
        # Un règlement sans hash n'est pas vérifiable : il ne peut pas alimenter
        # le registre durable, il ne compte donc pas comme un paiement.
        return False, "reglement annonce reussi sans hash de transaction", None, None
    return True, "paiement regle", str(transaction), data.get("payer")


def settlement_header(
    transaction: str | None,
    payer: str | None,
    success: bool = True,
    error_reason: str | None = None,
) -> str:
    """`X-PAYMENT-RESPONSE` : base64 d'un `SettlementResponse`."""
    body: dict[str, Any] = {
        "success": success,
        "transaction": transaction or "",
        "network": settings.x402_network,
        "payer": payer,
    }
    if not success:
        body["errorReason"] = error_reason or "payment_failed"
    return base64.b64encode(json.dumps(body).encode()).decode()


@dataclass
class PaymentDecision:
    """Verdict d'un paiement : de quoi répondre, tracer et facturer."""

    paid: bool
    reason: str
    protocol: str = "none"
    transaction: str | None = None
    payer: str | None = None
    amount_usdc: str | None = None
    response_header: str | None = None

    def refusal(self, reason: str, protocol: str = "x402") -> "PaymentDecision":
        """Verdict de refus, avec l'en-tête de règlement en échec (spec v1)."""
        return PaymentDecision(
            paid=False,
            reason=reason,
            protocol=protocol,
            response_header=settlement_header(
                None, None, success=False, error_reason=reason
            ),
        )


def authorize_and_settle(
    header: str | None, price: float, resource: str | None = None
) -> PaymentDecision:
    """Chemin complet x402 : décodage → vérification locale → règlement.

    L'ordre est significatif : rien n'est envoyé au facilitateur avant que la
    signature, la destination, le montant et la fenêtre soient conformes à
    l'offre publiée. Un facilitateur lent ou en panne ne peut donc pas servir
    de prétexte pour rendre le service sans paiement vérifié.
    """
    absent = PaymentDecision(paid=False, reason="en-tete X-PAYMENT absent", protocol="x402")

    wallet = settings.cdp_wallet_address or ""
    if not ADDRESS_RE.match(wallet):
        return absent.refusal("aucune adresse d'encaissement utilisable configuree")

    try:
        payload = parse_payment_header(header)
    except InvalidPayment as exc:
        return absent.refusal(str(exc))

    requirements = payment_requirements(price, resource)
    ok, reason, details = verify_authorization(payload, requirements)
    if not ok:
        logger.warning("paiement x402 refuse (local) : %s", reason)
        return absent.refusal(reason)

    nonce = details["nonce"]
    with _SETTLED_LOCK:
        if nonce in _SETTLED:
            return absent.refusal(f"autorisation deja reglee (nonce {nonce[:18]}...)")

    if settings.x402_facilitator_verify:
        ok, reason = facilitator_verify(payload, requirements)
        if not ok:
            logger.warning("paiement x402 refuse (facilitateur) : %s", reason)
            return absent.refusal(reason)

    transaction: str | None = None
    payer = details["payer"]
    if settings.x402_require_settlement:
        ok, reason, transaction, payer = facilitator_settle(payload, requirements)
        if not ok:
            logger.warning("paiement x402 refuse (reglement) : %s", reason)
            return absent.refusal(reason)
    else:  # pragma: no cover - mode diagnostic, jamais en production
        logger.warning("X402_REQUIRE_SETTLEMENT=false : service rendu sans encaissement")

    # Le nonce n'est mémorisé qu'après un règlement réussi : un échec transitoire
    # reste rejouable par un client honnête.
    with _SETTLED_LOCK:
        _SETTLED[nonce] = time.time()
        while len(_SETTLED) > _SETTLED_MAX:
            _SETTLED.popitem(last=False)

    return PaymentDecision(
        paid=True,
        reason=f"x402 : {details['amount_usdc']} USDC regles",
        protocol="x402",
        transaction=transaction,
        payer=payer or details["payer"],
        amount_usdc=details["amount_usdc"],
        response_header=settlement_header(transaction, payer or details["payer"]),
    )
