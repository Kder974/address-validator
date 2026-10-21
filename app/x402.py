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

Depuis D77, la même offre est aussi publiée en **x402 v2**
(`specs/transports-v2/http.md`) : en-tête `PAYMENT-REQUIRED` sur le `402`
(réseau CAIP-2, `amount`, extension `bazaar` du catalogue Coinbase), paiement
dans `PAYMENT-SIGNATURE`, règlement dans `PAYMENT-RESPONSE`. Le corps du `402`
reste en v1 : un client v1 et un client v2 lisent chacun leur version.

Depuis D78, le transport **MCP** de la même spécification
(`specs/transports-v2/mcp.md`) est servi : le `PaymentPayload` voyage dans
`params._meta["x402/payment"]`, un appel sans paiement rend un **résultat
d'outil** `isError: true` dont `structuredContent` est le `PaymentRequired` v2
(réseau CAIP-2, `mcp://tool/<nom>`), et le règlement revient dans
`result._meta["x402/payment-response"]`. La vérification et le règlement sont
**les mêmes** que pour l'en-tête HTTP : `settle_payment` est l'unique chemin,
les deux transports ne diffèrent que par l'endroit où le payload voyage.

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
from dataclasses import dataclass, field
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
#: x402 v2 (D77) : l'offre, le paiement et le règlement passent par des en-têtes.
PAYMENT_REQUIRED_HEADER = "PAYMENT-REQUIRED"
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE_HEADER_V2 = "PAYMENT-RESPONSE"
#: Transport MCP v2 (D78) : le paiement et son reçu vivent dans les métadonnées
#: de la requête (`params._meta`) et du résultat (`result._meta`).
MCP_PAYMENT_META_KEY = "x402/payment"
MCP_PAYMENT_RESPONSE_META_KEY = "x402/payment-response"
#: Ressource d'un outil MCP : la forme publiée par le SDK x402 officiel.
MCP_RESOURCE_PREFIX = "mcp://tool/"
#: Réseau accepté en entrée : nom v1 et identifiant CAIP-2 (clients v2), tirés de la configuration.
NETWORKS = (settings.x402_network, f"eip155:{settings.x402_chain_id}")
#: Un motif de refus voyage dans des en-têtes : borné et ASCII, sinon nginx (tampon de
#: 4 Ko) rendrait un 502 à la place du 402. Le motif peut venir du client ou du facilitateur.
MOTIF_MAX = 120
#: Fiche du catalogue Coinbase (`resource` v2) : ASCII, 32 caractères, 5 étiquettes au plus.
SERVICE_NAME = "Address Validator"
TAGS = ["address-validation", "geocoding", "shipping", "e-commerce", "ai-agents"]
#: Exemple publié dans la fiche : Bazaar le rejoue tel quel pour vérifier le `402`.
EXAMPLE_REQUEST = {
    "address": {
        "street": "1600 Amphitheatre Parkway",
        "city": "Mountain View",
        "state": "CA",
        "zip": "94043",
        "country": "US",
    }
}

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


def motif(reason: str | None) -> str | None:
    """Motif de refus publiable en en-tête : ASCII, `MOTIF_MAX` caractères au plus."""
    return reason.encode("ascii", "replace").decode()[:MOTIF_MAX] if reason else reason


def network_v2() -> str:
    """Réseau au format CAIP-2 (`eip155:8453` pour Base), dérivé de la chaîne configurée."""
    return f"eip155:{settings.x402_chain_id}"


def description(price: float) -> str:
    """Ce que l'agent achète, en anglais : la langue que les agents lisent le mieux."""
    return (
        "Validate and standardize a delivery address: parsing, provider verification "
        f"(US-optimized), geocoding and a deliverability score, as JSON. {price:g} USDC "
        "per call on Base. Use when an agent must verify a US or French shipping "
        "address before checkout, label creation, or customer storage."
    )


