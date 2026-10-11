# Sécurité — address-validator

Périmètre : le **code de ce dépôt**, tel que vérifié le 10/10/2026. Ne sont pas
couverts : la sécurité de l'hôte, du réseau, du fournisseur SmartyStreets, de
Nominatim, du portefeuille CDP et de l'hébergeur.

## 1. Surface d'attaque

| Élément | Exposition | Contrôle actuel |
|---------|------------|-----------------|
| `POST /validate` | public, payant | preuve `X-Payment-Proof`, *fail-closed* (`app/payment.py:111-147`) |
| `GET /health` | public | aucune donnée sensible ; sondes bornées (D22) |
| `GET /metrics` | public | compteurs d'exploitation + état interne (disjoncteur, cache) — **à restreindre** |
| `GET /manifest`, `GET /docs` | public | description du service, documentation auto-générée |
| Secrets | `.env` local, variables systemd | ignoré par git (`.gitignore`) |
| PostgreSQL / Redis | réseau local | non publiés par `nginx.conf` |
| Trafic sortant | SmartyStreets, Nominatim, Base (JSON-RPC, lecture seule, D27), (CDP) | HTTPS via `httpx`, vérification de certificat par défaut |

## 2. Secrets et configuration

- `api_secret_key` a pour valeur par défaut **`change_me`** (`app/config.py:20`)
  et sert de clé HMAC à la preuve de paiement. Rien dans le code ne refuse de
  démarrer avec cette valeur : **en production, toute personne connaissant le
  défaut peut forger des preuves valides et consommer le service gratuitement.**
  C'est le durcissement n°1 (voir §6).
- `deploy.sh:23` crée l'utilisateur PostgreSQL avec le mot de passe `change_me` :
  même remarque, à changer avant le premier déploiement.
- Les vrais secrets (clés CDP, identifiants Smarty, `DATABASE_URL`, `REDIS_URL`,
  `API_SECRET_KEY`) vivent dans `.env` ou dans l'environnement du service
  systemd, jamais dans le dépôt.
- `extra = "ignore"` (D8) tolère des variables inconnues : c'est une tolérance
  de démarrage, **pas** une protection.
- Rotation : changer `api_secret_key` invalide immédiatement toutes les preuves
  (fenêtre de validité 300 s au maximum).
- **État en production au 11/10/2026** : le `API_SECRET_KEY` du VPS a été
  **tourné** — 64 caractères hexadécimaux générés *sur le serveur*
  (`secrets.token_hex(32)`, équivalent de `openssl rand -hex 32`), donc jamais
  transmis par un poste de travail. Le placeholder `change_me` n'existe plus que
  comme défaut du code (`app/config.py`) et dans `.env.example`. La rotation rend
  caduque toute preuve émise avant elle (300 s de fenêtre au maximum) : aucune
  ne circulait, le seul client étant la recette.
- Le code ne journalise aucun secret. Seule exception à surveiller :
  `record_transaction` journalise l'exception SQLAlchemy en cas d'échec
  (`app/payment.py:169`), qui peut contenir les paramètres de la requête —
  soit la clé de cache (pseudonyme), jamais un secret ni une adresse lisible.

## 3. Authentification du paiement et anti-rejeu

Depuis le Lot B, le service authentifie le paiement par le **protocole x402 v1**
(`specs/transports-v1/http.md` du dépôt `coinbase/x402`) : le client signe une
autorisation EIP-712 et le facilitateur l'encaisse avant que le service soit
rendu (`app/x402.py`). La preuve à secret partagé reste servie comme voie de
compatibilité.

**Voie x402 (par défaut pour un agent).**

| Contrôle | Implémentation |
|----------|----------------|
| Enveloppe | `X-PAYMENT` : base64 d'un `PaymentPayload` `{x402Version, scheme, network, payload}` ; enveloppe v2 (`accepted`, CAIP-2) acceptée et normalisée |
| Signature | EIP-712 `TransferWithAuthorization` (EIP-3009) : digest recalculé explicitement (`eth_abi` + `eth_hash.auto.keccak`), signataire récupéré par `eth_keys` et comparé à `authorization.from` |
| Domaine | `name`/`version` annoncés dans `accepts[].extra`, `chainId` et contrat USDC issus de la configuration ; divergence = refus (`scripts/test_x402_payment.py --check-domain` le prouve on-chain) |
| Destination | `authorization.to` doit être `CDP_WALLET_ADDRESS` (comparaison insensible à la casse, adresse validée avant tout appel) |
| Montant | `authorization.value` ≥ `accepts[].maxAmountRequired` (unités atomiques, 6 décimales) |
| Fenêtre | `validAfter` ≤ maintenant < `validBefore` : ni prématurée, ni périmée |
| Ordre | tout ceci est vérifié **hors ligne** : le facilitateur n'est appelé que si la signature est déjà bonne (mesuré : aucun appel réseau sur un paiement falsifié) |
| Règlement | `POST /verify` puis `POST /settle` au facilitateur (défaut Coinbase CDP), JWT signé avec `CDP_API_KEY_*` (`cdp.auth.generate_jwt`, ES256 ou EdDSA) |
| Service | rendu **seulement** après un règlement réussi ; tout échec (refus, panne, hash absent, nonce déjà réglé) vaut `402` |
| Traçabilité | `X-PAYMENT-RESPONSE` (base64 de `{success, transaction, network, payer}`) sur les appels payés ; le hash du règlement alimente le registre durable (D29) |
| Anti-rejeu | nonce mémorisé après règlement réussi (cache borné en mémoire) ; la garantie dure est le nonce EIP-3009 consommé par la chaîne au premier règlement |

