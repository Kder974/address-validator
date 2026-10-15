# Sécurité — address-validator

Périmètre : le **code de ce dépôt**, tel que vérifié le 10/10/2026. Ne sont pas
couverts : la sécurité de l'hôte, du réseau, du fournisseur SmartyStreets, de
Nominatim, du portefeuille CDP et de l'hébergeur.

## 1. Surface d'attaque

| Élément | Exposition | Contrôle actuel |
|---------|------------|-----------------|
| `POST /validate` | public, payant | preuve `X-Payment-Proof`, *fail-closed* (`app/payment.py:111-147`) |
| `POST /validate` (abonnement) | public, payant par clé | `X-API-Key` comparée par **empreinte SHA-256**, refus `401`/`403`/`402`, panne `503`, clé jamais journalisée (§3) |
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

**Couche on-chain (D27) — active par défaut depuis le durcissement W1
(12/10/2026)** : la signature HMAC ne suffit plus. Le `tx_hash` doit désigner,
sur Base (chaîne 8453), une transaction confirmée (`status == 0x1`) dont le reçu
contient un événement ERC-20 `Transfer` émis par le contrat USDC vers
`CDP_WALLET_ADDRESS`, pour au moins `X402_PRICE`, avec `X402_MIN_CONFIRMATIONS`
blocs. La lecture est un **JSON-RPC en lecture seule** (`eth_getTransactionReceipt`,
`eth_blockNumber`) borné à 2 s connect / 4 s read, sans paquet web3 et sans
paquet de magasin de clés : le reçu est décodé à la main (`app/onchain.py`) et un
`tx_hash` malformé est refusé **avant** tout appel réseau. Tout échec vaut refus.

**Note W1 — pourquoi le défaut a changé (12/10/2026).** Jusqu'à cette date,
`X402_ONCHAIN_VERIFY` valait `false` par défaut : un déploiement qui omettait la
variable acceptait une preuve HMAC **synthétique** — signature correcte,
transaction inexistante — et servait un `200` sans aucun encaissement vérifiable.
`AUDIT_TRAFIC_VPS.md` §5 consigne la mesure des `200` de `/validate` en production
et la correction : le défaut est passé à `true` (`app/config.py:48-60`). La
conséquence est explicite plutôt que subie — hors recette, la voie héritée
n'obtient un `200` que si la chaîne confirme le transfert, et une couche on-chain
coupée hors recette refuse désormais **toutes** les preuves à secret partagé
(`app/payment.py`). Le seul `200` sans confirmation encore possible est celui
d'une **recette déclarée** (`TEST_MODE=true`) : il est journalisé en `WARNING`,
sa ligne de registre est marquée `test_mode` (D40) et sort de
`real_revenue_usdc` — un service rendu sans paiement ne peut pas devenir du
chiffre d'affaires. `TEST_MODE=true` sur un service de production est donc un
état à surveiller : `/metrics/revenue` publie `test_mode.recording_now` pour le
rendre visible.

**Anti-rejeu, limite résiduelle.** La voie héritée ne consomme pas la preuve : la
même preuve reste acceptée pendant sa fenêtre de 300 s, et le registre la
déduplique (`ON CONFLICT DO NOTHING`), donc elle n'est pas facturée deux fois —
mais elle rend le service deux fois. La voie x402, elle, refuse un nonce déjà
réglé et s'appuie sur la chaîne comme source de vérité. Voir `README.md` §7.

**Voie abonnement (`X-API-Key`, D45).**