def payment_requirements(price: float, resource: str | None = None) -> dict[str, Any]:
    """Offre de paiement x402 v1, dérivée de la configuration réelle.

    `maxAmountRequired` est en unités atomiques (USDC = 6 décimales) : 0,02
    USDC s'écrit `"20000"`. Un agent qui lit cette offre peut construire sa
    signature sans aucune information extérieure.

    D79 : `outputSchema` n'est plus `null` — il est dérivé du modèle Pydantic de
    la réponse (`ValidationResponse`), comme les schémas du manifeste et de MCP
    (D33) : un client x402 reçoit le contrat de sortie réel, et une évolution du
    modèle le met à jour toute seule.
    """
    from app.schemas import ValidationResponse

    return {
        "scheme": SCHEME,
        "network": settings.x402_network,
        "maxAmountRequired": str(usdc_units(price)),
        "resource": resource or resource_url(),
        "description": description(price),
        "mimeType": "application/json",
        "outputSchema": ValidationResponse.model_json_schema(),
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


def payment_requirements_v2(price: float) -> dict[str, Any]:
    """La même offre au format v2 : `amount` au lieu de `maxAmountRequired`, réseau CAIP-2."""
    v1 = payment_requirements(price)
    return {
        "scheme": SCHEME,
        "network": network_v2(),
        "amount": v1["maxAmountRequired"],
        "asset": v1["asset"],
        "payTo": v1["payTo"],
        "maxTimeoutSeconds": v1["maxTimeoutSeconds"],
        "extra": v1["extra"],
    }


def _inline(schema: dict[str, Any]) -> dict[str, Any]:
    """Schéma Pydantic sans `$defs` : Bazaar refuse toute référence hors du document."""
    defs = schema.get("$defs", {})

    def walk(node: Any, noms: bool = False) -> Any:
        # `noms` : on est dans un dictionnaire `properties`, dont les clés sont des noms de champs.
        if isinstance(node, dict):
            if "$ref" in node and not noms:
                return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: walk(v, k == "properties" and not noms) for k, v in node.items()
                    if noms or k not in ("$defs", "title", "description")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def bazaar_extension(price: float) -> dict[str, Any]:
    """Extension `bazaar` (specs/extensions/bazaar.md) : comment appeler `/validate`, et ce qui revient.

    Le schéma du corps est celui du modèle Pydantic de la route : la fiche ne peut
    pas promettre un autre format que celui que le service accepte.
    """
    from app.schemas import ValidationRequest

    exemple_reponse = {
        "result": {
            "validated": True,
            "standardized": {"line1": "1600 Amphitheatre Parkway", "line2": "", "city": "Mountain View",
                             "state": "CA", "zip": "94043", "country": "US"},
            "latitude": 37.422,
            "longitude": -122.084,
            "deliverability": "residential",
            "confidence": 1.0,
            "degraded": False,
            "cache_key": "<sha256>",
        },
        "price_paid": price,
        "transaction_id": "0x<settlement transaction hash>",
    }
    return {
        "info": {
            "input": {
                "type": "http",
                "method": "POST",
                "bodyType": "json",
                "body": EXAMPLE_REQUEST,
                # D96/Bazaar : le catalogue v2 exige que l'entree soit marquee
                # decouvrable ; sans ce champ, le catalogue peut ignorer la fiche.
                "discoverable": True,
            },
            "output": {"type": "json", "example": exemple_reponse},
        },
        "schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "input": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "const": "http"},
                        "method": {"type": "string", "enum": ["POST"]},
                        "bodyType": {"type": "string", "enum": ["json"]},
                        "body": _inline(ValidationRequest.model_json_schema()),
                        "discoverable": {"type": "boolean", "const": True},
                    },
                    "required": ["type", "method", "bodyType", "body", "discoverable"],
                    "additionalProperties": False,
                },
                "output": {
                    "type": "object",
                    "properties": {"type": {"type": "string"}, "example": {"type": "object"}},
                    "required": ["type"],
                },
            },
            "required": ["input"],
        },
    }


def payment_required_v2(price: float, error: str | None = None, fiche: bool = True) -> dict[str, Any]:
    """`PaymentRequired` v2 de `/validate` ; la fiche du catalogue seulement si `fiche`."""
    offre = {
        "x402Version": 2,
        "error": motif(error) or "Payment required to access this resource",
        "resource": {
            "url": resource_url(),
            "description": description(price),
            "mimeType": "application/json",
            "serviceName": SERVICE_NAME,
            "tags": TAGS,
        },
        "accepts": [payment_requirements_v2(price)],
    }
    if fiche:
        offre["extensions"] = {"bazaar": bazaar_extension(price)}
    return offre


def payment_required_header(price: float, error: str | None = None, fiche: bool = True) -> str:
    """Valeur de `PAYMENT-REQUIRED` : base64 du `PaymentRequired` v2, JSON compact."""
    corps = json.dumps(payment_required_v2(price, error, fiche), separators=(",", ":"), ensure_ascii=True)
    return base64.b64encode(corps.encode()).decode()


