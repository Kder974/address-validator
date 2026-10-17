"""Tests du paiement x402 standard (D34-D36) — hors ligne, signatures reelles.

Aucun octet ne sort : les signatures EIP-712 sont produites par `eth-account`
sur une cle jetable, et le facilitateur est remplace par une fonction qui repond
comme le vrai (`/verify` puis `/settle`). Ce qui est donc mesure ici est le
contrat du protocole, pas la disponibilite de la chaine — la recette reelle se
fait avec `scripts/test_x402_payment.py` (portefeuille finance).
"""

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import x402
from app.config import get_settings
from app.main import app
from app.x402 import payment_requirements

client = TestClient(app)
settings = get_settings()

ADDRESS = {
    "street": "1600 Amphitheatre Parkway",
    "city": "Mountain View",
    "state": "CA",
    "zip": "94043",
    "country": "US",
}
PAYLOAD = {"address": ADDRESS}
NONCE = "0x" + "ab" * 32
TX = "0x" + "cd" * 32

#: Clé jetable déterministe : les tests ne dépendent d'aucun aléa.
PRIVATE_KEY = bytes.fromhex("11" * 32)


def _private():
    from eth_keys import keys

    return keys.PrivateKey(PRIVATE_KEY)


def _address() -> str:
    return _private().public_key.to_address()


def _authorization(value: str | None = None, **overrides) -> dict:
    now = int(time.time())
    authorization = {
        "from": _address(),
        "to": settings.cdp_wallet_address,
        "value": value or payment_requirements(settings.x402_price)["maxAmountRequired"],
        "validAfter": now - 60,
        "validBefore": now + 240,
        "nonce": NONCE,
    }
    authorization.update(overrides)
    return authorization


def _sign(authorization: dict) -> str:
    """Signature EIP-712 reelle de l'autorisation, sur l'offre publiee.

    `eth_keys` plutot que `eth_account` : ce dernier importe `eth_keyfile`
    (`Crypto.Protocol.KDF`), dont le repli cffi de pycryptodome casse des que le
    module natif est bloque (voir tests/keccak_fallback.py).
    """
    digest = x402.eip712_digest(payment_requirements(settings.x402_price), authorization)
    return "0x" + bytes(_private().sign_msg_hash(digest).to_bytes()).hex()


def _header(version: int = 1, authorization: dict | None = None) -> str:
    """En-tete `X-PAYMENT` : base64 d'un PaymentPayload v1 (ou v2) signe."""
    authorization = authorization or _authorization()
    body = {
        "x402Version": version,
        "scheme": "exact",
        "network": settings.x402_network if version == 1 else "eip155:8453",
        "payload": {
            "signature": _sign(authorization),
            "authorization": authorization,
        },
    }
    if version == 2:
        body["accepted"] = {
            "scheme": "exact",
            "network": "eip155:8453",
            "amount": authorization["value"],
            "asset": settings.x402_usdc_contract,
            "payTo": settings.cdp_wallet_address,
        }
    return base64.b64encode(json.dumps(body).encode()).decode()


def _decoded_header(value: str) -> dict:
    return json.loads(base64.b64decode(value + "=" * (-len(value) % 4)))


def test_402_body_is_a_v1_payment_required_document():
    """D34 : le corps du 402 est lisible par un client x402 generique."""
    response = client.post("/validate", json=PAYLOAD)
    assert response.status_code == 402
    body = response.json()

    assert body["x402Version"] == 1
    assert body["error"]
    assert len(body["accepts"]) == 1, "un agent doit voir exactement une offre"
    offer = body["accepts"][0]
    assert offer["scheme"] == "exact"
    assert offer["network"] == "base"
    assert offer["maxAmountRequired"] == "20000", "0,02 USDC en unites atomiques"
    assert offer["asset"] == settings.x402_usdc_contract
    assert offer["payTo"] == settings.cdp_wallet_address
    assert offer["resource"] == "https://kder-ai.com/validate"
    assert offer["mimeType"] == "application/json"
    assert offer["maxTimeoutSeconds"] > 0
    assert offer["extra"]["name"] and offer["extra"]["version"]
    # Cles historiques conservees (outillage interne) : le client x402 les ignore.
    assert body["price"] == 0.02 and body["currency"] == "USDC"


