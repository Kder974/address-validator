# Address Validator API

API HTTP **payante à la requête** (protocole `x402`, prix par défaut
`0.02 USDC`) qui prend une adresse de livraison brute et renvoie une adresse
standardisée, ses coordonnées, un score de livrabilité et une clé de cache.

## 1. Présentation

- **Entrée unique payante** : `POST /validate` — refuse en `402 Payment Required`
  tant qu'aucune preuve de paiement valide n'est fournie.
- **Sortie** : adresse standardisée (`line1`, `line2`, `city`, `state`, `zip`,
  `country`), `latitude`/`longitude` (géocodage Nominatim), `deliverability`,
  `confidence`, `degraded` et `cache_key`.
- **Découverte** : `GET /manifest` décrit le service et son schéma pour un
  annuaire x402 / MCP.
- **Résilience** : si le fournisseur est injoignable, l'API répond quand même
  `200` avec `degraded: true` — jamais une erreur qui laisserait croire à une
  adresse invalide certaine.

Critères de succès de la spec, et où ils sont vérifiés automatiquement :

| # | Critère | Vérification |
|---|---------|--------------|
| 1 | Un appel sans preuve reçoit `402` (prix et protocole annoncés) | `tests/test_payment.py` (8 tests), `tests/test_routes_extra.py::test_metrics_count_unpaid_requests`, `scripts/e2e_check.py` |
| 2 | Un appel avec preuve valide reçoit `200` et le résultat | `tests/test_cache.py` (appels payants réels), `tests/test_validation.py::test_valid_address_with_mocked_provider`, `scripts/e2e_check.py` |
| 3 | Le cache évite de rappeler le fournisseur sur une adresse répétée | `tests/test_cache.py::test_second_identical_request_hits_cache` et `::test_distinct_requests_call_provider_twice`, `scripts/e2e_check.py` |
| 4 | Une panne fournisseur produit une réponse dégradée rapide et explicite | `tests/test_validation.py::test_degraded_flag_set_when_provider_unreachable`, `tests/test_circuit_breaker.py`, `tests/test_latency_budget.py` |

Ce que ce service **ne fait pas** : aucun rate limiting **applicatif** (D21) —
la limite est tenue au niveau *edge* par Nginx (`nginx-hardening.conf` : 10
requêtes/s par IP, rafale 20, `429` au-delà) — et la vérification de paiement
on-chain est **disponible mais désactivée par défaut** (D15 pour le mode par
défaut, D27 pour la couche optionnelle). Les deux sont documentés
(`README.md` §7 et §9, `SECURITY.md` §3 et §6) plutôt que passés sous silence.

## 2. Démarrage rapide

Prérequis : **Python 3.12** (le venv du dépôt tourne en 3.12.10, comme l'image
`python:3.12-slim` du `Dockerfile`). PostgreSQL et Redis sont **facultatifs** :
sans eux, l'API démarre et se dégrade proprement (`/health` le dit).

