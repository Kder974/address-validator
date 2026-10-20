# Address Validator API

API HTTP **payante à la requête** (protocole `x402`, prix par défaut
`0.02 USDC`) qui prend une adresse de livraison brute et renvoie une adresse
standardisée, ses coordonnées, un score de livrabilité et une clé de cache.

## 1. Présentation

- **Entrée unique payante** : `POST /validate` — refuse en `402 Payment Required`
  tant qu'aucune preuve de paiement valide n'est fournie.
- **Sortie** : adresse standardisée (`line1`, `line2`, `city`, `state`, `zip`,
  `country`), corrigée par le fournisseur quand l'adresse est reconnue (D84 :
  orthographe, ville, code postal ajouté), `latitude`/`longitude` (géocodage Nominatim), `deliverability`,
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
requêtes/s par IP, rafale 20, `429` au-delà). Le paiement hérité (preuve HMAC à
secret partagé, D15) n'est **plus accepté sur sa seule signature** : depuis le
durcissement W1 (12/10/2026), la confirmation on-chain (D27) est **active par
défaut** et exige, sur Base, un transfert USDC confirmé vers le portefeuille
d'encaissement. Une preuve HMAC synthétique n'obtient un `200` que dans une
**recette déclarée** (`TEST_MODE=true`), et la ligne écrite est alors marquée
`test_mode`, donc exclue du revenu réel (D40). Tout est documenté (`README.md`
§7 et §9, `SECURITY.md` §3 et §6, `AUDIT_TRAFIC_VPS.md` §5) plutôt que passé
sous silence.

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
| `x402_onchain_verify` | `X402_ONCHAIN_VERIFY` | `true` | confirmation on-chain du transfert USDC (D27, active par défaut depuis W1) — `false` n'est admis qu'avec `TEST_MODE=true` ; hors recette, toute preuve à secret partagé est alors refusée — voir §7 |
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
| `dashboard_user` / `dashboard_password` | `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | `kder` / vide | `/dashboard` - vide = endpoint **ferme** (`503`) ; `LOGIN_MAX_FAILURES` mots de passe faux par IP bloquent `LOGIN_LOCK_MINUTES` min (D67) |
| `geoip_db_path` / `audience_retention_days` | `GEOIP_DB_PATH` / `AUDIENCE_RETENTION_DAYS` | `/var/lib/address-validator/dbip-country.mmdb` / `400` | suivi d'audience (D67) : base des pays DB-IP Lite posée par `scripts/maj_geoip.sh` (absente = pays inconnu) ; conservation des lignes `page_visits` et `api_calls` |
| `pilotage_comptes` | `PILOTAGE_COMPTES` | vide | comptes du tableau de bord (D74), « Nom=email » séparés par des virgules ; jamais dans le code (dépôt public) |
| `cost_server_eur` / `cost_smarty_eur` / `cost_smarty_per_call_usd` / `cost_mail_eur` | `COST_SERVER_EUR` / `COST_SMARTY_EUR` / `COST_SMARTY_PER_CALL_USD` / `COST_MAIL_EUR` | `0` | coûts lus par le tableau de bord (D68) : tous à `0` = résultat du mois non calculé |
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
| `GET` | `/dashboard` | tableau de bord d'exploitation — **privé** (Basic Auth ; `503` si `DASHBOARD_PASSWORD` est absent ; 5 mots de passe faux par IP bloquent 15 min, D67) ; depuis D67, chaque visite d'une page publique et chaque appel d'API laissent une ligne de suivi (pays, origine, durée, statut ; ni IP ni cookie) ; depuis D69, dessin v2 (barre latérale, grande carte du résultat, carte du monde en points, Site et SEO avec sources, robots et pages, parcours visite → payant) ; depuis D68, écrans **argent** (encaissé réel, abonnements à facturer, coûts, résultat, prévision, revenu par pays et par canal), **API** (statuts, temps de réponse, cache, canaux, pays) et **clients** (essais qui finissent, quotas, jamais appelé), alertes, périodes `?p=jour|7j|30j|mois`, rafraîchie chaque minute | `app/routes/dashboard.py`, `app/audience.py`, `app/exploitation.py` |
| `GET` | `/dashboard/argent`, `/dashboard/api`, `/dashboard/clients`, `/dashboard/site`, `/dashboard/registre` | pages du pilotage, une par section (D71), mêmes accès que `/dashboard`, période gardée par `?p=` | `app/routes/dashboard.py`, `app/pilotage_page.py` |
| `GET` `POST` | `/dashboard/mot-de-passe`, `/dashboard/nouveau-mot-de-passe` | comptes du tableau de bord (D74) : une adresse de `PILOTAGE_COMPTES` reçoit par mail un lien (30 min, une fois, empreinte seule en base) pour choisir son mot de passe (empreinte bcrypt) ; même réponse pour une adresse inconnue ; 3 liens par compte et par heure ; connexion ensuite par email | `app/pilotage_comptes.py`, `app/routes/dashboard.py` |
| `GET` `POST` | `/dashboard/connexion`, `/dashboard/deconnexion` | page de connexion du tableau de bord (D69), à la place de la fenêtre du défi Basic : cookie `__Host-av_pilotage` signé (HMAC, 12 h, HttpOnly, Secure, SameSite=Strict), mêmes 5 essais puis 15 min de pause, formulaire d'un autre site refusé ; un navigateur sans session y est renvoyé, un script garde le défi Basic | `app/routes/dashboard.py`, `app/pilotage_page.py` |
| `GET` | `/prospection` | CRM de prospection (liste, filtres, marquage, export) — **privé**, même garde que `/dashboard` (`503` si le mot de passe est absent ou si `PROSPECTION_DB` n'existe pas) | `app/routes/prospection.py`, `prospection/README.md` |
| `GET` | `/pricing` | page publique de l'offre commerciale (D43) — **publique**, indexable, cachée 5 min ; aucune ressource externe, feuille de style partagée avec `/dashboard` (`app/web.py`) | `app/routes/pricing.py` |
| `GET` `POST` | `/signup` | inscription self-service (D44) — **publique** ; mot de passe bcrypt, clé API `sk_live_` affichée **une fois**, 5 inscriptions par IP et par heure | `app/routes/signup.py`, `app/accounts.py` |
| `GET` `POST` | `/validate-form` | essai gratuit public (D46) — **publique**, indexable, 3 essais par IP et par 24 h (D94) (`429` + `Retry-After` exact au-delà) ; aucune écriture, ne charge rien, résultat dégradé annoncé comme tel ; accepte aussi une adresse sur une ligne (champ `address`, D86) | `app/routes/validate_form.py`, `app/ratelimit.py` |
| `POST` | `/stripe/webhook` | paiement Stripe (D91) : un `checkout.session.completed` **signé** (`Stripe-Signature`, HMAC-SHA256, 5 min) passe le compte de `client_reference_id` à l'offre payée (2 900 → Starter, 7 900 → Pro) ; `customer.subscription.deleted` le ramène à l'essai échu ; sans `STRIPE_WEBHOOK_SECRET`, `503` | `app/routes/stripe_webhook.py`, `app/accounts.py` |
| `GET` | `/robots.txt`, `/sitemap.xml`, `/favicon.ico` | référencement (D65) — site public ouvert aux moteurs de recherche et d'IA, plan des pages publiques × 6 langues avec leurs `hreflang`, icône ; les pages publiques portent un bloc `<!-- seo -->` (canonique, Open Graph, JSON-LD) ; `www.` renvoie en `301` vers `PUBLIC_BASE_URL` | `app/seo.py` |
| `GET` | `/819a36fb2a9140f4f142b6e80ae19cc3.txt` | clé IndexNow (D81), publique par conception ; `scripts/indexnow.py --envoyer` annonce les pages du plan du site à Bing, Yandex, Seznam et Naver | `app/seo.py` |
| `GET` | `/guide` | guide d'intégration public (D48) — **publique**, indexable, cachable 5 min, sans secret : contrat JSON, codes de retour, cache, quotas, exemples appel direct / CSV / Shopify / WooCommerce | `app/routes/guide.py` |
| `GET` | `/agents`, `/compare` | pages de contenu (D66) — **publiques**, indexables, six langues : validation d'adresses pour agents IA (MCP, x402, sans compte) et comparatif des prix publiés de Google, Smarty, PostGrid et Geoapify (relevés le 14/10/2026, sources citées, « quand choisir lequel ») ; nos chiffres viennent du code et de la configuration | `app/routes/contenu.py` |
| `GET` `POST` | `/compte`, `/compte/cle` | espace client : tableau de bord du mois (D64 : chiffres du mois, prochaine étape déduite de l'état du compte, courbe jour par jour, conseils), clé API, offre et facturation — **privé** (session ouverte par `/compte/connexion`, ou Basic Auth envoyé par un script ; sans session, `303` vers la connexion ; régénérer la clé redemande le mot de passe ; `503` si la base ne répond pas) | `app/routes/signup.py`, `app/accounts.py` |
| `GET` `POST` | `/compte/connexion`, `/compte/deconnexion` | connexion à l'espace client (D62) — page privée (`no-store`, `noindex`) ; cookie `__Host-av_session` (jeton aléatoire, empreinte seule en base, 12 h au plus) ; 5 mots de passe faux bloquent 15 min par IP et par compte ; formulaire d'un autre site refusé | `app/routes/connexion.py` |
| `GET` `POST` | `/compte/mot-de-passe-oublie`, `/compte/nouveau-mot-de-passe` | mot de passe oublié (D63) — lien par mail fabriqué avec `PUBLIC_BASE_URL`, empreinte seule en base, 30 min, une seule fois ; même réponse que le compte existe ou non ; 3 mails par compte et 5 demandes par IP et par heure ; le nouveau mot de passe ferme toutes les sessions ; envoi par `SMTP_HOST` (vide = aucun mail) | `app/routes/mot_de_passe.py` |
| `POST` | `/validate` | **payant** — validation complète ; **deux façons de payer** : abonnement (`X-API-Key`, D45) ou encaissement à l'appel (x402 / preuve HMAC) | `app/routes/validate.py` |
| `GET` | `/docs` | documentation interactive (FastAPI) | auto |
| `GET` | `/llms.txt`, `/AGENTS.md`, `/.well-known/security.txt` | fichiers de découverte pour agents, servis en statique par Nginx (voir `DISCOVERY.md`) | `nginx-discovery.conf` |
| `POST` | `/mcp` | serveur MCP (JSON-RPC 2.0) : decouverte gratuite, `tools/call` **payant** (transport x402 v2 : paiement dans `params._meta["x402/payment"]`, offre et reçu dans le résultat d'outil ; en-tête `X-PAYMENT` v1 accepté) | `app/routes/mcp.py` |
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

Sur un registre déjà écrit, le rattrapage se fait sans redéploiement :
`venv/bin/python scripts/migrate_test_mode.py` (simulation, ligne par ligne) puis
`--apply`. Le rejeu n'écrit plus rien (idempotent) ; le résultat se contrôle avec
`curl -s http://127.0.0.1:8000/metrics/revenue`.