def test_valid_x402_payment_is_settled_then_served(facilitator):
    """D36 : le service est rendu apres encaissement, et l'encaissement le dit."""
    response = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": _header()})

    assert response.status_code == 200, response.text
    assert [suffix for suffix, _ in facilitator] == ["/verify", "/settle"], (
        "l'ordre doit etre verification puis encaissement"
    )
    receipt = _decoded_header(response.headers["X-PAYMENT-RESPONSE"])
    assert receipt["success"] is True
    assert receipt["transaction"] == TX
    assert receipt["network"] == "base"
    assert receipt["payer"].lower() == _address().lower()
    assert response.json()["transaction_id"] == TX
    assert response.json()["price_paid"] == 0.02


def test_settlement_hash_is_what_the_ledger_records(facilitator, monkeypatch):
    """D29 + D36 : le registre compte le hash regle, pas la preuve annoncee."""
    written: list[tuple] = []

    def _ecrit(tx_hash, amount, address):
        written.append((tx_hash, amount))
        return True  # D41 : le service n'est rendu que si la ligne est ecrite

    monkeypatch.setattr("app.routes.validate.record_transaction", _ecrit)
    response = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": _header()})
    assert response.status_code == 200
    assert written == [(TX, 0.02)], f"registre alimente par autre chose : {written}"


def test_altered_signature_is_refused_before_any_network_call(facilitator):
    """D35 : une signature falsifiee ne doit pas atteindre le facilitateur."""
    header = _decoded_header(_header())
    header["payload"]["signature"] = "0x" + "11" * 65
    encoded = base64.b64encode(json.dumps(header).encode()).decode()

    response = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": encoded})
    assert response.status_code == 402
    assert facilitator == [], "le facilitateur a ete appele avec une signature invalide"
    receipt = _decoded_header(response.headers["X-PAYMENT-RESPONSE"])
    assert receipt["success"] is False and receipt["errorReason"]


@pytest.mark.parametrize(
    "override, motif",
    [
        ({"to": "0x" + "99" * 20}, "destinataire"),
        ({"value": "19999"}, "montant"),
        ({"validAfter": int(time.time()) + 600}, "pas encore valide"),
    ],
)
def test_local_checks_refuse_before_paying(facilitator, override, motif):
    """D35 : destination, montant et fenetre sont verifies hors ligne."""
    header = _header(authorization=_authorization(**override))
    response = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": header})
    assert response.status_code == 402
    assert motif in response.json()["error"]
    assert facilitator == []


def test_expired_authorization_is_refused(facilitator):
    """D35 : une autorisation perimee ne va pas jusqu'au facilitateur."""
    authorization = _authorization(validBefore=int(time.time()) - 1)
    response = client.post(
        "/validate",
        json=PAYLOAD,
        headers={"X-PAYMENT": _header(authorization=authorization)},
    )
    assert response.status_code == 402
    assert "expiree" in response.json()["error"]
    assert facilitator == []


def test_settlement_failure_is_a_refusal(monkeypatch):
    """D36 : une autorisation non encaissee ne donne pas droit au service."""
    monkeypatch.setattr(
        x402,
        "_post_facilitator",
        lambda suffix, body: (
            {"isValid": True}
            if suffix == "/verify"
            else {"success": False, "errorReason": "insufficient_funds", "transaction": ""}
        ),
    )
    x402.reset_nonce_cache()
    response = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": _header()})
    x402.reset_nonce_cache()

    assert response.status_code == 402
    assert "insufficient_funds" in response.json()["error"]
    receipt = _decoded_header(response.headers["X-PAYMENT-RESPONSE"])
    assert receipt["success"] is False
    assert receipt["errorReason"]


def test_settlement_without_hash_is_a_refusal(monkeypatch):
    """D36 : un reglement sans hash n'est pas verifiable, donc il ne compte pas."""
    monkeypatch.setattr(
        x402,
        "_post_facilitator",
        lambda suffix, body: (
            {"isValid": True}
            if suffix == "/verify"
            else {"success": True, "transaction": "", "network": "base"}
        ),
    )
    x402.reset_nonce_cache()
    response = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": _header()})
    x402.reset_nonce_cache()

    assert response.status_code == 402
    assert "sans hash" in response.json()["error"]


def test_facilitator_unreachable_is_a_refusal(facilitator, monkeypatch):
    """D36/D6 : une panne du facilitateur ne rend pas le service gratuitement."""
    import httpx

    def boom(suffix: str, body: dict):
        raise httpx.ConnectError("facilitateur injoignable")

    monkeypatch.setattr(x402, "_post_facilitator", boom)
    response = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": _header()})
    assert response.status_code == 402
    assert "impossible" in response.json()["error"]


