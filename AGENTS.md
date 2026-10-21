# Guide pour agents autonomes — Address Validator API

Ce document s'adresse aux agents qui decouvrent ce service seuls. Il dit ce qui
repond, ce que cela coute, et surtout ce qui n'existe pas.

## En une phrase

Une adresse postale entre, une adresse standardisee sort : POST
https://kder-ai.com/validate, 0,02 USDC par appel.

## Sequence d'integration recommandee

1. Lire https://kder-ai.com/manifest : les schemas d'entree et de sortie y sont
   derives des modeles Pydantic de l'application, donc jamais desynchronises.
2. Lire https://kder-ai.com/health avant une campagne d'appels.
3. Envoyer l'adresse sur https://kder-ai.com/validate avec l'en-tete
   `X-PAYMENT` (voir « Payer » ci-dessous).
4. Toujours lire `degraded` dans la reponse : si `degraded` vaut `true`, le
   geocodage a echoue et `confidence` est basse ; la validation doit etre rejouee
   plus tard, pas presentee comme un resultat.

Si l'agent parle MCP plutot que HTTP : lire la carte
https://kder-ai.com/.well-known/mcp-server, appeler `initialize` puis
`tools/list` sur https://kder-ai.com/mcp (gratuit), puis `tools/call` en
presentant le paiement. Un appel sans paiement rend un **resultat d'outil**
`isError: true` dont `structuredContent` est l'offre x402 v2 ; le client MCP
renvoie alors le `PaymentPayload` v2 dans `params._meta["x402/payment"]` et
recoit le reglement dans `result._meta["x402/payment-response"]`.

## Payer et appeler validate_address (MCP) - copier-coller

Trois etapes, copier-coller : lire l'offre (gratuit), signer EIP-3009, renvoyer le meme `tools/call` avec `params._meta["x402/payment"]`. Le code lit le prix et l'offre a l'execution : rien n'est code en dur.

```python
# pip install requests eth-account
import os, time, requests
from eth_account import Account

BASE = "https://kder-ai.com"
ARGS = {"address": {"street": "1600 Amphitheatre Parkway", "city": "Mountain View",
                    "state": "CA", "zip": "94043", "country": "US"}}

def mcp(method, params):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return requests.post(f"{BASE}/mcp", json=body, timeout=30).json()

# 1) FREE: read the offer. An unpaid tools/call returns result.isError + structuredContent.
offer = mcp("tools/call", {"name": "validate_address", "arguments": ARGS})["result"]["structuredContent"]
accept = offer["accepts"][0]

# 2) SIGN: EIP-3009 TransferWithAuthorization on Base (USDC). Domain comes from the offer.
key = os.environ["EVM_PRIVATE_KEY"]
auth = {"from": Account.from_key(key).address, "to": accept["payTo"],
        "value": accept["amount"], "validAfter": int(time.time()) - 60,
        "validBefore": int(time.time()) + 240, "nonce": "0x" + os.urandom(32).hex()}
typed = {
    "primaryType": "TransferWithAuthorization",
    "domain": {"name": accept["extra"]["name"], "version": accept["extra"]["version"],
               "chainId": 8453, "verifyingContract": accept["asset"]},
    "types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                               {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
              "TransferWithAuthorization": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                                            {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
                                            {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}]},
    "message": {"from": auth["from"], "to": auth["to"], "value": int(auth["value"]),
                "validAfter": auth["validAfter"], "validBefore": auth["validBefore"], "nonce": auth["nonce"]},
}
signature = Account.sign_typed_data(key, full_message=typed).signature
payment = {"x402Version": 2, "resource": offer["resource"], "accepted": accept,
           "payload": {"signature": "0x" + signature.hex().removeprefix("0x"),
                       "authorization": auth}}

# 3) PAY + CALL: the same tools/call, with the payment in params._meta.
paid = mcp("tools/call", {"name": "validate_address", "arguments": ARGS,
                          "_meta": {"x402/payment": payment}})["result"]
print(paid["structuredContent"])   # validated address; receipt in paid["_meta"]
```