```powershell
# 1. Environnement virtuel
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt

# 2. Configuration (jamais de secret dans le dépôt)
Copy-Item .env.example .env        # renseigner SMARTY_AUTH_ID / API_SECRET_KEY / clés CDP

# 3. Lancer l'API
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

```powershell
# Vérifications
.\.venv\Scripts\python.exe -m pytest -q                  # suite complète
.\.venv\Scripts\python.exe scripts\run_tests.py          # oracle de tests (rapport JUnit)
.\.venv\Scripts\python.exe scripts\doc_check.py README.md 10   # sections de ce fichier
.\.venv\Scripts\python.exe scripts\e2e_check.py http://127.0.0.1:8000   # recette bout-en-bout
```

Démarrage conteneurisé : `docker compose up -d` (voir `docker-compose.yml` et
`Dockerfile`).

Déploiement en production (VPS Debian 12, Nginx + HTTPS, paiement x402) :
**`DEPLOIEMENT_VPS.md`** — compte rendu réel, écarts assumés par rapport au plan
et points de vigilance d'exploitation.

## 3. Configuration

Toutes les valeurs viennent de l'environnement ou de `.env` (pydantic-settings,
`app/config.py`). Une variable inconnue est ignorée (décision D8).

| Champ | Variable | Défaut | Rôle |
|-------|----------|--------|------|
| `api_host` / `api_port` | `API_HOST` / `API_PORT` | `127.0.0.1` / `8000` | écoute uvicorn (`127.0.0.1` : le service est publié par Nginx, pas directement) |
| `api_secret_key` | `API_SECRET_KEY` | `change_me` | **clé HMAC de la preuve de paiement** — à changer impérativement |
| `database_url` | `DATABASE_URL` | `postgresql://user:password@localhost:5432/address_validator` | PostgreSQL (journal des paiements) |
| `redis_url` | `REDIS_URL` | `redis://localhost:6379/0` | cache des résultats |
| `smarty_auth_id` / `smarty_auth_token` | `SMARTY_AUTH_ID` / `SMARTY_AUTH_TOKEN` | vide | identifiants du fournisseur de validation |
| `cdp_api_key_id` / `cdp_api_key_secret` | `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET` | vide | SDK Coinbase CDP (facultatif) |
| `cdp_wallet_address` | `CDP_WALLET_ADDRESS` | vide | portefeuille annoncé dans le `402` |
| `x402_price` | `X402_PRICE` | `0.02` | prix par validation, en USDC |
| `x402_onchain_verify` | `X402_ONCHAIN_VERIFY` | `false` | confirmation on-chain du transfert USDC (D27) — voir §7 |
| `base_rpc_url` | `BASE_RPC_URL` | `https://mainnet.base.org` | nœud JSON-RPC Base (chaîne 8453) utilisé quand D27 est actif |
| `x402_usdc_contract` | `X402_USDC_CONTRACT` | `0x833589…4bda02913` | contrat USDC natif de Base |
| `x402_min_confirmations` | `X402_MIN_CONFIRMATIONS` | `1` | confirmations minimales exigées avant d'accepter le paiement |
| `x402_network` / `x402_chain_id` | `X402_NETWORK` / `X402_CHAIN_ID` | `base` / `8453` | reseau et chaine annonces dans `accepts[]` |
| `x402_domain_name` / `x402_domain_version` | `X402_DOMAIN_NAME` / `X402_DOMAIN_VERSION` | `USD Coin` / `2` | domaine EIP-712 du jeton - **verifie on-chain** par `scripts/test_x402_payment.py --check-domain` |
| `x402_facilitator_url` | `X402_FACILITATOR_URL` | `https://api.cdp.coinbase.com/platform/v2/x402` | facilitateur (`/verify` puis `/settle`) |
| `x402_facilitator_verify` | `X402_FACILITATOR_VERIFY` | `true` | appel de `/verify` avant le reglement |
| `x402_require_settlement` | `X402_REQUIRE_SETTLEMENT` | `true` | **ne rendre le service qu'apres encaissement** (`false` = jamais en production) |
| `public_base_url` / `mcp_public_url` | `PUBLIC_BASE_URL` / `MCP_PUBLIC_URL` | `https://kder-ai.com` / `.../mcp` | URLs publiees dans `accepts[].resource`, la carte MCP et les annuaires |
| `discovery_log_*` | `DISCOVERY_LOG_ENABLED`, `DISCOVERY_LOG_PATH`, `DISCOVERY_LOG_RAW_IP`, `DISCOVERY_LOG_SALT`, `DISCOVERY_LOG_RETENTION_DAYS` | journal actif, chemin vide (journald), IP pseudonymisee, 30 j | journal des refus `402` et retention appliquee (`PRIVACY.md` 3) |
| `dashboard_user` / `dashboard_password` | `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | `kder` / vide | `/dashboard` - vide = endpoint **ferme** (`503`) |
| `test_mode` / `test_mode_cutoff_utc` | `TEST_MODE` / `TEST_MODE_CUTOFF_UTC` | `false` / `2026-10-11T12:00:00+00:00` | une transaction marquée `test_mode` est un essai de recette : exclue de `real_*`, comptée dans `test_*` et `total_*` (D40) |
| `environment` / `log_level` | `ENVIRONMENT` / `LOG_LEVEL` | `development` / `INFO` | contexte et verbosité |

## 4. Endpoints et contrat de données

| Méthode | Chemin | Rôle | Source |
|---------|--------|------|--------|
| `GET` | `/` | identité du service et liens | `app/main.py:57-65` |
| `GET` | `/manifest` | description x402 (prix, devise, schémas) | `app/routes/manifest.py` |
| `GET` | `/health` | état réel de PostgreSQL et Redis, borné à 2 s | `app/routes/health.py` |
| `GET` | `/metrics` | compteurs, état du disjoncteur, état du cache | `app/routes/metrics.py` |
| `GET` | `/metrics/revenue` | revenu agrégé 24 h / 7 j / 30 j — `real_*` hors recette, `total_*` registre entier (D40) — et disponibilité du registre | `app/routes/revenue.py` |
| `GET` | `/dashboard` | tableau de bord d'exploitation — **privé** (Basic Auth ; `503` si `DASHBOARD_PASSWORD` est absent) | `app/routes/dashboard.py` |
| `POST` | `/validate` | **payant** — validation complète | `app/routes/validate.py` |
| `GET` | `/docs` | documentation interactive (FastAPI) | auto |
| `GET` | `/llms.txt`, `/AGENTS.md`, `/.well-known/security.txt` | fichiers de découverte pour agents, servis en statique par Nginx (voir `DISCOVERY.md`) | `nginx-discovery.conf` |
| `POST` | `/mcp` | serveur MCP (JSON-RPC 2.0) : decouverte gratuite, `tools/call` **payant** | `app/routes/mcp.py` |
| `GET` | `/.well-known/mcp-server` | carte de decouverte MCP (servie par l'application) | `app/routes/mcp.py` |

Chaque requête refusée en `402` est journalisée sous forme de métadonnées
(horodatage, endpoint, `User-Agent`, IP **pseudonymisée**) : jamais l'adresse
validée. Réglages et rétention : `PRIVACY.md` §3, `DISCOVERY.md` §4.

### 4.1 Revenu réel contre revenu du registre (D40)

`/metrics/revenue` publie trois lectures du même registre, jamais mélangées :

| Champ | Contenu |
|-------|---------|
| `real_validations_paid`, `real_revenue_usdc` | ce qu'un **client** a payé — le chiffre publiable |
| `total_validations_paid`, `total_revenue_usdc` | tout le registre, essais de recette compris (audit) |
| `test_validations_paid`, `test_revenue_usdc` | ce qui est exclu du réel, chiffré : `real_*` + `test_*` = `total_*` |

Les alias `validations_paid` et `revenue_usdc` (D31) pointent sur le **réel**.
Une transaction est marquée `test_mode` de deux façons, et de deux seulement :
`TEST_MODE=true` au moment où le service l'enregistre (recette en cours), ou
`scripts/migrate_test_mode.py` pour les lignes **déjà écrites** — seuil
`TEST_MODE_CUTOFF_UTC` (défaut `2026-10-11T12:00:00+00:00`, les deux essais de
recette de ce jour-là). Rien n'est déduit du montant ni de l'adresse : une ligne
non marquée reste du revenu réel. `/dashboard` affiche le réel en avant et la
recette dans sa propre carte.

Requête (`app/schemas.py:11-16`) : `{"address": {"street", "city", "state",
"zip", "country"}}` — `country` vaut `US` par défaut, les autres champs sont
obligatoires (un champ manquant donne `422`).

Réponse (`app/schemas.py:19-37`) : `{"result": {"validated", "standardized",
"latitude", "longitude", "deliverability", "confidence", "degraded",
"cache_key"}, "price_paid", "transaction_id"}`.

Pipeline de validation (`app/validation/__init__.py:26-41`) :
`parse → standardize → verify (fournisseur) → geocode → score`.

| Sortie | Signification |
|--------|---------------|
| `degraded: true` | le fournisseur n'a pas pu être interrogé (panne, timeout, disjoncteur ouvert) : `deliverability` vaut alors `invalid` mais n'est **pas** une preuve d'adresse inexistante |
| `deliverability` / `confidence` | issus du code DPV : `Y` → residential 0,99 ; `S` → residential 0,95 ; `D` → commercial 0,99 ; `X` → unknown 0,50 ; `N` → invalid 0,00 ; `+0,05` si les coordonnées ont été obtenues (`app/validation/scorer.py:17-36`) |
| `cache_key` | SHA-256 de l'adresse brute canonisée : identifie l'entrée de cache et permet l'effacement (voir `PRIVACY.md` §5) |

## 5. Exemple d'appel payant

1. Construire une preuve (le client et l'API partagent `API_SECRET_KEY`) :

```powershell
.\.venv\Scripts\python.exe -c "from app.payment import build_payment_proof; print(build_payment_proof('0x-demo-tx'))"
# -> 0x-demo-tx:1770000000.123:<hmac_sha256_hex>
```

2. Appeler l'API :

```powershell
curl.exe -X POST http://127.0.0.1:8000/validate `
  -H "Content-Type: application/json" `
  -H "X-Payment-Proof: 0x-demo-tx:1770000000.123:<hmac>" `
  -H "X-Transaction-Id: 0x-demo-tx" `
  -d "{\"address\":{\"street\":\"1600 Amphitheatre Parkway\",\"city\":\"Mountain View\",\"state\":\"CA\",\"zip\":\"94043\",\"country\":\"US\"}}"
```

