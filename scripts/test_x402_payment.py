#!/usr/bin/env python
"""Recette reelle du paiement x402 — de bout en bout, avec de vrais USDC.

Trois modes, du plus sûr au plus engageant :

1. **Plan** (défaut, aucun secret) : lit l'offre publiée par le service
   (`GET /manifest` et le corps `402`), vérifie que les deux sont **identiques**,
   et affiche ce qui sera signé. C'est la preuve que l'installation répond le
   contrat x402 v1 attendu, avant toute dépense.

2. **`--check-domain`** : lit sur la chaîne (`eth_call`, Base) le domaine EIP-712
   du jeton USDC — `name()`, `version()`, `symbol()`, `decimals()` — et le compare
   à `X402_DOMAIN_NAME` / `X402_DOMAIN_VERSION` / `X402_USDC_CONTRACT`. Une
   divergence rendrait **toute** signature honnête invalide : c'est le contrôle à
   passer avant la première vente.

3. **`--pay`** (+ `--private-key 0x…` ou `X402_TEST_PRIVATE_KEY`) : paiement réel
   — `X-PAYMENT`, règlement par le facilitateur, vérification du 200 et de
   `X-PAYMENT-RESPONSE`. **Ce mode dépense 0.02 USDC** et exige un portefeuille
   acheteur financé en USDC sur Base.

Usage :

    .venv\\Scripts\\python.exe scripts\\test_x402_payment.py
    .venv\\Scripts\\python.exe scripts\\test_x402_payment.py --check-domain
    $env:X402_TEST_PRIVATE_KEY="0x..."; .venv\\Scripts\\python.exe scripts\\test_x402_payment.py --pay

Codes de retour : 0 conforme, 1 échec, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.x402 import eip712_digest, payment_requirements  # noqa: E402

settings = get_settings()

ADDRESS = {
    "street": "1600 Amphitheatre Parkway",
    "city": "Mountain View",
    "state": "CA",
    "zip": "94043",
    "country": "US",
}
BODY = {"address": ADDRESS}

#: Sélecteurs de fonctions (4 premiers octets du keccak du prototype).
SELECTORS = {
    "name()": "0x06fdde03",
    "version()": "0x54fd4d50",
    "symbol()": "0x95d89b41",
    "decimals()": "0x313ce567",
}


def eth_call(to: str, selector: str) -> bytes:
    """`eth_call` sur le nœud JSON-RPC configuré. Lève si le nœud refuse."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": to, "data": selector}, "latest"],
    }
    with httpx.Client(timeout=10.0) as client:
        response = client.post(settings.base_rpc_url, json=payload)
        response.raise_for_status()
        body = response.json()
    if "error" in body:
        raise RuntimeError(f"eth_call {selector} : {body['error']}")
    return bytes.fromhex(body["result"][2:])


def check_domain() -> int:
    """Compare le domaine EIP-712 annoncé à celui du contrat USDC, on-chain."""
    from eth_abi import decode

    print(f"Contrat annonce : {settings.x402_usdc_contract} (reseau {settings.x402_network})")
    print(f"RPC             : {settings.base_rpc_url}")
    values: dict[str, str] = {}
    for label, selector in SELECTORS.items():
        try:
            raw = eth_call(settings.x402_usdc_contract, selector)
        except Exception as exc:  # noqa: BLE001
            print(f"ECHEC {label} : {type(exc).__name__}: {exc}")
            return 1
        values[label] = str(decode(["uint8"], raw)[0]) if label == "decimals()" else decode(["string"], raw)[0]
        print(f"{label:12s} = {values[label]}")

    attendu = {
        "name()": settings.x402_domain_name,
        "version()": settings.x402_domain_version,
        "decimals()": "6",
    }
    ecarts = {cle: (values[cle], valeur) for cle, valeur in attendu.items() if values[cle] != valeur}
    if ecarts:
        for champ, (obtenu, voulu) in ecarts.items():
            print(f"ECART {champ} : chaine={obtenu!r} configuration={voulu!r}")
        print("DOMAIN FAIL l'offre annonce un domaine que le contrat ne signe pas")
        return 1
    print("DOMAIN OK le domaine annonce est celui du contrat")
    return 0