**Voie héritée à secret partagé (D15, outillage de l'exploitant).**

```
X-Payment-Proof: <tx_hash>:<timestamp>:<hmac_sha256_hex(tx_hash:timestamp)>
```

| Contrôle | Implémentation |
|----------|----------------|
| Signature | HMAC-SHA256 sur `tx_hash:timestamp`, clé = `api_secret_key` (`app/payment.py`) |
| Comparaison | `hmac.compare_digest` — temps constant, pas de fuite par timing |
| Fraîcheur | fenêtre **symétrique** ±300 s : rejette le périmé *et* le futur |
| Malformation | `len(parts) != 3`, `tx_hash` ou `signature` vide → refus |
| Comportement par défaut | `False` — toute exception renvoie `False` (*fail-closed*) |
| `X-PAYMENT-RESPONSE` | **jamais** émis : un paiement HMAC n'est pas un règlement x402 |

**Couche on-chain optionnelle (D27)** : quand `X402_ONCHAIN_VERIFY=true`, la
signature HMAC ne suffit plus. Le `tx_hash` doit désigner, sur Base (chaîne
8453), une transaction confirmée (`status == 0x1`) dont le reçu contient un
événement ERC-20 `Transfer` émis par le contrat USDC vers `CDP_WALLET_ADDRESS`,
pour au moins `X402_PRICE`, avec `X402_MIN_CONFIRMATIONS` blocs. La lecture est
un **JSON-RPC en lecture seule** (`eth_getTransactionReceipt`,
`eth_blockNumber`) borné à 2 s connect / 4 s read, sans paquet web3 et sans
paquet de magasin de clés : le reçu est décodé à la main (`app/onchain.py`) et un
`tx_hash` malformé est refusé **avant** tout appel réseau. Tout échec vaut refus.

**Anti-rejeu, limite résiduelle.** La voie héritée ne consomme pas la preuve : la
même preuve reste acceptée pendant sa fenêtre de 300 s, et le registre la
déduplique (`ON CONFLICT DO NOTHING`), donc elle n'est pas facturée deux fois —
mais elle rend le service deux fois. La voie x402, elle, refuse un nonce déjà
réglé et s'appuie sur la chaîne comme source de vérité. Voir `README.md` §7.

## 4. Transport, chiffrement et réseau

- L'application **ne termine pas le TLS** : uvicorn écoute en HTTP sur
  `127.0.0.1:8000` — défaut de `app/config.py:18`, aligné sur la ligne
  `ExecStart` de `address-validator.service`. Aucun en-tête de sécurité n'est
  émis par l'application elle-même.
- Le TLS est porté par Nginx + Certbot (`deploy.sh:32-34`). Le `nginx.conf`
  versionné écoute **seulement en 80** et relaie vers `127.0.0.1:8000` avec
  `X-Real-IP` / `X-Forwarded-For` / `X-Forwarded-Proto` : ni redirection 80→443
  ni HSTS n'y figurent, car `certbot --nginx` réécrit le vhost à chaque
  renouvellement.
- **Durcissement versionné** : `nginx-hardening.conf`, installé dans
  `/etc/nginx/conf.d/` par l'étape 7 de `scripts/deploy-kder-ai.sh`, pose
  `Strict-Transport-Security: max-age=31536000; includeSubDomains`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `limit_req` (10 req/s par IP, rafale 20 → `429`) et
  `client_max_body_size 16k`. Mesuré en production le 11/10/2026 (run de
  référence `deploy-kder-ai.run6.log`, contrôle 10.8) : 60 appels simultanés sur
  `/manifest` → **27 × `200` + 33 × `429`** ; lors d'un envoi manuel, **42 ×
  `200` + 18 × `429`**. Le partage exact varie selon la synchronisation des
  appels (28 × `200` + 32 × `429` au 4ᵉ essai) : jamais 60 × `200`, la rafale
  au-delà de 20 est bien coupée.
- `redis-server` est installé **sans mot de passe** (`deploy.sh:26-27`) : Redis
  doit rester lié à la boucle locale et n'être joignable que par l'application.
- Trafic sortant : HTTPS vers SmartyStreets et Nominatim, avec vérification de
  certificat par défaut d'`httpx` (`app/validation/verifier.py:109`,
  `app/validation/geocoder.py:38`).