3. Sans preuve, ou avec une preuve périmée / malformée / falsifiée, la réponse
   est `402` (`app/payment.py:77-85`) :

```json
{"error": "Payment Required", "price": 0.02, "currency": "USDC",
 "wallet": "", "protocol": "x402", "version": "1.0"}
```

4. Avec une preuve valide, la réponse est `200`. Exemple **mesuré** en recette
   le 10/10/2026 sur ce poste (fournisseur injoignable, donc `degraded`) :

```json
{"result": {"validated": false, "standardized": {"line1": "1600 Amphitheatre Parkway", "...": "..."},
            "deliverability": "invalid", "confidence": 0.05, "degraded": true,
            "cache_key": "8f1c…"},
 "price_paid": 0.02, "transaction_id": "0x-demo-tx"}
```

`transaction_id` reflète simplement l'en-tête `X-Transaction-Id` reçu
(`app/routes/validate.py:67`).

Même appel **en production** le 11/10/2026 (`https://kder-ai.com`, fournisseur
réel actif, preuve HMAC valide) :

```json
{"result": {"validated": true, "deliverability": "residential", "confidence": 1.0,
            "degraded": false, "cache_key": "250a6087…"},
 "price_paid": 0.02, "transaction_id": null}
```

`deliverability` est dérivé du code DPV renvoyé par le fournisseur (`Y` →
`residential`, `scorer.py:17-23`) et `confidence` intègre le bonus de géocodage
(0,99 + 0,05, borné à 1,0). Le `metadata.rdi` du fournisseur (`Commercial` sur
cette adresse) reste dans la charge brute, non exposée par l'API.

