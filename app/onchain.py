"""Vérification on-chain du paiement (D27) : lecture du reçu d'un transfert USDC.

D15 reste la référence du service : la preuve de paiement est un HMAC à secret
partagé, et `verify_payment` accepte cette preuve **sans** interroger la
blockchain. Cette couche ajoute une confirmation *réelle*, activée explicitement
par l'exploitant (`X402_ONCHAIN_VERIFY=true`) : la transaction citée dans
l'en-tête `X-Payment-Proof` doit alors exister sur Base, être confirmée, et
contenir un transfert USDC vers le portefeuille d'encaissement d'au moins le
prix annoncé.

Choix techniques (D27) :

* **aucun paquet web3 ni authorization EIP-3009** : le reçu est lu par JSON-RPC
  (`eth_getTransactionReceipt`) et l'événement ERC-20
  `Transfer(address,address,uint256)` est décodé à la main. La surface reste le
  `httpx` déjà présent (D2/D23) au lieu d'une nouvelle dépendance ;
* `status == 0x1` obligatoire : une transaction rejetée ne paie rien ;
* un *log* de transfert émis par le contrat USDC vers le portefeuille
  d'encaissement est exigé ; un appel direct comme un transfert relayé par un
  contrat intermédiaire émettent le même événement ;
* nombre de confirmations minimal configurable (défaut 1) ;
* **fail-closed** : hash malformé, réseau injoignable, réponse illisible, reçu
  absent, transaction rejetée, destinataire ou montant incorrects → refus.
  Aucune exception ne remonte : la fonction renvoie `(ok, raison)` pour que
  l'appelant journalise la cause exacte du refus.
"""

import logging
import re
from decimal import Decimal
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Sujet de l'événement ERC-20 Transfer(address,address,uint256) : keccak-256,
# identique sur toutes les chaînes EVM.
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# USDC est un jeton à 6 décimales : 0,02 USDC = 20 000 unités entières.
USDC_DECIMALS = 6

# Timeout borné, sur le modèle du fournisseur (D23) : la vérification du paiement
# ne doit jamais transformer une requête payante en attente longue.
RPC_TIMEOUT = httpx.Timeout(connect=2.0, read=4.0, write=4.0, pool=2.0)

TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def usdc_units(price: float) -> int:
    """Prix en unités USDC entières, sans erreur d'arrondi flottant."""
    return int(Decimal(str(price)) * 10**USDC_DECIMALS)


def _usdc_str(units: int) -> str:
    return f"{Decimal(units) / 10**USDC_DECIMALS:.6f}"


def _rpc(method: str, params: list[Any]) -> Any:
    """Appel JSON-RPC. Lève en cas d'erreur réseau ou de réponse d'erreur."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    with httpx.Client(timeout=RPC_TIMEOUT) as client:
        response = client.post(settings.base_rpc_url, json=payload)
        response.raise_for_status()
        body = response.json()
    if isinstance(body, dict) and body.get("error"):
        raise RuntimeError(f"RPC {method} : {body['error']}")
    return body.get("result") if isinstance(body, dict) else None


def _topics_address(topic: str) -> str:
    """Adresse (20 derniers octets) contenue dans un sujet indexé."""
    return "0x" + topic[-40:].lower()


def transferred_to(receipt: dict, recipient: str) -> int | None:
    """Total reçu par `recipient` depuis le contrat USDC, ou None si absent."""
    contract = settings.x402_usdc_contract.lower()
    wanted = recipient.lower()
    total = 0
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        if (
            str(log.get("address", "")).lower() != contract
            or len(topics) != 3
            or str(topics[0]).lower() != TRANSFER_TOPIC
            or _topics_address(str(topics[2])) != wanted
        ):
            continue
        total += int(log.get("data", "0x0"), 16)
    return total or None


def check_usdc_transfer(tx_hash: str) -> tuple[bool, str]:
    """Vérifie qu'un transfert USDC confirmé a payé le service.

    `fail-closed` : toute anomalie renvoie `(False, raison)` ; `(True, raison)`
    n'est renvoyé que pour un reçu confirmé contenant un transfert USDC d'au
    moins `X402_PRICE` vers `CDP_WALLET_ADDRESS`.
    """
    if not TX_HASH_RE.match(tx_hash or ""):
        return False, "hash de transaction malforme"

    wallet = settings.cdp_wallet_address
    if not wallet:
        return False, "CDP_WALLET_ADDRESS absent : aucun destinataire a verifier"
    if not ADDRESS_RE.match(wallet):
        # Un destinataire malformé rend le rapprochement des *logs* impossible :
        # mieux vaut refuser que comparer une adresse tronquée.
        return False, "CDP_WALLET_ADDRESS malforme : aucun destinataire verifiable"

    minimum = usdc_units(settings.x402_price)
    try:
        receipt = _rpc("eth_getTransactionReceipt", [tx_hash])
        if not isinstance(receipt, dict):
            return False, "transaction inconnue de la chaine"
        if receipt.get("status") != "0x1":
            return False, f"transaction non confirmee (status={receipt.get('status')})"

        received = transferred_to(receipt, wallet)
        if received is None:
            return False, f"aucun transfert USDC vers {wallet}"
        if received < minimum:
            return False, (
                f"montant insuffisant : {_usdc_str(received)} USDC "
                f"< {_usdc_str(minimum)} USDC"
            )

        latest = int(_rpc("eth_blockNumber", []), 16)
        confirmations = latest - int(receipt.get("blockNumber", "0x0"), 16) + 1
        if confirmations < settings.x402_min_confirmations:
            return False, (
                f"{confirmations} confirmation(s) "
                f"< {settings.x402_min_confirmations} requise(s)"
            )
    except Exception as exc:  # refus fermé : jamais d'exception vers l'appelant
        return False, f"verification on-chain impossible : {type(exc).__name__}: {exc}"

    return True, f"{_usdc_str(received)} USDC recus, {confirmations} confirmation(s)"
