# Recette du paiement réel — x402 sur Base (USDC)

Plan de recette du **dernier point non prouvé** du Lot B : un paiement x402
**réellement encaissé** (D34-D36). Le reste de la chaîne a été vérifié en
production le 11/10/2026 (`TEST_REPORT.md`, `GATES.md`, portes D34-D39).

Statut à ce jour : **non exécuté — aucun USDC dépensé**. La commande est prête ;
elle attend un portefeuille acheteur financé. Coût total de la recette :
**0,02 USDC** (plus le gaz du facilitateur, qui ne revient pas à l'acheteur).

## 1. Ce que la recette doit prouver

| # | Étape | Preuve attendue |
|---|---|---|
| 1 | Requête non payée | `POST /validate` → **402** avec `accepts[0]` (schéma `exact`, réseau `base`, `maxAmountRequired` `20000`, `payTo` `0x0fe9…d339`) |
| 2 | Signature hors chaîne | L'acheteur signe une autorisation **EIP-712 / EIP-3009** `TransferWithAuthorization` — aucun gaz payé par l'acheteur |
| 3 | Vérification facilitateur | Le service appelle CDP `/verify` puis `/settle` (JWT ES256 signé avec `CDP_API_KEY_ID`/`CDP_API_KEY_SECRET`) |
| 4 | Service rendu après encaissement | **200** + en-tête `X-PAYMENT-RESPONSE` (base64) contenant `success: true` et un hash de transaction |
| 5 | Encaissement réel | Le solde du portefeuille acheteur baisse de **0,02 USDC** ; la transaction est visible sur `https://basescan.org` |
| 6 | Comptabilité | `GET /metrics/revenue` : `validations_paid` et `revenue_usdc` augmentent de 1 et 0,02 |

## 2. Préparer le portefeuille acheteur (une seule fois)

1. **Créer un portefeuille dédié**, distinct de tout portefeuille de production :
   dans Coinbase Wallet (ou MetaMask), « Créer un compte » → noter l'adresse
   publique `0x…`. Ce portefeuille ne sert **qu'** à la recette.
2. **Le financer en USDC sur Base.** Deux voies courantes :
   - *Coinbase Wallet* : acheter ou recevoir des USDC, puis choisir le réseau
     **Base** (pas Ethereum mainnet) ;
   - *Bridge interne* : envoyer des USDC depuis un échange vers l'adresse, en
     sélectionnant le réseau **Base**.
   Montant conseillé : **0,05 USDC** (0,02 pour la recette, le reste comme marge).
   Le contrat USDC utilisé par le service est `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913`
   (USDC natif Base, 6 décimales).
3. **Exporter la clé privée** du compte (Coinbase Wallet : Paramètres → Sécurité →
   Exporter la clé privée). C'est un secret : il ne doit **jamais** entrer dans le
   dépôt, ni dans un fichier suivi par Git.

## 3. Contrôles à blanc (aucune dépense)

```powershell
cd C:\Users\kder_\mon-projet-agent\address-validator
.\.venv\Scripts\python.exe scripts\test_x402_payment.py                 # plan : compare /manifest et le corps 402
.\.venv\Scripts\python.exe scripts\test_x402_payment.py --check-domain  # domaine EIP-712 lu on-chain
```

`--check-domain` doit afficher `DOMAIN OK` (`name()="USD Coin"`, `version()="2"`,
`decimals()=6`). Un domaine faux ferait rejeter toute signature honnête : ne pas
passer à l'étape 4 sans ce OK.

## 4. Lancer le paiement réel (dépense 0,02 USDC)

```powershell
cd C:\Users\kder_\mon-projet-agent\address-validator
$env:X402_TEST_PRIVATE_KEY = "0x..."   # clé du portefeuille acheteur, jamais commitée
.\.venv\Scripts\python.exe scripts\test_x402_payment.py --pay
Remove-Item Env:\X402_TEST_PRIVATE_KEY # effacer la variable après le test
```

Déroulé attendu, dans l'ordre :

1. `POST /validate` sans en-tête → **402**, `accepts[0]` récupéré ;
2. signature EIP-712 locale (clé en mémoire, aucune écriture disque) ;
3. second `POST /validate` avec `X-PAYMENT` (base64 du `PaymentPayload`) ;
4. le service vérifie puis **règle** via le facilitateur CDP ;
5. réponse **`HTTP 200`**, puis `Reglement : {"success": true, "transaction": "0x…"}`,
   puis `RESULT : {...}` et la ligne finale **`PAYMENT OK service rendu apres encaissement`** ;
6. code de retour **0**.

## 5. Vérifier l'encaissement côté acheteur et côté service

- **Portefeuille** : le solde USDC Base a diminué de 0,02 (à la 6e décimale) ;
  l'historique contient un `Transfer` vers `0x0fe9afb42021cbcbed9da7473f619026fe42d339`.
- **Chaîne** : ouvrir `https://basescan.org/tx/<transaction>` (hash imprimé à l'étape 5)
  et vérifier : statut *Success*, jeton **USDC**, montant **0,02**, destinataire le
  `payTo` de l'offre.
- **Service** : `curl.exe -s https://kder-ai.com/metrics/revenue` → `validations_paid`
  et `revenue_usdc` incrémentés ; le reçu `X-PAYMENT-RESPONSE` archivé dans le
  rapport de recette.
- **Idempotence** : relancer la même commande produit un **nouveau** nonce et un
  second encaissement (0,02 de plus) — c'est voulu, mais c'est pourquoi on ne
  relance pas « pour voir ».

## 6. Échecs attendus et diagnostic

| Message | Cause | Action |
|---|---|---|
| `402` qui se répète, `preuve de paiement invalide` | domaine EIP-712 ou en-tête `X-PAYMENT` altéré | relancer `--check-domain`, comparer avec `/manifest` |
| `facilitateur ... 401` | JWT CDP refusé (clé absente ou expirée) | vérifier `CDP_API_KEY_ID`/`CDP_API_KEY_SECRET` dans le `.env` du VPS |
| `insufficient` / solde insuffisant | portefeuille sans USDC sur **Base** (souvent des USDC sur mainnet) | recharger sur Base |
| `nonce déjà utilisé` | `authorization` rejouée | relancer la commande (nonce neuf) |
| `pas de X-PAYMENT-RESPONSE` | règlement non abouti : le service ne rend rien | lire la réponse brute ; l'acheteur n'est pas débité |

## 7. Règles de sécurité

- Portefeuille dédié, dédié à cette recette, contenant au plus quelques centimes.
- La clé privée vit dans une variable d'environnement, jamais dans un fichier suivi
  par Git (`.env`, `*.env` sont ignorés — `.gitignore` ligne 15-16).
- Après la recette : supprimer la variable, archiver le hash de transaction, et
  vider le portefeuille si la clé a pu être exposée.
- Aucune donnée d'adresse validée n'est journalisée (PRIVACY.md §3) ; le paiement
  n'y change rien.