Requête (`app/schemas.py:11-16`) : `{"address": {"street", "city", "state",
"zip", "country"}}` — `country` vaut `US` par défaut, les autres champs sont
obligatoires (un champ manquant donne `422`).

Réponse (`app/schemas.py:19-37`) : `{"result": {"validated", "standardized",
"latitude", "longitude", "deliverability", "confidence", "degraded",
"cache_key"}, "price_paid", "transaction_id"}`.

Pipeline de validation (`app/validation/__init__.py:26-41`) :
`parse → standardize → verify (fournisseur) → geocode → score`. Depuis D84, une
adresse reconnue repasse par `parse → standardize` sous la forme corrigée par le
fournisseur (`corrige`) avant le géocodage.

| Sortie | Signification |
|--------|---------------|
| `degraded: true` | le fournisseur n'a pas pu être interrogé (panne, timeout, disjoncteur ouvert) : `deliverability` vaut alors `invalid` mais n'est **pas** une preuve d'adresse inexistante |
| `deliverability` / `confidence` | issus du code DPV : `Y` → residential 0,99 ; `S` → residential 0,95 ; `D` → commercial 0,99 ; `X` → unknown 0,50 ; `N` → invalid 0,00 ; `+0,05` si les coordonnées ont été obtenues (`app/validation/scorer.py:17-36`) |
| `cache_key` | SHA-256 de l'adresse brute canonisée : identifie l'entrée de cache et permet l'effacement (voir `PRIVACY.md` §5) |