## 5. Journalisation, supervision et réponse à incident

- Journaux : `logging.basicConfig` au niveau `LOG_LEVEL` (`app/main.py:20`) ;
  avertissements sur les échecs non bloquants (CDP, écriture de transaction,
  initialisation de la base). Pas de journal de requêtes applicatif.
- `/metrics` expose l'état d'exploitation : requêtes totales / payées / refusées,
  succès et échecs de cache, réponses dégradées, erreurs fournisseur, uptime,
  **état du disjoncteur** (`state`, `failures`, `threshold`) et **disponibilité
  du backend cache** (D24, `app/routes/metrics.py:60-80`).
- `/health` interroge réellement PostgreSQL et Redis, en parallèle et bornés à
  2 s (`app/routes/health.py`, D22) : il dit la vérité même quand tout est en
  panne.
- **Ces deux points d'entrée sont publics** : ils divulguent l'état interne du
  service. À restreindre côté Nginx (§6).
- Aucun mécanisme d'alerte automatique n'existe dans le dépôt : les métriques
  nécessaires sont exposées, le branchement (Prometheus, Discord, n8n) reste à
  faire.

Procédure en cas d'incident :

1. Rotation de `API_SECRET_KEY` (effet immédiat) et révocation des clés CDP.
2. Purge du cache : `redis-cli --scan --pattern 'addr:*' | xargs redis-cli del`
   (les valeurs contiennent des adresses).
3. Revue des lignes `payment_transactions` sur la période concernée.
4. Redémarrage du service (`systemctl restart address-validator`).

## 6. Durcissement recommandé et points non couverts

| Sujet | État actuel | Recommandation |
|-------|-------------|----------------|
| Secret par défaut | `change_me` **refusé au déploiement** (`scripts/deploy-kder-ai.sh` : arrêt si `change_me` ou moins de 32 caractères) mais toujours accepté par le code | refuser aussi au démarrage de l'application (`app/config.py`) : `DEPLOIEMENT_VPS.md` §2 décrit l'état réel du VPS (secret de 64 caractères, tourné le 11/10/2026) |
| Rate limiting par IP | **absent de l'application** (D21), **assuré au niveau edge** : `nginx-hardening.conf` (10 req/s, rafale 20 → `429`) | `fail2ban` pour les IP qui insistent, `limit_conn` si des clients ouvrent de nombreuses connexions |
| En-têtes de sécurité | HSTS / `nosniff` / `Referrer-Policy` posés par `nginx-hardening.conf`, donc conservés lors des renouvellements Certbot | ajouter une CSP seulement si une interface web est servie un jour |
| CORS | `allow_origins=["*"]` + `allow_credentials=True` (`app/main.py:43-49`) | restreindre aux origines nécessaires |
| `/metrics`, `/docs` | publics | restreindre par IP ou *basic auth* |
| Rejeu de preuve | possible pendant 300 s | consommer les `tx_hash` (`SETNX` Redis avec TTL 300 s) |
| Preuve de paiement | secret partagé par défaut (D15) ; couche on-chain disponible mais **désactivée** (D27) | activer `X402_ONCHAIN_VERIFY` (confirmation du transfert USDC sur Base) et/ou vérifier une authorization EIP-3009 via un facilitateur x402 (`README.md` §7) |
| 4xx rejoués | `@retry` rejoue aussi les 401/403 (~6,8 s mesurés par appel) | ne retenter que les erreurs réseau et 5xx (`TEST_REPORT.md`, « Limites connues ») |
| Trace de paiement | `record_transaction` en meilleur effort : un échec d'écriture n'empêche pas la réponse 200 | rendre l'échec visible (métrique/alerte) |
| Redis | sans mot de passe ni TLS | mot de passe + réseau privé, TLS si Redis sort de l'hôte |
| Dépendances | versions épinglées (`requirements.txt`) | `pip check` et veille CVE réguliers ; les dépendances de test restent hors image (D18) |
| WAF / IPS | aucun | à envisager si l'API est réellement exposée au public |