| Contrôle | Implémentation |
|----------|----------------|
| En-tête | `X-API-Key: sk_live_` + 32 caractères tirés de `secrets` (`app/accounts.py`) |
| Recherche | par **empreinte SHA-256** (`api_key_hash`) : la clé présentée n'est jamais stockée ni comparée en clair, et un refus ne renseigne pas sur l'existence d'un secret (même règle que les connexions, D44) |
| Stockage | la base ne garde que l'empreinte et un préfixe affichable (`sk_live_ab12`) ; la clé est montrée **une fois**, sur une page `Cache-Control: no-store`, et une clé perdue se régénère (`/compte/cle`, mot de passe exigé) |
| Journalisation | un refus journalise le **préfixe** seulement (`cle API refusee (cle_inconnue) prefixe=sk_live_Qq7Z`) ; la clé n'entre ni dans un journal, ni dans un corps de réponse, ni dans un message d'erreur |
| Réponses | `401` clé inconnue, `403` essai terminé, `402` quota du **mois** consommé ; le corps ne rend que `mode`, `reason`, `prefix`, l'état du plan (`plan`, `quota`, `validations_this_month`, `trial_ends_at`) et la sortie `upgrade` |
| Priorité | quand `X-API-Key` et `X-PAYMENT` sont présentés ensemble, l'abonnement tranche : le paiement n'est pas examiné, donc un client abonné ne peut pas être encaissé en USDC par erreur |
| Débit | le compteur du compte (`customer_validations`) : **aucune** ligne au registre des paiements (D29), donc le revenu publié (D40) ne compte jamais un abonnement comme un encaissement |
| Panne des comptes | `503` explicite, **jamais** `402` : une clé qui n'a pas pu être *vérifiée* n'est pas une clé *refusée*, et demander un paiement sur une panne pousserait un client en règle à payer deux fois (`api_key_unavailable`) |
| Transport | la clé voyage en clair dans l'en-tête jusqu'à Nginx, qui termine le TLS (§4) : en `http://` hors boucle locale, elle est lisible sur le réseau — un client ne doit l'envoyer qu'en `https://` |
| Périmètre | la clé API ouvre `/validate` et **pas** `/compte` (Basic Auth email + mot de passe, D44) ; il n'existe encore ni expiration de plan payant ni révocation autre que la régénération |

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
- `/metrics/agents` expose l'observabilité des appelants (D67) : requêtes par
  classe, top agents, top pages, conversion et la **liste blanche publiée** des
  agents IA. Il ne publie **aucune adresse en clair** : l'IP est pseudonymisée à
  l'entrée (SHA-256 + sel, D30) et c'est le pseudonyme qui sort. La publication
  est volontaire (un agent doit pouvoir vérifier s'il est reconnu) ; la liste des
  agents d'entraînement n'est pas un secret.
- Le journal JSONL de D30 (`DISCOVERY_LOG_PATH`) n'écrit que les classes
  `agent_ia` et `annuaire_mcp` : les visites humaines sont comptées, jamais
  journalisées. Aucune adresse validée, aucun corps de requête, aucune clé n'y
  figure ; `DISCOVERY_LOG_RAW_IP=true` met l'IP en clair et doit rester une
  mesure d'enquête datée (PRIVACY.md §3).
- Les deux analyseurs (`scripts/analyser_agents.py`, `scripts/analyser_visites.py`)
  pseudonymisent par défaut ; `--ip-en-clair` est le seul moyen de lire une
  adresse, et il est explicite. La rétention annoncée (30 jours) est appliquée
  par `scripts/prune_discovery_log.py`.
- `/health` interroge réellement PostgreSQL et Redis, en parallèle et bornés à
  2 s (`app/routes/health.py`, D22) : il dit la vérité même quand tout est en
  panne.
- **Ces trois points d'entrée sont publics** : ils divulguent l'état interne du
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
| Rate limiting par IP | **deux régimes, séparés volontairement.** La protection anti-abus générique reste **hors de l'application** (D21) et assurée par l'edge : `nginx-hardening.conf` (10 req/s, rafale 20 → `429`). Les **plafonds produit** vivent dans l'application (`app/ratelimit.py`, D44) — 5 inscriptions par IP et par heure, 10 essais gratuits par IP et par 24 h — parce qu'ils portent un **message** au client (`429` + `Retry-After`, « créez un compte pour continuer ») et non seulement un refus ; Redis les porte, un compteur local **compté** prend le relais s'il tombe | `fail2ban` pour les IP qui insistent, `limit_conn` si des clients ouvrent de nombreuses connexions |
| En-têtes de sécurité | HSTS / `nosniff` / `Referrer-Policy` posés par `nginx-hardening.conf`, donc conservés lors des renouvellements Certbot | ajouter une CSP seulement si une interface web est servie un jour |
| CORS | `allow_origins=["*"]` + `allow_credentials=True` (`app/main.py:43-49`) | restreindre aux origines nécessaires |
| `/metrics`, `/docs` | publics | restreindre par IP ou *basic auth* |
| Rejeu de preuve | possible pendant 300 s | consommer les `tx_hash` (`SETNX` Redis avec TTL 300 s) |
| Preuve de paiement | secret partagé (D15) : qui connaît `API_SECRET_KEY` peut forger une preuve bien signée — mais hors recette elle ne vaut plus rien, la chaîne devant confirmer le transfert (D27, W1). Reste le rejeu dans la fenêtre de 300 s (ligne ci-dessus) | vérifier une authorization EIP-3009 via le facilitateur x402 (`X-PAYMENT`) : la voie recommandée pour un client (D34-D36, `README.md` §7) |
| 4xx rejoués | `@retry` rejoue aussi les 401/403 (~6,8 s mesurés par appel) | ne retenter que les erreurs réseau et 5xx (`TEST_REPORT.md`, « Limites connues ») |
| Trace de paiement | `record_transaction` en meilleur effort : un échec d'écriture n'empêche pas la réponse 200 | rendre l'échec visible (métrique/alerte) |
| Redis | sans mot de passe ni TLS | mot de passe + réseau privé, TLS si Redis sort de l'hôte |
| Dépendances | versions épinglées (`requirements.txt`) | `pip check` et veille CVE réguliers ; les dépendances de test restent hors image (D18) |
| WAF / IPS | aucun | à envisager si l'API est réellement exposée au public |