### 4.2 Deux façons de payer la même validation (D45)

`POST /validate` sert deux clients : la **boutique abonnée**, qui présente sa clé API,
et l'**agent autonome**, qui paie à l'appel (§5). La clé a la priorité : si les deux
sont envoyés, l'abonnement tranche et l'appel n'est pas encaissé en USDC.

| Mode | En-tête d'entrée | Ce qui est débité | `price_paid` |
|------|------------------|-------------------|--------------|
| Abonnement | `X-API-Key: sk_live_…` | le compteur du compte (`customer_validations`) | `0.0` |
| x402 / HMAC | `X-PAYMENT` ou `X-Payment-Proof` | le registre des paiements (D29) | `0.02` (USDC) |

La réponse porte `X-Payment-Mode` (`api_key`, `x402` ou `hmac`) : sans lui, un client
ne peut pas distinguer un appel couvert par son abonnement d'un appel encaissé. En
mode clé, deux en-têtes disent aussi l'état du compte — `X-Plan` et
`X-Quota-Remaining` (absent pour un plan illimité, l'absence dit « illimité »).

| Situation | Code | Pourquoi ce code-là |
|-----------|------|---------------------|
| Clé valide, quota du mois disponible | `200` | servi, compte débité, registre USDC **intact** |
| Clé inconnue (ou vide) | `401` | c'est la configuration du client qu'il faut corriger |
| Essai gratuit terminé (plan `trial`) | `403` | le compte existe, il n'ouvre plus le service |
| Quota du **mois** consommé | `402` | plafond d'usage, pas défaut d'authentification |
| Base des comptes injoignable | `503` | une clé non *vérifiée* n'est pas une clé *refusée* — jamais un `402` |

Le quota est **mensuel**, comme publié sur `/pricing` : la fenêtre est le mois
calendaire **UTC** (`accounts.month_start()`), le compteur à vie reste affiché dans
`/compte` mais ne ferme aucune porte. Les corps de refus sont identifiables
(`{"mode": "api_key", "reason": "cle_inconnue" | "compte_expire" | "quota_atteint"}`),
ne contiennent que le **préfixe** de la clé (`sk_live_ab12`) et indiquent la sortie
(`upgrade` → `/pricing`).

```powershell
$cle = "sk_live_..."   # affichée une seule fois à l'inscription, régénérable sur /compte
$corps = '{"address":{"street":"1 Rue de Paris","city":"Saint-Denis","state":"RE","zip":"97400","country":"RE"}}'
$r = Invoke-WebRequest -Uri http://127.0.0.1:8000/validate -Method POST `
     -Headers @{ "X-API-Key" = $cle } -ContentType "application/json" -Body $corps
$r.Headers["X-Payment-Mode"], $r.Headers["X-Plan"], $r.Headers["X-Quota-Remaining"]
$r.Content | ConvertFrom-Json | Select-Object price_paid, transaction_id
# -> api_key  trial  99 ; price_paid = 0.0, transaction_id = $null
```

Un même appel sans en-tête `X-API-Key` reste facturé en x402 : le mode clé n'a **rien**
retiré aux agents autonomes. Compteurs publiés par `/metrics` : `api_key_requests`,
`api_key_validations`, `api_key_denied`, `api_key_unavailable`.

### 4.3 Essai gratuit public (D46)

`GET /validate-form` sert une page publique — indexable, cachable 5 minutes, et
**autonome** : aucun script, aucune ressource externe, la feuille de style
partagée (`app/web.py`). C'est la première page qu'un visiteur voit ; la CSP
(`form-action 'self'`) rend vérifiable par le navigateur ce que le code promet.

| Point | Comportement |
|-------|--------------|
| Plafond | `VALIDATE_FORM_RATE_LIMIT_PER_DAY` (**10** par défaut) essais par IP et par 24 h ; fenêtre **fixe**, qui démarre au premier essai et ne se prolonge pas à chaque refus |
| Refus | `429` + `Retry-After` **exact** (secondes restantes de la fenêtre), avec la limite rappelée et un lien vers `/signup` |
| Écritures | **aucune** : ni `customers`, ni `customer_validations`, ni `payment_transactions`. Seuls les compteurs `validate_form_requests` / `validate_form_validations` / `validate_form_denied` avancent |
| Pipeline | le **même** que `/validate` (`perform_validation`) : même verdict, même cache |
| Cache et indexation | le formulaire vide est `public, max-age=300` et indexable ; toute réponse `POST` (résultat, erreur, refus) est `no-store` + `noindex`, parce qu'elle contient une adresse |
| Résultat dégradé | affiché comme tel : « le fournisseur n'a pas répondu, cette réponse ne dit rien de l'adresse » — jamais présenté comme une adresse invalide |
| Saisie | échappée et réaffichée pour correction ; un pays sans deux lettres est refusé (`400`) avant tout appel fournisseur |

Compteurs du plafond visibles dans `/metrics` : un refus de plafond compté est la
seule façon de distinguer « personne n'essaie » de « tout le monde est bloqué ».

### 4.4 Guide d'intégration public (D48)

`GET /guide` répond à la question que se pose un client qui vient de recevoir sa
clé : authentification (`X-API-Key` ou x402), contrat JSON exact, les **huit**
codes de retour avec l'action à mener, ce qui est mis en cache et ce qui est
plafonné, puis quatre cas pratiques (appel direct PowerShell, import CSV en
Python, Shopify, WooCommerce).

| Point | Comportement |
|-------|--------------|
| Faits publiés | base publique, prix, plafond de l'essai gratuit, en-têtes `X-Payment-Mode` / `X-Plan` / `X-Quota-Remaining` et plans : **lus dans le code et la configuration**, jamais recopiés (la porte refuse un gabarit non remplacé) |
| Secrets | aucun : la page est indexable, donc publique par construction. La clé d'exemple est un gabarit (`sk_live_VOTRE_CLE`) |
| Autonomie | aucun script, aucune ressource externe, feuille de style partagée, `Cache-Control: public, max-age=300`, `form-action 'none'` |
| Navigation | une seule définition (`app/web.py: site_head()`) : `/guide` est atteignable depuis toutes les pages publiques, et `/pricing` ne recopie plus sa propre copie du `<nav>` |
| Limites dites | pas d'application Shopify ni d'extension WooCommerce fournie (l'appel vient du service du client) ; un résultat `degraded` est « à re-vérifier », jamais une adresse invalide |

### 4.5 Email de bienvenue d'une boutique (D49)

Pas une page, un **message** : celui qu'on envoie à la boutique qui vient de créer
son compte. Il vit dans `EMAIL_BIENVENUE.md`, sous deux formes — le gabarit à
charger dans n8n (variables `{{…}}`, expliquées dans une table) et un exemplaire
**prêt à envoyer** pour le premier client (Tiitoune Art).

| Point | Comportement |
|-------|--------------|
| Chiffres | jours d'essai, quota offert et prix mensuels **lus dans le code** (`accounts.JOURS_ESSAI`, `VALIDATIONS_ESSAI`) et sur `/pricing` — la porte refuse un email qui ne les reprendrait plus |
| Voie annoncée | l'**abonnement** (`X-API-Key`) : ni `X-PAYMENT`, ni `X-Payment-Proof`, ni `tx_hash` — la porte les interdit dans ce message (le paiement à l'appel est la voie des agents, décrite dans `/guide`) |
| Secrets | aucune clé, jamais : le texte renvoie à `/signup` et `/compte`. La porte refuse une clé `sk_live_` complète ou une valeur par défaut du dépôt |
| Limites dites | aucun prélèvement automatique, `degraded` qui « ne dit rien » de l'adresse, empreinte (pas adresse) conservée, effacement par `cache_key` |
| Envoi | l'exemplaire ne contient plus aucune variable de fusion (vérifié), et chaque variable du gabarit est expliquée (vérifié) |

### 4.6 Suivi des clients (D50)

Un tableau de suivi ne vaut que par son accord avec ce qui est réellement servi.
Ce que la table des comptes contient déjà répond à la seule question qui décide du
revenu : **quels comptes existent, lesquels se perdent en silence, et que faut-il
leur dire ?** Les règles vivent dans `app/suivi.py` (états, seuils, ordre
d'urgence), l'exécution dans `scripts/suivi_clients.py`.

```powershell
# rapport lisible, sur la base de la configuration (DATABASE_URL)
.\.venv\Scripts\python.exe scripts\suivi_clients.py

# export pour un tableur, seuils resserrés
.\.venv\Scripts\python.exe scripts\suivi_clients.py --format csv --sortie suivi.csv --essai-bientot-jours 5

# expliquer après coup un classement : rejouer le rapport à une date donnée
.\.venv\Scripts\python.exe scripts\suivi_clients.py --maintenant 2026-10-01T09:00:00+00:00
```

| Point | Comportement |
|-------|--------------|
| État publié | celui de la route payante : `accounts.plan_status` / `consumption` / `refusal_reason`, et les codes HTTP lus dans `REFUS_HTTP` — aucun seuil recopié, donc aucun écart possible |
| Ordre | ordre d'**urgence** : refusé maintenant, puis essai qui se termine ou quota bientôt atteint, puis intégration jamais aboutie ; à priorité égale, l'échéance la plus proche |
| Secrets | le **préfixe** de clé (de quoi reconnaître un compte au téléphone), jamais la clé complète ni son empreinte ; aucune adresse validée n'est lue |
| Lecture seule | uniquement des `SELECT` : un rapport ne peut pas modifier la base de production |
| Export | `--format csv` écrit UTF-8, séparateur « ; » (Excel français), fins de ligne `\n` — le script le rappelle au moment d'écrire |
| Codes de retour | `0` rapport produit (une base vide reste une mesure), `2` rien n'a pu être **mesuré** — un rapport vide n'est pas un « tout va bien » |

Vérification : `.\.venv\Scripts\python.exe scripts\decisions_check.py D50` — la
porte installe une base réelle et compare l'état publié au refus que la route
applique, y compris le cas du client payant dont la date d'essai est passée.

### 4.7 Page d'accueil (D53)

`GET /` sert la vitrine à un navigateur et garde le JSON d'avant pour un programme
(`Vary: Accept` sur les deux). Le dessin vit dans `app/templates/accueil/fr.html`
(maquette d'origine : `docs/maquettes/page-accueil/`) ; ses chiffres (essai, quotas,
prix, prix x402, contact) sont injectés par `app/routes/accueil.py` depuis le code et
la configuration.

```powershell
curl.exe -s https://kder-ai.com/                      # JSON, comme avant
curl.exe -s -H "Accept: text/html" https://kder-ai.com/   # la vitrine
```

Vérification : `.\.venv\Scripts\python.exe scripts\decisions_check.py D53`.

### 4.8 Six langues (D57)

Le français reste à la racine ; les cinq autres langues ont leur préfixe. Toutes les
pages publiques existent dans les six : la vitrine, le guide (D58), l'essai gratuit (D59),
les tarifs (D60), l'inscription et l'espace client (D61), chacune à son adresse française
précédée du préfixe de la langue (`/en/guide`, `/ar/signup`…).

| Langue | Accueil | Monnaie affichée (facturé en euros) |
|---|---|---|
| français | `/` | 29 € / 79 € |
| anglais | `/en/` | $33 / $90 |
| chinois | `/zh/` | ¥223 / ¥608 |
| hindi | `/hi/` | ₹3,160 / ₹8,620 |
| espagnol | `/es/` | 29 € / 79 € |
| arabe (droite à gauche) | `/ar/` | $33 / $90 |

`app/i18n.py` fabrique les adresses (`chemin(lang, page)`), les nombres et les prix de
chaque langue (`prix`, `mention_euros`, taux de septembre 2026) et rend les gabarits
(`rendre`). Les montants en euros viennent d'`accounts.OFFRES` (aussi lisibles sous
`pricing.OFFRES`) : changer un prix sans
refaire la table convertie fait échouer `tests/test_i18n.py` et la porte D57. Les
photos sont servies par l'application sous `/images/` : la CSP publique n'ouvre que
`img-src 'self'`.

```powershell
curl.exe -s https://kder-ai.com/en/                              # la vitrine en anglais
curl.exe -sI https://kder-ai.com/images/commercante-colis.jpg    # image/jpeg, nosniff
```

Vérification : `.\.venv\Scripts\python.exe scripts\decisions_check.py D57`.

### 4.9 Agents IA et visites : mesurer au lieu de bloquer (D72, D73)

Le service **classe** chaque appelant en six familles (`agent_ia`,
`annuaire_mcp`, `moteur`, `outil`, `navigateur`, `inconnu`), **journalise** les
agents IA et les annuaires MCP dans le journal JSONL de D30 — jamais les visites
humaines — et **publie** cinq mesures (`agents`, `top_ip`, `top_agents`,
`top_pages`, `conversion`). Aucun agent n'est bloqué : un agent qui lit
`/pricing` est un prospect sans navigateur. La liste blanche des 25 agents IA
publiés par leurs éditeurs est dans `docs/AGENTS-IA.md` et dans la réponse de
`/metrics/agents`.

```powershell
curl.exe -s https://kder-ai.com/metrics/agents                        # en direct (compteurs du processus)
.\.venv\Scripts\python.exe scripts\analyser_agents.py --since 7d      # ce qui est écrit (journal JSONL)
.\.venv\Scripts\python.exe scripts\analyser_visites.py --since 24h    # tout le trafic (journal Nginx), 7 sections
```

`scripts/analyser_visites.py` rend sept sections — chiffres clés, temps, top
pages, top IP, codes de statut, clients, anomalies (sondes, chemins sensibles,
fuites). Les adresses y sont **pseudonymisées** (SHA-256 + sel, D30) sauf
`--ip-en-clair` demandé explicitement ; une fuite est un chemin sensible servi en
`2xx` (un `301` renvoie ailleurs, il n'expose rien). Les deux analyseurs lisent la
**même** table de classification : l'endpoint et les rapports ne peuvent pas se
contredire sur ce qu'est un agent.

Vérification : `.\\.venv\\Scripts\\python.exe scripts\\run_tests.py tests\\test_agents_ia.py tests\\test_visites.py`.


### 4.10 Adresses françaises : Base Adresse Nationale (D79)

Pour un pays de `PAYS_BAN` (France métropolitaine et outre-mer : `FR`, `RE`,
`GP`, `MQ`, `GF`, `YT`, `BL`, `MF`, `PM`), la vérification passe par la **Base
Adresse Nationale** (`app/validation/ban.py`, `https://data.geopf.fr/geocodage`)
et **jamais** par SmartyStreets, qui ne sait pas juger une adresse française :

* score BAN **≥ 0,8**, **code postal retourné identique** au code postal saisi,
  **ville identique** (accents, casse, tirets et ponctuation normalisés) ;
* types `housenumber` (0,99) et `street` (0,95) seulement — les autres
  (`municipality`, `locality`) n'ont pas de voie reconnue ;
* une panne BAN rend `degraded: true`, **sans repli** sur SmartyStreets ;
* côté américain, seul un code DPV `Y`/`S`/`D` valide une adresse ; `X` et `N`
  ne valident pas, et un candidat sans DPV rend `degraded: true`.

Les résultats France/outre-mer calculés avant ce correctif (30 jours de cache)
se purgent avec `scripts/purger_cache_ban.py` (simulation par défaut,
`--apply` pour supprimer). Mesure du 17/10/2026 : « 8 rue madelaine, Lyon,
75005 » passe de `validated: true` (cache) à `validated: false`, tandis que
Paris 75002, Lyon 69001 et Saint-Denis 97400 restent validées.


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

Le corps du `402` publié ci-dessus est un extrait : il porte aussi
`x402Version`, `network`, `accepts[]` (offre x402 v1) et désormais
`accepts[0].outputSchema`, le schéma Pydantic de `ValidationResponse` (D80).
Mesuré le 17/10/2026 : 1 788 octets de corps, en-têtes sous le tampon de 4 Ko
de nginx, et la même offre en v2 dans l'en-tête `PAYMENT-REQUIRED`.

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
| TTL d'un résultat **dégradé** | **5 minutes**, pas 30 jours : `degraded: true` dit « le fournisseur n'a pas répondu », pas « adresse invalide ». Le garder un mois figerait une panne passagère ; le garder 5 minutes suffit à ne pas marteler un fournisseur en panne. Un verdict vérifié, lui, reste 30 jours | D47 |
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

**x402 v2 aussi (D77).** Le même `402` porte l'offre en v2 dans l'en-tête
`PAYMENT-REQUIRED` (réseau `eip155:8453`, champ `amount`, extension `bazaar` du
catalogue Coinbase). Un client v2 paie par `PAYMENT-SIGNATURE` et reçoit
`PAYMENT-RESPONSE`. Sans aucun moyen de paiement, `/validate` répond `402` même
si le corps est vide : c'est ce que le contrôle du catalogue Coinbase attend.

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
constant, et confirmation obligatoire du reçu sur Base (D27, active par défaut ;
seule une recette déclarée `TEST_MODE=true` s'en passe) par lecture JSON-RPC
(`app/onchain.py`, sans paquet `web3` ni paquet de magasin de clés). Un paiement HMAC n'est jamais présenté comme un règlement x402 : il ne
porte pas `X-PAYMENT-RESPONSE`.

**MCP.** Le même outil est exposé en MCP sur `POST /mcp` (JSON-RPC 2.0, sans état,
sans flux SSE). Depuis D78, le paiement suit le **transport MCP x402 v2** : un
`tools/call` sans paiement rend un **résultat d'outil** `isError: true` dont
`structuredContent` est le `PaymentRequired` v2 (et `content[0].text` le même
document, réseau CAIP-2, ressource `mcp://tool/validate_address`) ; le client met
alors son `PaymentPayload` v2 dans `params._meta["x402/payment"]` et reçoit le
règlement dans `result._meta["x402/payment-response"]`. Un `402` JSON-RPC n'est
plus émis par ce transport : les clients x402 MCP lisent un résultat, pas un
statut HTTP. La compatibilité D34/D37 est gardée (un client v1 paie par
`X-PAYMENT` et garde ses en-têtes de règlement), et `settle_payment` est l'unique
chemin de vérification/encaissement des deux transports. La carte
`GET /.well-known/mcp-server` est servie par l'application, donc elle ne peut pas
décrire un endpoint éteint. Publication annuaire : `server.json`,
`.mcp.json`, `smithery.yaml` (voir `DISCOVERY.md` §1).

**Recette réelle.** `scripts/test_x402_payment.py` a trois modes : plan (vérifie
que `/manifest`, le corps `402` et l'offre du transport MCP publient la **même**
offre), `--check-domain` (compare le domaine annoncé au contrat on-chain) et
`--pay` (paiement réel ; exige un portefeuille acheteur financé en USDC sur Base et
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
| La recette déclarée (`TEST_MODE=true`) accepte la preuve à secret partagé sans interroger la chaîne | réservé aux essais de l'exploitant : les lignes écrites sont marquées `test_mode` et exclues du revenu réel ; hors recette, une preuve sans transfert USDC confirmé reçoit `402` | `§7`, `SECURITY.md` §3, D27, D40, D42 |
| Preuve rejouable pendant 300 s | une même preuve peut servir plusieurs fois dans la fenêtre | `§7`, `SECURITY.md` §6 |
| `address_validations` jamais alimentée | aucune trace d'adresse en base : la conservation repose sur le cache Redis (30 j) | `PRIVACY.md` §3 |
| `record_transaction` refuse de servir sans trace | depuis D41 un échec d'écriture rend un `500` (`ledger_write_failures_total`) : le paiement est accepté, la validation n'est pas servie, et l'encaissement doit être rapproché | `SECURITY.md` §5, D41 |
| Pas d'expiration des plans payants | un plan accordé à la main reste ouvert jusqu'à son retrait manuel : seul le quota **mensuel** referme la porte (D45). Aucune facturation automatique, donc aucune date de fin d'abonnement à faire respecter | `SECURITY.md` §3, D45 |
| Deux plafonds **produit** dans le code, l'anti-abus reste au *edge* | Nginx limite à 10 requêtes/s par IP (rafale 20, `429` au-delà) pour l'abus ; le code ajoute des plafonds métier qui **parlent** au client : 5 inscriptions par IP et par heure (`/signup`, D44) — et, avec `/validate-form`, 3 essais gratuits par IP et par 24 h (élément 3, D21 révisée) | `§9`, `SECURITY.md` §6, D21 (révisée), D44 |
| Métriques en mémoire | perdues au redémarrage, pas d'export Prometheus ni d'alerte | `SECURITY.md` §5 |
| Erreurs 4xx du fournisseur rejouées | latence dégradée mesurée à 11,1 s ; le back-off porte sur des erreurs non rejouables | `TEST_REPORT.md`, « Limites connues » |
| `CORS *`, `/metrics` et `/docs` publics, secret par défaut `change_me` encore accepté par le code | restent à durcir ; le déploiement, lui, refuse `change_me` et tout secret de moins de 32 caractères (`scripts/deploy-kder-ai.sh`) | `§9`, `SECURITY.md` §2 et §6 |
| Couverture France/outre-mer | vérifiée par la Base Adresse Nationale (D79) : score, code postal et ville ; panne BAN = `degraded`, jamais un verdict ; les résultats cachés avant le correctif se purgent une fois (`scripts/purger_cache_ban.py`) | `§4.10`, D79 |
| Contrat du `402` | `outputSchema` (schéma de sortie dérivé) et `network` racine publiés ; le corps passe à ~1,8 Ko, les en-têtes restent sous 4 Ko | `§5`, `§7`, D80 |
| `deploy.sh` ne crée pas `.env` | démarrage avec des secrets par défaut si l'étape manuelle est oubliée | `§9` |

Suite recommandée, par ordre de valeur :

1. Ne retenter que les erreurs **réseau et 5xx** du fournisseur : supprime le
   back-off sur `401` et ramène la latence dégradée sous ~3 s.
2. Consommer les preuves de paiement (anti-rejeu) et refuser le démarrage si
   `API_SECRET_KEY` est resté à sa valeur par défaut.
3. Brancher les métriques sur une supervision réelle et générer le `.env` au
   déploiement.




