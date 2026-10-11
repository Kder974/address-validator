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
`tools/list` sur https://kder-ai.com/mcp (gratuit), et n'appeler `tools/call`
qu'avec l'en-tete `X-PAYMENT` (payant).

## Payer sans se tromper

- En-tete attendu : `X-PAYMENT`, base64 d'un `PaymentPayload` x402 v1 dont le
  champ `payload.authorization` porte `from`, `to`, `value`, `validAfter`,
  `validBefore`, `nonce`, et `payload.signature` la signature EIP-712
  (`TransferWithAuthorization`, EIP-3009) de ces champs.
- L'offre exacte (reseau, jeton, montant, destination, domaine `name`/`version`)
  est publiee dans `accepts[]` du 402 : la recopier telle quelle, ne rien deviner.
- Le service ne rend le resultat qu'apres reglement : la reponse 200 porte
  `X-PAYMENT-RESPONSE` (base64 de `{success, transaction, network, payer}`).
- Un 402 n'est pas une erreur de l'agent : c'est l'absence de paiement valide, et
  le corps dit exactement pourquoi (`error`).
- Les paiements sont dedupliques par hash de reglement : rejouer la meme
  autorisation ne double pas la facturation, elle est refusee.

## Ce qui n'existe pas

- Aucune ressource ni invite MCP (`resources/list` et `prompts/list` rendent des
  listes vides), aucun flux SSE (`GET /mcp` repond 405), aucune session
  (`Mcp-Session-Id` n'est jamais emis) : le serveur MCP est sans etat, avec un
  seul outil.
- Aucun abonnement, aucune cle d'API, aucun compte : le paiement est le seul
  droit d'entree.
- Aucun endpoint de remboursement ni support conversationnel : le contact est
  celui publie dans https://kder-ai.com/.well-known/security.txt et dans le champ
  `contact.email` du manifeste.
- Le tableau de bord d'exploitation n'est pas public.

## Bonnes pratiques

- Pas plus de 10 requetes par seconde et par IP (HTTP 429 au-dela).
- Le volume de refus est publie en agregat : https://kder-ai.com/metrics/revenue.
- N'envoyer que les composantes de l'adresse : l'API n'accepte ni nom ni e-mail,
  et le service ne conserve pas l'adresse dans ses journaux.

## Pour les mainteneurs du service

Ces fichiers de decouverte sont servis en statique par le reverse proxy. Toute
route citee sous `kder-ai.com` doit exister et repondre reellement : un chemin
cite qui renvoie 404 transforme la decouverte en fausse promesse.
