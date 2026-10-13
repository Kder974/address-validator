#!/usr/bin/env python
"""Vérification bout-en-bout réelle de l'API (harnais de recette).

Ce script interroge une instance **déjà démarrée** et vérifie les critères de
succès observables de l'extérieur (codes HTTP, corps de réponse, cache).

    # 1. démarrer le serveur
    .venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000

    # 2. dans un autre terminal
    .venv\\Scripts\\python.exe scripts\\e2e_check.py
    .venv\\Scripts\\python.exe scripts\\e2e_check.py http://127.0.0.1:8123

Sortie : tableau PASS/FAIL + code de retour 0 (tout vert) ou 1.

Régime (W1) : la voie à secret partagé (`X-Payment-Proof`, D15) n'est servie que
dans une **recette déclarée** (`TEST_MODE=true` côté serveur). Le harnais lit
donc `TEST_MODE` dans son propre environnement, qui doit être celui du serveur.
Sans recette déclarée, les contrôles qui exigent une preuve acceptée (§5 et son
cache §6) sont annoncés comme *non applicables* au lieu d'un faux PASS : la
vérification on-chain, elle, ne se simule pas depuis l'extérieur (D27).
"""

import os
import sys
import time
from pathlib import Path

# Lancé depuis `scripts/`, le dossier racine n'est pas dans sys.path : on
# l'ajoute pour pouvoir importer le paquet `app` (même secret que le serveur).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.payment import build_payment_proof  # noqa: E402


BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

ADDRESS = {
    "street": "1600 Amphitheatre Parkway",
    "city": "Mountain View",
    "state": "CA",
    "zip": "94043",
    "country": "US",
}
PAYLOAD = {"address": ADDRESS}

RESULTS: list[tuple[bool, str]] = []