def show_plan(base: str) -> int:
    """Vérifie que /manifest, le corps 402 et la carte MCP décrivent la même offre."""
    with httpx.Client(base_url=base, timeout=15.0) as client:
        manifest = client.get("/manifest").json()
        unpaid = client.post("/validate", json=BODY)
        card = client.get("/.well-known/mcp-server")

    body = unpaid.json()
    if unpaid.status_code != 402:
        print(f"ECHEC attente d'un 402 sans paiement, recu HTTP {unpaid.status_code}")
        return 1

    offer = body["accepts"][0]
    print(f"Service         : {base}")
    print(f"x402Version     : {body['x402Version']}")
    print(f"Offre           : {json.dumps(offer, indent=2)}")
    if manifest["payment"]["accepts"][0] != offer:
        print("ECHEC l'offre du manifeste differe de celle du corps 402")
        return 1
    if card.status_code != 200 or card.json()["endpoint"] != f"{base}/mcp":
        print(f"ECHEC carte MCP incoherente (HTTP {card.status_code})")
        return 1
    for champ in ("scheme", "network", "maxAmountRequired", "asset", "payTo", "resource"):
        if champ not in offer:
            print(f"ECHEC champ obligatoire absent de l'offre : {champ}")
            return 1

    print("PLAN OK manifeste, corps 402 et carte MCP decrivent la meme offre")
    print("Pour payer reellement : X402_TEST_PRIVATE_KEY=0x... puis --pay")
    return 0


def pay(base: str, private_key: str) -> int:
    """Paiement réel : signe l'autorisation, présente `X-PAYMENT`, vérifie le règlement."""
    import base64

    from eth_keys import keys

    raw = private_key[2:] if private_key.startswith("0x") else private_key
    wallet = keys.PrivateKey(bytes.fromhex(raw))
    buyer = wallet.public_key.to_address()

    with httpx.Client(base_url=base, timeout=30.0) as client:
        unpaid = client.post("/validate", json=BODY)
        if unpaid.status_code != 402:
            print(f"ECHEC attente d'un 402, recu HTTP {unpaid.status_code}")
            return 1
        offer = unpaid.json()["accepts"][0]

        now = int(time.time())
        authorization = {
            "from": buyer,
            "to": offer["payTo"],
            "value": offer["maxAmountRequired"],
            "validAfter": now - 60,
            "validBefore": now + int(offer["maxTimeoutSeconds"]) - 60,
            # Nonce neuf a chaque tentative : EIP-3009 le consomme au reglement.
            "nonce": "0x" + os.urandom(32).hex(),
        }
        digest = eip712_digest(payment_requirements(settings.x402_price), authorization)
        signature = "0x" + bytes(wallet.sign_msg_hash(digest).to_bytes()).hex()
        header = base64.b64encode(
            json.dumps(
                {
                    "x402Version": 1,
                    "scheme": offer["scheme"],
                    "network": offer["network"],
                    "payload": {"signature": signature, "authorization": authorization},
                }
            ).encode()
        ).decode()

        print(f"Acheteur        : {buyer}")
        print(
            f"Montant signe   : {authorization['value']} unites USDC "
            f"(max {offer['maxAmountRequired']}) vers {offer['payTo']}"
        )
        paid = client.post("/validate", json=BODY, headers={"X-PAYMENT": header})

    print(f"Reponse         : HTTP {paid.status_code}")
    receipt = paid.headers.get("X-PAYMENT-RESPONSE")
    if not receipt:
        print(f"ECHEC aucun X-PAYMENT-RESPONSE : {paid.text[:400]}")
        return 1
    decoded = json.loads(base64.b64decode(receipt + "==="))
    print(f"Reglement       : {json.dumps(decoded)}")
    if paid.status_code != 200 or decoded.get("success") is not True or not decoded.get("transaction"):
        print("ECHEC paiement non abouti (aucune transaction encaissee)")
        return 1
    print(f"RESULT          : {json.dumps(paid.json()['result'], ensure_ascii=False)[:300]}")
    print("PAYMENT OK service rendu apres encaissement")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://kder-ai.com", help="Base du service a tester")
    parser.add_argument("--pay", action="store_true", help="Paiement reel (depense 0.02 USDC)")
    parser.add_argument(
        "--check-domain", action="store_true", help="Compare le domaine EIP-712 au contrat on-chain"
    )
    parser.add_argument("--private-key", default=os.environ.get("X402_TEST_PRIVATE_KEY", ""))
    args = parser.parse_args(argv)

    if args.check_domain:
        return check_domain()
    if args.pay:
        key = args.private_key or os.environ.get("X402_TEST_PRIVATE_KEY", "")
        if not key:
            print("USAGE --pay exige --private-key 0x... ou X402_TEST_PRIVATE_KEY")
            return 2
        return pay(args.url.rstrip("/"), key)
    return show_plan(args.url.rstrip("/"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