def test_the_same_authorization_cannot_be_replayed(facilitator):
    """D36 : un nonce deja regle est refuse (la garantie dure est on-chain)."""
    first = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": _header()})
    assert first.status_code == 200

    second = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": _header()})
    assert second.status_code == 402
    assert "deja reglee" in second.json()["error"]
    assert [suffix for suffix, _ in facilitator] == ["/verify", "/settle"], (
        "le rejeu ne doit pas rappeler le facilitateur"
    )


def test_v2_payload_is_accepted_and_normalized(facilitator):
    """D34 : un client v2 (CAIP-2, enveloppe `accepted`) est servi, pas rejete."""
    response = client.post(
        "/validate", json=PAYLOAD, headers={"X-PAYMENT": _header(version=2)}
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "header",
    [
        "pas-du-base64",
        base64.b64encode(b"[]").decode(),
        base64.b64encode(json.dumps({"x402Version": 9}).encode()).decode(),
        base64.b64encode(json.dumps({"x402Version": 1, "scheme": "permit2"}).encode()).decode(),
        base64.b64encode(
            json.dumps({"x402Version": 1, "scheme": "exact", "network": "solana"}).encode()
        ).decode(),
    ],
)
def test_malformed_or_unknown_payments_are_refused(facilitator, header: str):
    """D34 : hors schema (version, reseau, encodage) est refuse, jamais devine."""
    response = client.post("/validate", json=PAYLOAD, headers={"X-PAYMENT": header})
    assert response.status_code == 402
    assert facilitator == []


def test_local_keccak_fallback_matches_public_vectors():
    """Le filet local (politique Windows) doit etre un keccak correct."""
    from tests.keccak_fallback import keccak256

    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    assert keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"


#: Vecteur **externe** : charge utile publiee dans
#: `specs/schemes/exact/scheme_exact_evm.md` du depot coinbase/x402.
SPEC_SIGNATURE = (
    "0x2d6a7588d6acca505cbf0d9a4a227e0c52c6c34008c8e8986a12832597641736"
    "08a2ce6496642e377d6da8dbbf5836e9bd15092f9ecab05ded3d6293af148b571c"
)
SPEC_AUTHORIZATION = {
    "from": "0x857b06519E91e3A54538791bDbb0E22373e36b66",
    "to": "0x209693Bc6afc0C5328bA36FaF03C514EF312287C",
    "value": "10000",
    "validAfter": "1740672089",
    "validBefore": "1740672154",
    "nonce": "0xf3746613c2d920b5fdabc0856f2aeb2d4f88ee6037b8cc5d04a71a4462f13480",
}
SPEC_ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
#: Digest EIP-712 de cette charge utile : c'est **le seul** qui redonne
#: `from` en récupérant la clé publique de la signature documentée. Obtenu par
#: le calcul explicite de `app/x402.py` et confirmé par `eth_account` sur le VPS.
#: Piège relevé au passage : sur le VPS, `encode_typed_data(...).body` rend le
#: *struct hash* (`49ad6445…`), pas le digest — le domaine est porté par l'en-tête.
SPEC_DIGEST_HEX = "f256992871671abcb27ff92885a7afa46218724e5fc0bac35d050115aa1d22e6"
#: Struct hash de l'autorisation, identique à ce que `eth_account` publie
#: comme corps de message signable (deux implémentations, un seul résultat).
SPEC_STRUCT_HASH_HEX = "49ad644555c7bdf3ea4fc022a4940ae5589b230b1dde0065c5504fa19bbe39a4"


def test_external_reference_vector_recovers_the_documented_signer(monkeypatch):
    """Vecteur externe : la signature publiee par la spec x402 doit rendre `from`.

    Le domaine du document est `name = "USDC"`, `version = "2"` et
    `chainId = 84532` (Base Sepolia). Les deux premiers viennent de
    `accepts[].extra`, le troisieme du reseau : ce test mesure donc aussi que le
    nom annonce dans l'offre est bien celui qui sert a verifier la signature.
    Si le keccak ou la reconstruction EIP-712 etaient faux, l'adresse recuperee
    ne pourrait pas etre celle du document.
    """
    monkeypatch.setattr(settings, "x402_domain_name", "USDC")
    monkeypatch.setattr(settings, "x402_domain_version", "2")
    monkeypatch.setattr(settings, "x402_chain_id", 84532)

    # Le digest est compare a celui que `eth_account` produit sur le VPS pour la
    # meme charge utile : deux implementations independantes, un seul resultat.
    requirements = {"asset": SPEC_ASSET}
    digest = x402.eip712_digest(requirements, SPEC_AUTHORIZATION)
    assert digest.hex() == SPEC_DIGEST_HEX
    assert (
        x402.authorization_struct_hash(requirements, SPEC_AUTHORIZATION).hex()
        == SPEC_STRUCT_HASH_HEX
    )

    recovered = x402.recover_signer(
        {"asset": SPEC_ASSET}, SPEC_AUTHORIZATION, SPEC_SIGNATURE
    )
    assert recovered.lower() == SPEC_AUTHORIZATION["from"].lower()