def payment_required_mcp(tool: str, price: float, error: str | None = None) -> dict[str, Any]:
    """`PaymentRequired` v2 du transport MCP (D78) : la ressource est l'outil.

    Mêmes règles que la fiche HTTP, sans l'extension `bazaar` : un outil MCP se
    fait découvrir par la carte `/.well-known/mcp-server` et par le registre MCP
    (`server.json`), pas par le catalogue HTTP. Le document est celui que le SDK
    officiel publie (réseau CAIP-2, `amount`, ressource `mcp://tool/<nom>`), et
    ses champs sont lus par les clients x402 v2 pour construire le paiement.
    """
    defaut = "Payment required to access this tool"
    return {
        "x402Version": 2,
        "error": (error[:300] if error else defaut),
        "resource": {
            "url": f"{MCP_RESOURCE_PREFIX}{tool}",
            "description": description(price),
            "mimeType": "application/json",
        },
        "accepts": [payment_requirements_v2(price)],
    }


def settlement_response(
    transaction: str | None,
    payer: str | None,
    success: bool = True,
    error_reason: str | None = None,
) -> dict[str, Any]:
    """`SettlementResponse` lisible dans `result._meta["x402/payment-response"]` (D78).

    Le réseau est toujours au format CAIP-2 (`eip155:8453`) : le transport MCP
    est v2 par construction, même quand le paiement vient d'un client v1 par
    l'en-tête `X-PAYMENT` (compatibilité D37).
    """
    body: dict[str, Any] = {
        "success": success,
        "transaction": transaction or "",
        "network": network_v2(),
    }
    if payer:
        body["payer"] = payer
    if not success:
        body["errorReason"] = motif(error_reason) or "payment_failed"
    return body



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

    Le payload brut est rendu tel quel : c'est lui qui part au facilitateur. En
    v2 (D77), le schéma et le réseau choisis sont dans `accepted` ; ils sont lus
    là, sans rien ajouter au payload (un facilitateur v2 refuse les clés inconnues).
    """
    if not value:
        raise InvalidPayment("en-tete X-PAYMENT absent")
    try:
        payload = json.loads(_b64decode(value.strip()))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidPayment(f"en-tete X-PAYMENT non JSON : {exc}") from exc
    return parse_payment_payload(payload)


def parse_payment_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Valide un `PaymentPayload` **déjà décodé**, d'où qu'il vienne.

    Depuis D78 deux transports portent le même document : l'en-tête HTTP
    (`X-PAYMENT`, `PAYMENT-SIGNATURE`) et `params._meta["x402/payment"]` du
    transport MCP. La validation est donc faite ici, une seule fois : version,
    schéma, réseau, présence du champ `payload`.
    """
    if not isinstance(payload, dict):
        raise InvalidPayment("PaymentPayload : objet JSON attendu")

    version = payload.get("x402Version")
    if version not in (1, 2):
        raise InvalidPayment(f"x402Version non supportee : {version!r}")

    offre = offer_of(payload)
    if offre.get("scheme") != SCHEME:
        raise InvalidPayment(f"schema de paiement non supporte : {offre.get('scheme')!r}")
    network = str(offre.get("network", ""))
    if network not in NETWORKS:
        raise InvalidPayment(f"reseau non supporte : {network!r}")

    payload_data = payload.get("payload")
    if not isinstance(payload_data, dict):
        raise InvalidPayment("champ payload absent du PaymentPayload")
    return payload


def offer_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Schéma et réseau choisis : dans `accepted` en v2, à la racine en v1."""
    if payload.get("x402Version") == 2:
        accepted = payload.get("accepted")
        return accepted if isinstance(accepted, dict) else {}
    return payload


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
        minimum = int(requirements.get("amount") or requirements["maxAmountRequired"])
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

    offre = offer_of(payload)
    if "amount" in requirements:
        # v2 : l'offre que le client dit accepter doit être celle que nous publions (comme le SDK officiel).
        for champ in ("scheme", "network", "amount", "asset", "payTo"):
            if str(offre.get(champ, "")).lower() != str(requirements[champ]).lower():
                return False, f"offre acceptee differente de l'offre publiee ({champ})", {}
    elif str(offre.get("network", "")) not in NETWORKS:
        return False, f"reseau non supporte : {offre.get('network')!r}", {}

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
    version: int = 1,
) -> str:
    """`X-PAYMENT-RESPONSE` (v1) ou `PAYMENT-RESPONSE` (v2) : base64 d'un `SettlementResponse`."""
    body: dict[str, Any] = {
        "success": success,
        "transaction": transaction or "",
        "network": settings.x402_network if version == 1 else network_v2(),
    }
    if payer:
        body["payer"] = payer
    if not success:
        body["errorReason"] = motif(error_reason) or "payment_failed"
    return base64.b64encode(json.dumps(body).encode()).decode()