# Le harnais partage le régime du serveur : hors recette déclarée, une preuve à
# secret partagé est refusée (W1). Les contrôles qui en dépendent sont alors
# sautés explicitement, jamais validés par défaut.
RECETTE = os.environ.get("TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


def check(label: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((bool(condition), label))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  -> {detail}" if detail else ""))


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=30.0)

    print(f"\n=== Recette bout-en-bout : {BASE} ===\n")
    print(
        "  régime : "
        + (
            "RECETTE DÉCLARÉE (TEST_MODE=true) — preuves à secret partagé acceptées"
            if RECETTE
            else "PRODUCTION (TEST_MODE absent) — seul un règlement on-chain ouvre un 200"
        )
    )

    try:
        client.get("/")
    except Exception as exc:  # noqa: BLE001
        print(f"Serveur injoignable sur {BASE} : {exc}")
        return 1

    print("1. Endpoints de service")
    root = client.get("/")
    check("GET / -> 200", root.status_code == 200, f"HTTP {root.status_code}")

    manifest = client.get("/manifest")
    mbody = manifest.json()
    check("GET /manifest -> 200", manifest.status_code == 200)
    check(
        "manifest declare x402 + 0.02 USDC",
        mbody.get("protocol") == "x402" and mbody.get("price") == 0.02,
        f"price={mbody.get('price')}",
    )

    health = client.get("/health")
    check("GET /health -> 200", health.status_code == 200)
    check(
        "  /health expose database + redis",
        "database" in health.json() and "redis" in health.json(),
    )
    print(f"       database={str(health.json().get('database'))[:48]}")
    print(f"       redis={str(health.json().get('redis'))[:48]}")

    print("\n2. Metrique d'uptime")
    metrics = client.get("/metrics")
    check("GET /metrics -> 200", metrics.status_code == 200)
    check("  uptime_seconds > 0", metrics.json().get("uptime_seconds", 0) > 0)

    print("\n3. Paiement requis (critere n.1)")
    unpaid = client.post("/validate", json=PAYLOAD)
    check(
        "POST /validate sans preuve -> 402",
        unpaid.status_code == 402,
        f"HTTP {unpaid.status_code}",
    )
    ubody = unpaid.json()
    check(
        "  402 annonce le prix 0.02 USDC",
        ubody.get("price") == 0.02 and ubody.get("currency") == "USDC",
    )
    check("  402 annonce le protocole x402", ubody.get("protocol") == "x402")

    proof = build_payment_proof("0xE2ETEST")
    headers = {"X-Payment-Proof": proof, "X-Transaction-Id": "0xe2e-tx-1"}

    print("\n4. Preuve invalide rejetee (fail-closed)")
    stale = build_payment_proof("0xE2ETEST", timestamp=1234567890)
    r_stale = client.post("/validate", json=PAYLOAD, headers={"X-Payment-Proof": stale})
    check("preuve perimee (2009) -> 402", r_stale.status_code == 402, f"HTTP {r_stale.status_code}")
    r_tampered = client.post(
        "/validate", json=PAYLOAD, headers={"X-Payment-Proof": proof[:-4] + "0000"}
    )
    check("signature falsifiee -> 402", r_tampered.status_code == 402, f"HTTP {r_tampered.status_code}")
    r_malformed = client.post(
        "/validate", json=PAYLOAD, headers={"X-Payment-Proof": "nimportequoi"}
    )
    check("preuve malformee -> 402", r_malformed.status_code == 402, f"HTTP {r_malformed.status_code}")

    print("\n5. Appel payant (critere n.2)")
    if not RECETTE:
        # Hors recette, la même preuve correctement signée doit être refusée :
        # c'est le contrôle utile quand on éprouve une instance de production.
        hors_recette = client.post("/validate", json=PAYLOAD, headers=headers)
        check(
            "  hors recette, preuve a secret partage -> 402 (W1)",
            hors_recette.status_code == 402,
            f"HTTP {hors_recette.status_code} — seul un reglement on-chain ouvre un 200",
        )
        print("       critere n.2 et mesure du cache (n.3) : non applicables sans recette declaree")
    else:
        t0 = time.time()
        paid = client.post("/validate", json=PAYLOAD, headers=headers)
        elapsed = time.time() - t0
        check(
            "POST /validate avec preuve valide -> 200",
            paid.status_code == 200,
            f"HTTP {paid.status_code} en {elapsed:.3f}s",
        )
        pbody = paid.json()
        check("  reponse contient result.standardized", "standardized" in pbody.get("result", {}))
        check("  price_paid == 0.02", pbody.get("price_paid") == 0.02)
        check(
            "  transaction_id repris de l'en-tete",
            pbody.get("transaction_id") == "0xe2e-tx-1",
        )
        check(
            "  resultat explicite (degraded est un booleen, jamais silencieux)",
            isinstance(pbody.get("result", {}).get("degraded"), bool),
            f"degraded={pbody.get('result', {}).get('degraded')}",
        )
        print(f"       line1={pbody.get('result', {}).get('standardized', {}).get('line1')!r}")
        print(f"       confidence={pbody.get('result', {}).get('confidence')}")

    print("\n6. Cache (critere n.3)")
    if not RECETTE:
        print("       mesure du cache : non applicable sans recette declaree (preuve refusee)")
    else:
        before = client.get("/metrics").json()
        t0 = time.time()
        paid2 = client.post("/validate", json=PAYLOAD, headers=headers)
        elapsed2 = time.time() - t0
        after = client.get("/metrics").json()
        check("2e appel identique -> 200", paid2.status_code == 200)
        check(
            "  meme cache_key",
            pbody["result"]["cache_key"] == paid2.json()["result"]["cache_key"],
        )
        if after["cache_hits"] > before["cache_hits"]:
            check("  cache_hit incremente (Redis actif)", True, f"ratio={after['cache_hit_ratio']}")
        else:
            check(
                "  Redis absent -> 2e appel recalcule (degradation attendue)",
                True,
                f"ratio={after['cache_hit_ratio']}",
            )
        print(f"       1er appel {elapsed:.3f}s / 2e appel {elapsed2:.3f}s")

    print("\n7. Robustesse de l'entree")
    bad = client.post("/validate", json={"address": {"street": "x"}}, headers=headers)
    check("charge utile incomplete -> 422", bad.status_code == 422, f"HTTP {bad.status_code}")

    print("\n8. Decouverte MCP et offre x402 (Lot B)")
    check(
        "  402 publie une offre x402 v1 (accepts[0].maxAmountRequired)",
        ubody.get("x402Version") == 1
        and ubody["accepts"][0]["maxAmountRequired"] == "20000"
        and ubody["accepts"][0]["network"] == "base",
        f"x402Version={ubody.get('x402Version')}",
    )
    card = client.get("/.well-known/mcp-server")
    check(
        "GET /.well-known/mcp-server -> 200 et annonce /mcp",
        card.status_code == 200 and card.json().get("endpoint", "").endswith("/mcp"),
        f"HTTP {card.status_code}",
    )
    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    check(
        "POST /mcp tools/list -> 200 (decouverte gratuite)",
        tools.status_code == 200
        and tools.json()["result"]["tools"][0]["name"] == "validate_address",
        f"HTTP {tools.status_code}",
    )
    unpaid_tool = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "validate_address", "arguments": PAYLOAD},
        },
    )
    check(
        "POST /mcp tools/call sans paiement -> 402 JSON-RPC -32002",
        unpaid_tool.status_code == 402 and unpaid_tool.json()["error"]["code"] == -32002,
        f"HTTP {unpaid_tool.status_code}",
    )
    check(
        "  le refus MCP porte la meme offre que le 402 HTTP",
        unpaid_tool.json()["error"]["data"]["accepts"][0]
        == ubody["accepts"][0],
    )
    check(
        "GET /mcp -> 405 (pas de flux SSE)",
        client.get("/mcp").status_code == 405,
    )

    failed = [label for ok, label in RESULTS if not ok]
    total = len(RESULTS)
    print(f"\n=== {total - len(failed)}/{total} verifications OK ===")
    if failed:
        print("Echecs : " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