# --- D77 : x402 v2 (en-têtes) et fiche du catalogue Coinbase -----------------


def _v2_header(authorization: dict | None = None, **accepte) -> str:
    """`PAYMENT-SIGNATURE` strictement conforme a specs/transports-v2/http.md."""
    authorization = authorization or _authorization()
    offre = _decoded_header(client.post("/validate", json=PAYLOAD).headers["PAYMENT-REQUIRED"])
    body = {
        "x402Version": 2,
        "resource": offre["resource"],
        "accepted": {**offre["accepts"][0], **accepte},
        "payload": {"signature": _sign(authorization), "authorization": authorization},
        "extensions": offre["extensions"],
    }
    return base64.b64encode(json.dumps(body).encode()).decode()


def test_402_publishes_the_same_offer_in_v2():
    """D77 : un client v2 lit l'offre dans `PAYMENT-REQUIRED` ; le corps reste en v1."""
    response = client.post("/validate", json=PAYLOAD)
    assert response.status_code == 402 and response.json()["x402Version"] == 1
    offre = _decoded_header(response.headers["PAYMENT-REQUIRED"])
    v1 = response.json()["accepts"][0]
    assert offre["x402Version"] == 2 and offre["error"] == "Payment required to access this resource"
    [v2] = offre["accepts"]
    assert v2["network"] == "eip155:8453" and v2["amount"] == v1["maxAmountRequired"] == "20000"
    assert (v2["payTo"], v2["asset"], v2["extra"]) == (v1["payTo"], v1["asset"], v1["extra"])
    assert offre["resource"]["url"] == "https://kder-ai.com/validate"
    assert offre["resource"]["description"].isascii() and "0.02 USDC" in offre["resource"]["description"]
    # Regles du catalogue (bazaar.md) : nom ASCII <= 32, au plus 5 etiquettes ASCII <= 32.
    assert offre["resource"]["serviceName"].isascii() and len(offre["resource"]["serviceName"]) <= 32
    assert len(offre["resource"]["tags"]) <= 5 and all(t.isascii() and len(t) <= 32 for t in offre["resource"]["tags"])
    # nginx refuse une reponse dont les en-tetes depassent son tampon (4 Ko par defaut).
    assert sum(len(k) + len(v) + 4 for k, v in response.headers.items()) < 3800


def test_bazaar_card_matches_the_route():
    """D77 : la fiche dit comment appeler `/validate`, et son exemple est accepte par la route."""
    from app.schemas import ValidationRequest

    bazaar = _decoded_header(client.post("/validate", json=PAYLOAD).headers["PAYMENT-REQUIRED"])["extensions"]["bazaar"]
    entree, schema = bazaar["info"]["input"], bazaar["schema"]["properties"]["input"]
    assert set(entree) <= set(schema["properties"]) and set(schema["required"]) <= set(entree)
    assert entree["type"] == schema["properties"]["type"]["const"] == "http"
    assert entree["method"] in schema["properties"]["method"]["enum"] and entree["bodyType"] == "json"
    ValidationRequest.model_validate(entree["body"])
    assert "$ref" not in json.dumps(bazaar), "Bazaar refuse les references hors du document"
    # Bazaar rejoue l'exemple sans payer : il doit recevoir le 402, pas une erreur.
    assert client.post("/validate", json=entree["body"]).status_code == 402


def test_unpaid_request_gets_402_even_with_an_empty_body():
    """D77 : le controle Coinbase envoie un corps vide ; sans moyen de paiement, c'est 402."""
    vide = client.post("/validate", json={})
    assert vide.status_code == 402 and "PAYMENT-REQUIRED" in vide.headers
    # Qui paie ou presente une cle garde le 422 : rien n'est encaisse sur un corps faux.
    assert client.post("/validate", json={}, headers={"X-PAYMENT": _header()}).status_code == 422
    assert client.post("/validate", json={}, headers={"X-API-Key": "av_inconnue"}).status_code == 422