def settlement_headers(version: int | None, transaction: str | None, payer: str | None,
                       success: bool = True, error_reason: str | None = None) -> dict[str, str]:
    """Le règlement dans l'en-tête de la version du client ; dans les deux si elle est inconnue."""
    noms = {1: PAYMENT_RESPONSE_HEADER, 2: PAYMENT_RESPONSE_HEADER_V2}
    return {noms[v]: settlement_header(transaction, payer, success, error_reason, v)
            for v in ((version,) if version in noms else (1, 2))}


@dataclass
class PaymentDecision:
    """Verdict d'un paiement : de quoi répondre, tracer et facturer."""

    paid: bool
    reason: str
    protocol: str = "none"
    transaction: str | None = None
    payer: str | None = None
    amount_usdc: str | None = None
    response_headers: dict[str, str] = field(default_factory=dict)

    def refusal(self, reason: str, protocol: str = "x402", version: int | None = None) -> "PaymentDecision":
        """Verdict de refus, avec l'en-tête de règlement en échec (v1, v2, ou les deux)."""
        return PaymentDecision(
            paid=False,
            reason=reason,
            protocol=protocol,
            response_headers=settlement_headers(version, None, None, success=False, error_reason=reason),
        )


def _refus_paiement(reason: str, version: int | None = None) -> PaymentDecision:
    """Refus x402 : le motif, et le reçu d'échec dans la version du client (ou les deux)."""
    return PaymentDecision(
        paid=False,
        reason=reason,
        protocol="x402",
        response_headers=settlement_headers(version, None, None, success=False, error_reason=reason),
    )


def authorize_and_settle(
    header: str | None, price: float, resource: str | None = None
) -> PaymentDecision:
    """Chemin x402 **par en-tête HTTP** : décodage → vérification locale → règlement.

    L'ordre est significatif : rien n'est envoyé au facilitateur avant que la
    signature, la destination, le montant et la fenêtre soient conformes à
    l'offre publiée. Un facilitateur lent ou en panne ne peut donc pas servir
    de prétexte pour rendre le service sans paiement vérifié.
    """
    wallet = settings.cdp_wallet_address or ""
    if not ADDRESS_RE.match(wallet):
        return _refus_paiement("aucune adresse d'encaissement utilisable configuree")

    try:
        payload = parse_payment_header(header)
    except InvalidPayment as exc:
        return _refus_paiement(str(exc))
    return settle_payment(payload, price, resource)


def settle_payment(
    payload: dict[str, Any], price: float, resource: str | None = None
) -> PaymentDecision:
    """Vérifie hors ligne puis règle un `PaymentPayload` **déjà validé**.

    Unique chemin d'encaissement des deux transports (D78) : l'en-tête HTTP
    (`authorize_and_settle`) et le transport MCP (`params._meta["x402/payment"]`).
    Le payload est celui que le client a signé, inchangé ; seule l'offre
    confrontée dépend de sa version (v1 ou v2).
    """
    wallet = settings.cdp_wallet_address or ""
    if not ADDRESS_RE.match(wallet):
        return _refus_paiement("aucune adresse d'encaissement utilisable configuree")

    # D77 : le facilitateur reçoit l'offre dans la version du payload.
    version = payload["x402Version"]
    requirements = payment_requirements_v2(price) if version == 2 else payment_requirements(price, resource)
    ok, reason, details = verify_authorization(payload, requirements)
    if not ok:
        logger.warning("paiement x402 refuse (local) : %s", reason)
        return _refus_paiement(reason, version=version)

    nonce = details["nonce"]
    with _SETTLED_LOCK:
        if nonce in _SETTLED:
            return _refus_paiement(f"autorisation deja reglee (nonce {nonce[:18]}...)", version=version)

    if settings.x402_facilitator_verify:
        ok, reason = facilitator_verify(payload, requirements)
        if not ok:
            logger.warning("paiement x402 refuse (facilitateur) : %s", reason)
            return _refus_paiement(reason, version=version)

    transaction: str | None = None
    payer = details["payer"]
    if settings.x402_require_settlement:
        ok, reason, transaction, payer = facilitator_settle(payload, requirements)
        if not ok:
            logger.warning("paiement x402 refuse (reglement) : %s", reason)
            return _refus_paiement(reason, version=version)
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
        response_headers=settlement_headers(version, transaction, payer or details["payer"]),
    )