## Payer sans se tromper

- En-tete attendu : `X-PAYMENT`, base64 d'un `PaymentPayload` x402 v1 dont le
  champ `payload.authorization` porte `from`, `to`, `value`, `validAfter`,
  `validBefore`, `nonce`, et `payload.signature` la signature EIP-712
  (`TransferWithAuthorization`, EIP-3009) de ces champs.
- L'offre exacte (reseau, jeton, montant, destination, domaine `name`/`version`)
  est publiee dans `accepts[]` du 402 : la recopier telle quelle, ne rien deviner.
- Un client x402 v2 lit la meme offre dans l'en-tete `PAYMENT-REQUIRED` du 402
  (reseau `eip155:8453`, champ `amount`, extension `bazaar`), paie par l'en-tete
  `PAYMENT-SIGNATURE` et recoit le reglement dans `PAYMENT-RESPONSE`. Le corps du
  402 reste en v1 pour les clients v1.
- Un client **MCP** x402 v2 met le meme `PaymentPayload` (`x402Version: 2`,
  offre publiee dans `accepted`) dans `params._meta["x402/payment"]` et lit le
  reglement dans `result._meta["x402/payment-response"]` (reseau `eip155:8453`).
  L'en-tete `X-PAYMENT` reste accepte pour un client v1 ; un outil non paye
  repond par un resultat d'outil `isError`, jamais par un code 402.
- Le service ne rend le resultat qu'apres reglement : la reponse 200 porte
  `X-PAYMENT-RESPONSE` (base64 de `{success, transaction, network, payer}`).
- Un 402 n'est pas une erreur de l'agent : c'est l'absence de paiement valide, et
  le corps dit exactement pourquoi (`error`). En MCP, ce refus n'est pas un code
  HTTP : c'est `result.isError` avec l'offre dans `result.structuredContent`.
- Les paiements sont dedupliques par hash de reglement : rejouer la meme
  autorisation ne double pas la facturation, elle est refusee.
- Un agent n'a besoin d'aucun compte : le paiement x402 suffit. L'autre voie,
  l'abonnement par cle d'API (en-tete `X-API-Key`), sert les boutiques : compte
  sur https://kder-ai.com/signup, offres sur https://kder-ai.com/pricing,
  integration sur https://kder-ai.com/guide. Si `X-API-Key` et `X-PAYMENT` sont
  presents, la cle est prioritaire et rien n'est encaisse en USDC.

## Ce qui n'existe pas

- Aucune ressource ni invite MCP (`resources/list` et `prompts/list` rendent des
  listes vides), aucun flux SSE (`GET /mcp` repond 405), aucune session
  (`Mcp-Session-Id` n'est jamais emis) : le serveur MCP est sans etat, avec un
  seul outil.
- Aucun endpoint de remboursement ni support conversationnel : le contact est
  celui publie dans https://kder-ai.com/.well-known/security.txt et dans le champ
  `contact.email` du manifeste.
- Le tableau de bord d'exploitation n'est pas public.

## Bonnes pratiques

- Verification : les adresses des Etats-Unis passent par SmartyStreets (USPS DPV) ;
  celles de France et d'outre-mer par la Base Adresse Nationale (BAN) ; ailleurs,
  la verification reste partielle. Un refus `402` porte dans `accepts[0]` le prix
  et le `outputSchema` de la reponse ; `network` figure aussi a la racine du corps
  (D80).
- Pas plus de 10 requetes par seconde et par IP (HTTP 429 au-dela).
- Le volume de refus est publie en agregat : https://kder-ai.com/metrics/revenue.
- N'envoyer que les composantes de l'adresse : l'API n'accepte ni nom ni e-mail,
  et le service ne conserve pas l'adresse dans ses journaux.

## Pour les mainteneurs du service

Ces fichiers de decouverte sont servis en statique par le reverse proxy. Toute
route citee sous `kder-ai.com` doit exister et repondre reellement : un chemin
cite qui renvoie 404 transforme la decouverte en fausse promesse.