## 6. Cache, résilience et budget de latence

| Mécanisme | Comportement | Décision |
|-----------|--------------|----------|
| Cache Redis | clé `addr:<sha256>`, TTL 30 jours, consulté **avant** tout appel réseau | D5 |
| Cooldown cache | après un échec du backend, le cache est réputé indisponible 30 s : une panne Redis coûte une fois, pas à chaque requête. `/health` garde une sonde réelle | D25 |
| Timeouts Redis | connexion 1 s, lecture 1 s ; compteur d'erreurs exposé | D12 |
| Disjoncteur fournisseur | `closed → open → half-open → closed`, seuil applicatif **3** échecs, récupération 60 s ; une fois ouvert, la réponse est locale et immédiate | D23 |
| Timeout fournisseur | granulaire : connexion 2 s, lecture 4 s (au lieu d'un 10 s global) | D23 |
| Timeout géocodeur | connexion 2 s, lecture 3 s ; l'échec laisse `latitude`/`longitude` à `null` sans faire échouer la requête | D26 |
| Sondes `/health` | exécutées hors boucle d'événements, en parallèle, bornées à 2 s | D22 |
| Observabilité | état du disjoncteur et disponibilité du cache exposés dans `/metrics` | D24 |

Latences mesurées sur ce poste le **10/10/2026** (PostgreSQL et Redis éteints,
réseau sortant actif, identifiants fournisseur vides donc réponse `401`) :

| Mesure | Valeur | Méthode |
|--------|--------|---------|
| `GET /health` | **2,13 s** | requête HTTP réelle (borne D22 : 2,0 s atteinte, les deux sondes sont en panne) |
| `POST /validate` payant, 1ᵉʳ appel | **11,117 s** | `scripts/e2e_check.py` |
| `POST /validate` payant, 2ᵉ appel | **11,036 s** | `scripts/e2e_check.py` |
| Lecture Redis, backend absent | **2,03 s** | mesure in-process (payée une seule fois grâce à D25) |
| Appel fournisseur (401 rejoué 3×) | **6,77 s** | mesure in-process |
| Géocodage Nominatim | **0,86 s** | mesure in-process |
| Pipeline complet (`validate_full`) | **7,62 s** | mesure in-process |

Ces chiffres sont honnêtes et non flatteurs : la latence dégradée est dominée par
le fournisseur, dont les erreurs **4xx sont rejouées** par le back-off (voir
`TEST_REPORT.md`, « Limites connues »). Les valeurs de référence enregistrées
dans le code (`15,2 s` avant D23, `6,03 s` avant D22) n'ont **pas** été
re-mesurées aujourd'hui : elles sont citées comme historique dans
`TEST_REPORT.md`.

## 7. Paiement x402 : ce qui est vérifié, réglé, et ce qui reste hérité

Le service parle **x402 v1** (spécification `specs/transports-v1/http.md` du dépôt
`coinbase/x402`) et règle réellement les paiements en USDC sur Base. Le corps du
`402` est un document `PaymentRequired` :

```json
{ "x402Version": 1, "error": "...", "accepts": [ { "scheme": "exact",
  "network": "base", "maxAmountRequired": "20000", "asset": "0x833589…2913",
  "payTo": "0x0fe9…d339", "resource": "https://kder-ai.com/validate",
  "mimeType": "application/json", "maxTimeoutSeconds": 300,
  "extra": { "name": "USD Coin", "version": "2" } } ] }
```

Le client renvoie sa signature dans `X-PAYMENT` (base64 d'un `PaymentPayload`
`{signature, authorization:{from,to,value,validAfter,validBefore,nonce}}`) et
reçoit `X-PAYMENT-RESPONSE` (base64 de `{success, transaction, network, payer}`).

**Deux contrôles, dans cet ordre** (`app/x402.py`) :

1. **local, hors ligne** — le digest EIP-712 (`TransferWithAuthorization`,
   EIP-3009) est recalculé explicitement (`eth_abi`, `eth_hash`, `eth_keys`), la
   signature récupérée est comparée à `authorization.from`, et destination,
   montant, jeton, réseau et fenêtre de validité sont confrontés à l'offre
   publiée. Rien ne sort du serveur avant que ce contrôle passe ;
2. **règlement** — `POST /verify` puis `POST /settle` au facilitateur (défaut :
   celui de Coinbase, authentifié par un JWT CDP signé avec `CDP_API_KEY_ID` /
   `CDP_API_KEY_SECRET`). Le hash du règlement alimente le registre durable
   (D29) ; tout échec (refus explicite, panne, hash absent, nonce déjà réglé)
   vaut un refus.

Le domaine EIP-712 annoncé (`extra.name` / `extra.version`) est celui du contrat :
`scripts/test_x402_payment.py --check-domain` le vérifie on-chain — mesuré le
11/10/2026 sur Base : `name() = "USD Coin"`, `version() = "2"`, `decimals() = 6`.

**Ce qui reste hérité (D15).** L'en-tête `X-Payment-Proof`
(`tx_hash:timestamp:hmac_sha256`) reste accepté pour l'outillage de l'exploitant
et les clients déjà intégrés : fenêtre symétrique de 300 s, HMAC comparé en temps
constant, et — si `X402_ONCHAIN_VERIFY=true` — confirmation du reçu sur Base par
lecture JSON-RPC (`app/onchain.py`, sans paquet `web3` ni paquet de magasin de
clés). Un paiement HMAC n'est jamais présenté comme un règlement x402 : il ne
porte pas `X-PAYMENT-RESPONSE`.

**MCP.** Le même outil est exposé en MCP sur `POST /mcp` (JSON-RPC 2.0, sans état,
sans flux SSE) : `tools/list` est gratuit, `tools/call` est payé par le même
en-tête `X-PAYMENT` et rend `structuredContent` + `content`. La carte
`GET /.well-known/mcp-server` est servie par l'application, donc elle ne peut pas
décrire un endpoint éteint. Publication annuaire : `server.json`,
`.mcp.json`, `smithery.yaml` (voir `DISCOVERY.md` §1).

**Recette réelle.** `scripts/test_x402_payment.py` a trois modes : plan (vérifie
que `/manifest`, le corps `402` et la carte MCP publient la **même** offre),
`--check-domain` (compare le domaine annoncé au contrat on-chain) et `--pay`
(paiement réel ; exige un portefeuille acheteur financé en USDC sur Base et
dépense 0,02 USDC).

## 8. Tests et vérification

| Outil | Ce qu'il prouve | Commande |
|-------|-----------------|----------|
| `pytest` | comportement unitaire et d'intégration | `.\.venv\Scripts\python.exe -m pytest -q` |
| `scripts/run_tests.py` | **oracle de tests** : rapport JUnit XML + code de sortie, sans dépendre de la ligne de résumé de pytest (qui disparaît en sortie redirigée) | `.\.venv\Scripts\python.exe scripts\run_tests.py` |
| `scripts/doc_check.py` | la documentation contient **exactement** le nombre attendu de sections `## N.`, numérotées sans trou | `.\.venv\Scripts\python.exe scripts\doc_check.py README.md 10` |
| `scripts/e2e_check.py` | recette HTTP réelle, 22 vérifications couvrant les 4 critères de succès | `.\.venv\Scripts\python.exe scripts\e2e_check.py http://127.0.0.1:8000` |
| `GATES.md` | ledger de portes : oracles exécutables (`CHECK:` / `EXPECT:`) et revues manuelles | voir « Comment jouer le ledger » ci-dessous |
| `.cline/skills/unlazy/scripts/gate-lint.mjs` | les oracles peuvent réellement échouer (qualité du ledger) | `node .cline\skills\unlazy\scripts\gate-lint.mjs GATES.md` |

Résultats mesurés le 10/10/2026 sur ce poste : `46 tests collectés — 45 passés,
1 ignoré` (le test ignoré est un test d'intégration qui exige de vrais
identifiants SmartyStreets et le réseau) ; `scripts/e2e_check.py` →
`22/22 vérifications OK`. Détail complet, méthode et limites dans
`TEST_REPORT.md`.

Comment jouer le ledger, depuis la racine du projet :

```powershell
node .cline\skills\unlazy\scripts\gate-lint.mjs address-validator\GATES.md
node .cline\skills\unlazy\scripts\gate-check.mjs --status  address-validator\GATES.md   # lit, n'exécute rien
node .cline\skills\unlazy\scripts\gate-check.mjs --approve address-validator\GATES.md   # approuve après lecture
node .cline\skills\unlazy\scripts\gate-check.mjs           address-validator\GATES.md   # exécute les oracles approuvés
```

## 9. Déploiement

`deploy.sh` cible un VPS Debian/Ubuntu et enchaîne : installation des paquets
(`python3.12`, PostgreSQL, Redis, Nginx, Certbot), clone dans `/opt`,
environnement virtuel, `pip install -r requirements.txt`, création de la base et
de l'utilisateur PostgreSQL, activation de Redis, copie de `nginx.conf`, obtention
du certificat (`certbot --nginx`), puis installation et démarrage du service
systemd. Le script est en `set -euo pipefail` (D20) : il s'arrête à la première
erreur.

**Point d'attention : `deploy.sh` ne crée aucun fichier `.env`.** Sans étape
manuelle, le service démarre avec les valeurs par défaut — donc
`API_SECRET_KEY=change_me`, le mot de passe PostgreSQL `change_me`, et aucun
identifiant SmartyStreets (le fournisseur répond alors `401` et **toutes** les
réponses sont dégradées). À faire immédiatement après le déploiement :

```powershell
# sur le serveur, dans /opt/address-validator
cp .env.example .env        # puis renseigner API_SECRET_KEY, SMARTY_*, DATABASE_URL, REDIS_URL
systemctl restart address-validator
systemctl status address-validator
curl -s http://127.0.0.1:8000/health
journalctl -u address-validator -n 50
```

Variante conteneurisée : `docker compose up -d` (`Dockerfile` basé sur
`python:3.12-slim`, `docker-compose.yml` pour l'API et ses dépendances).

**Déploiement du VPS de production (`kder-ai.com`)** : `scripts/deploy-kder-ai.sh`
— le script réellement exécuté sur le serveur, **versionné ici** (avant le
11/10/2026 il ne vivait que sur le poste de travail et dans `/opt`, donc hors du
contrôle de version). Il reprend les 11 étapes ci-dessus et ajoute :

- le refus de déployer si `API_SECRET_KEY` vaut encore `change_me` ou fait moins
  de 32 caractères, ou si le `.env` est en CRLF ;
- l'installation de `nginx-hardening.conf` dans `/etc/nginx/conf.d/` : HSTS
  (1 an, `includeSubDomains`), `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `limit_req` 10 req/s par IP (rafale 20) et
  corps de requête borné à 16 ko. C'est volontairement **hors du vhost**, que
  `certbot --nginx` réécrit à chaque renouvellement ;
- des contrôles qui échouent (ou signalent) si le durcissement a disparu :
  écoute de `127.0.0.1:8000` uniquement (10.6), en-têtes de sécurité (10.7),
  `429` sur 60 appels simultanés (10.8) ;
- la compilation de Python 3.12 en repli quand le dépôt Debian n'en fournit pas.

Le compte rendu du déploiement réel (versions, journaux, écarts assumés,
correctifs du 11/10/2026) est dans `DEPLOIEMENT_VPS.md`.

Après mise en production : restreindre `/metrics` et `/docs`, activer un mot de
passe Redis, et vérifier que Nginx a bien installé la redirection HTTPS
(`SECURITY.md` §4 et §6).

## 10. Limites connues et suite

| Limite | Impact | Où c'est documenté |
|--------|--------|--------------------|
| Paiement vérifié par secret partagé (mode par défaut) ; confirmation on-chain disponible mais désactivée | un `tx_hash` inventé mais correctement signé est accepté tant que `X402_ONCHAIN_VERIFY` est à `false` | `§7`, `SECURITY.md` §3, D15, D27 |
| Preuve rejouable pendant 300 s | une même preuve peut servir plusieurs fois dans la fenêtre | `§7`, `SECURITY.md` §6 |
| `address_validations` jamais alimentée | aucune trace d'adresse en base : la conservation repose sur le cache Redis (30 j) | `PRIVACY.md` §3 |
| `record_transaction` en meilleur effort | un échec d'écriture laisse la réponse `200` sans trace de paiement | `SECURITY.md` §6 |
| Pas de rate limiting applicatif | volume et abus traités au niveau *edge* : Nginx limite à 10 requêtes/s par IP (rafale 20, `429` au-delà) ; rien dans le code | `§9`, `SECURITY.md` §6, D21 |
| Métriques en mémoire | perdues au redémarrage, pas d'export Prometheus ni d'alerte | `SECURITY.md` §5 |
| Erreurs 4xx du fournisseur rejouées | latence dégradée mesurée à 11,1 s ; le back-off porte sur des erreurs non rejouables | `TEST_REPORT.md`, « Limites connues » |
| `CORS *`, `/metrics` et `/docs` publics, secret par défaut `change_me` encore accepté par le code | restent à durcir ; le déploiement, lui, refuse `change_me` et tout secret de moins de 32 caractères (`scripts/deploy-kder-ai.sh`) | `§9`, `SECURITY.md` §2 et §6 |
| `deploy.sh` ne crée pas `.env` | démarrage avec des secrets par défaut si l'étape manuelle est oubliée | `§9` |

Suite recommandée, par ordre de valeur :

1. Ne retenter que les erreurs **réseau et 5xx** du fournisseur : supprime le
   back-off sur `401` et ramène la latence dégradée sous ~3 s.
2. Consommer les preuves de paiement (anti-rejeu) et refuser le démarrage si
   `API_SECRET_KEY` est resté à sa valeur par défaut.
3. Passer au paiement réellement vérifié : activer `X402_ONCHAIN_VERIFY` (D27)
   **une fois le client capable de payer** (authorization EIP-3009 ou facilitateur
   x402, `§7`).
4. Brancher les métriques sur une supervision réelle et générer le `.env` au
   déploiement.