def test_strict_v2_payment_is_settled_with_the_v2_offer(facilitator):
    """D77 : payload v2 de la spec -> facilitateur en v2, recu dans `PAYMENT-RESPONSE`."""
    entete = _v2_header()
    response = client.post("/validate", json=PAYLOAD, headers={"PAYMENT-SIGNATURE": entete})
    assert response.status_code == 200, response.text
    for suffix, body in facilitator:
        assert body["x402Version"] == 2
        assert body["paymentPayload"] == _decoded_header(entete), f"{suffix} : payload modifie"
        assert body["paymentRequirements"]["amount"] == "20000" and "maxAmountRequired" not in body["paymentRequirements"]
        assert body["paymentRequirements"]["network"] == "eip155:8453"
    recu = _decoded_header(response.headers["PAYMENT-RESPONSE"])
    assert recu["success"] is True and recu["transaction"] == TX and recu["network"] == "eip155:8453"
    assert "X-PAYMENT-RESPONSE" not in response.headers


def test_refused_v2_payment_says_why_in_v2(facilitator):
    """D77 : un montant trop bas est refuse avant le facilitateur, et le refus parle v2."""
    entete = _v2_header(_authorization(value="1"))
    response = client.post("/validate", json=PAYLOAD, headers={"PAYMENT-SIGNATURE": entete})
    assert response.status_code == 402 and facilitator == []
    recu = _decoded_header(response.headers["PAYMENT-RESPONSE"])
    assert recu["success"] is False and "montant insuffisant" in recu["errorReason"]
    assert "montant insuffisant" in _decoded_header(response.headers["PAYMENT-REQUIRED"])["error"]


@pytest.mark.parametrize("accepte", [{"network": "base"}, {"amount": "1"}, {"payTo": "0x" + "00" * 20}])
def test_v2_payment_for_another_offer_is_refused_locally(facilitator, accepte):
    """D77 : l'offre `accepted` doit etre celle publiee ; sinon refus avant le facilitateur."""
    response = client.post("/validate", json=PAYLOAD, headers={"PAYMENT-SIGNATURE": _v2_header(**accepte)})
    assert response.status_code == 402 and facilitator == []
    assert "offre acceptee differente" in _decoded_header(response.headers["PAYMENT-RESPONSE"])["errorReason"]


def test_refusal_402_stays_under_the_nginx_buffer(facilitator, monkeypatch):
    """D77 : un motif long (facilitateur bavard, champ client geant) ne fait pas deborder les en-tetes."""

    def bavard(suffix: str, body: dict) -> dict:
        raise RuntimeError("facilitateur /verify HTTP 400 : " + "é" * 200)

    monkeypatch.setattr(x402, "_post_facilitator", bavard)
    geant = base64.b64encode(json.dumps({"x402Version": "9" * 3000}).encode()).decode()
    for entetes in ({"PAYMENT-SIGNATURE": _v2_header()}, {"X-PAYMENT": _header()}, {"X-PAYMENT": geant}):
        response = client.post("/validate", json=PAYLOAD, headers={**entetes, "Origin": "https://agent.example"})
        assert response.status_code == 402
        assert sum(len(k) + len(v) + 4 for k, v in response.headers.items()) < 3800, entetes
        assert all(v.isascii() for v in response.headers.values())


# --- D79 : le 402 publie le vrai contrat de sortie et le reseau au niveau racine


def test_402_publie_le_schema_de_sortie_et_le_reseau():
    """Un client x402 reçoit `outputSchema` (dérivé de Pydantic) et `network`."""
    from app.schemas import ValidationResponse

    body = client.post("/validate", json=PAYLOAD).json()

    assert body["network"] == settings.x402_network
    schema = body["accepts"][0]["outputSchema"]
    assert schema is not None, "outputSchema ne doit pas être null"
    assert schema == ValidationResponse.model_json_schema()
    assert schema.get("properties", {}).get("result"), "le schéma de sortie est vide"
    # L'offre v2, elle, reste au schéma x402 v2 (pas de champ `outputSchema`).
    v2 = _decoded_header(client.post("/validate", json=PAYLOAD).headers["PAYMENT-REQUIRED"])
    assert v2["accepts"][0]["amount"] == body["accepts"][0]["maxAmountRequired"]
