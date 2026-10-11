# Décisions — address-validator

Journal des écarts assumés par rapport à la spécification d'origine et des
correctifs issus de la recette. Les identifiants `D1..D39` sont **chronologiques**
(ordre de décision), pas des numéros de phase.

Règle de provenance : chaque ligne cite le fichier qui **contient** la décision
(docstring ou commentaire de code). Aucune décision n'est listée ici sans
référence vérifiable dans le dépôt.

| ID | Décision | Pourquoi | Source |
|----|----------|----------|--------|
| D1 | Épingler `cdp-sdk==1.0.1` au lieu de `1.0.0` | la version demandée n'existe pas sur PyPI ; release publiée la plus proche | `requirements.txt:6` |
| D2 | Retirer `smarty-street-python` des dépendances | paquet inexistant (HTTP 404) ; le code interroge l'API HTTP directement, il n'était importé nulle part | `requirements.txt:7-9` |
| D3 | Ajouter `pydantic-settings` | importé par `app/config.py`, absent de la liste de la spec | `requirements.txt:10` |
| D4 | Relever `pydantic` de 2.9.2 à 2.10.3 | `cdp-sdk>=1.0.1` épingle `pydantic==2.10.3` : conflit dur de résolution | `requirements.txt:11-12` |
| D5 | Brancher le cache Redis dans la route `/validate` | la spec exige « le cache réduit les appels fournisseur de 80 % » sans brancher le cache dans la route | `app/routes/validate.py:3-8` |
| D6 | Import et configuration du SDK CDP défensifs ; vérification *fail-closed* | une clé absente ou un SDK à un autre niveau d'API ne doit pas empêcher l'API de démarrer ; aucun paiement deviné | `app/payment.py:5-8,40-74` |
| D7 | Durcir la preuve de paiement : fenêtre de fraîcheur symétrique 300 s + HMAC-SHA256 comparé en temps constant | le squelette acceptait n'importe quelle chaîne `tx:ts:sig` (rejeu trivial) ; la fenêtre rejette le périmé **et** le futur | `app/payment.py:10-16,120-125` |
| D8 | `extra = "ignore"` dans `Settings` | une variable d'environnement non déclarée (celles du VPS) ne doit pas faire échouer le démarrage | `app/config.py:3-5,40` |
| D9 | `Lock` autour de l'état du disjoncteur + méthode `reset()` | uvicorn sert plusieurs requêtes en parallèle : l'état partagé doit être thread-safe et réarmable | `app/validation/verifier.py:3-6,53` |
| D10 | `User-Agent` explicite avec adresse de contact sur Nominatim | la politique d'usage d'OpenStreetMap l'exige ; un UA anonyme peut être bloqué | `app/validation/geocoder.py:3-4,17` |
| D11 | Ajouter la table `payment_transactions` au DDL | la couche paiement doit tracer les transactions ; `ON CONFLICT DO NOTHING` rend l'enregistrement idempotent | `app/database.py:9,43-51` |
| D12 | Timeouts Redis courts (connect 1 s, read 1 s) + compteur d'erreurs exposé | un Redis absent doit dégrader la latence, pas bloquer l'API ; le compteur alimente la supervision | `app/cache.py:3-5,30-35` |
| D13 | Modèles ORM SQLAlchemy en miroir du DDL SQL brut | typer les écritures Python ; double maintenance assumée, toute évolution de schéma va des deux côtés | `app/models.py:1-7` |

| D14 | `connect_timeout=2`, `pool_pre_ping=True`, `init_db()` idempotent non bloquant | un `/health` ne doit pas pendre si la base est injoignable ; l'API démarre même sans base | `app/database.py:3-9,19-23,64-72` |
| D15 | Preuve de paiement à **secret partagé**, pas de vérification on-chain EIP-3009 | aucun facilitateur x402 n'est dans le périmètre ; chemin de mise à niveau décrit (README §7) | `app/payment.py:18-20` |
| D16 | `lifespan` qui tente `init_db()` au démarrage, échec non bloquant | créer les tables sans rendre le démarrage de l'API dépendant de la base | `app/main.py:3-5,24-33` |
| D17 | Registre de métriques en mémoire, thread-safe, sans dépendance externe | `/metrics` est exigé mais son contenu ne l'est pas ; Prometheus reste branchable sans toucher aux appels | `app/routes/metrics.py:3-6,22-55` |
| D18 | Dépendances de test isolées dans `requirements-dev.txt` | `pytest`/`pytest-cov` ne doivent pas alourdir l'image de production | `requirements-dev.txt:1-6` |
| D19 | Comparer `>=` (et non `>`) à l'expiration du disjoncteur | sur une horloge à résolution grossière (Windows ~15 ms), `time.time() - last_failure_time` peut valoir exactement `0.0` et bloquer la sonde half-open | `app/validation/verifier.py:58-61` |
| D20 | `set -euo pipefail` en tête de `deploy.sh` | un script d'installation qui continue après un `apt install` échoué laisse le VPS incohérent | `deploy.sh:3-6` |
| D21 | Pas de rate limiting par IP dans l'application : délégué au reverse proxy Nginx | un compteur en mémoire ne serait pas partagé entre workers ; le rôle est tenu au niveau *edge* (voir `SECURITY.md` §6) | `.gitignore:2-3` |
| D22 | Sondes `/health` hors boucle, en parallèle, bornées à 2 s | mesuré 6,03 s (4,02 s Postgres + 2,02 s Redis) : les appels bloquants gelaient la boucle d'événements de **toutes** les requêtes | `app/routes/health.py:6-17,29-46` |
| D23 | Budget d'appel fournisseur borné : timeout granulaire (connect 2 s / read 4 s), seuil du disjoncteur applicatif abaissé à 3 | réponse dégradée mesurée à 15,2 s, et 5 échecs nécessaires avant *fast-fail* (75 s) — incompatible avec une API payante synchrone. La classe `CircuitBreaker` garde les valeurs de la spec (5 / 60 s) ; la politique de retry (3 tentatives, back-off) est conservée | `app/validation/verifier.py:11-23,39-40,90-93` |
| D24 | Exposer l'état du disjoncteur et l'état du cache dans `/metrics` | sans cela « fournisseur interrogé en échec » et « disjoncteur ouvert, aucun appel réseau » produisent tous deux `degraded=true` : indistinguables en supervision | `app/routes/metrics.py:60-80` |
| D25 | Fenêtre de refroidissement de 30 s après un échec du backend cache | Redis éteint : ~2 s par lecture **et** ~2 s par écriture à **chaque** requête, indéfiniment ; une panne doit coûter une fois. `check_redis()` reste une sonde réelle | `app/cache.py:10-17,39,51-61,103-111` |
| D26 | Timeout géocodeur borné (connect 2 s / read 3 s) | un Nominatim qui *pend* immobilisait une requête payante 10 s pour un résultat facultatif (l'échec retombe sur `lat/lng = null`) | `app/validation/geocoder.py:6-9,18` |
| D27 | Confirmation on-chain du paiement en **lecture JSON-RPC** sur Base — optionnelle, désactivée par défaut | la preuve HMAC de D15 est forgeable par quiconque connaît le secret, et le squelette prévoyait un facilitateur x402 via le SDK CDP : le SDK épinglé (`cdp-sdk==1.0.1`) n'expose pas `Cdp.configure` (constaté dans le journal du VPS : `CdpClient has no attribute 'configure'`). La couche lit donc le reçu (`eth_getTransactionReceipt` + `eth_blockNumber`), exige un transfert USDC confirmé vers `CDP_WALLET_ADDRESS` pour au moins `X402_PRICE`, sans paquet web3 ni authorization EIP-3009, et refuse tout le reste (fail-closed) dès que `X402_ONCHAIN_VERIFY=true`. Désactivée par défaut pour que D15 reste la référence du service et que la recette existante (preuve synthétique, 22 contrôles) reste mesurable | `app/onchain.py`, `app/payment.py:18-25,139-147`, `app/config.py:36-45`, `.env.example:21-27` |
| D28 | Publier `llms.txt`, `AGENTS.md` et `.well-known/security.txt` ; **ne pas** publier `/.well-known/mcp-server` ni `ard.json` | un agent ne découvre un service que par des fichiers qui répondent : un manifeste MCP exigerait un endpoint MCP réel (`/mcp` répond 404) et `ard.json` suit une spécification « ARD » qu'aucune source vérifiable ne documente. D28 refuse tout chemin `kder-ai.com` cité qui ne correspond à aucune route mesurée, et refuse un `security.txt` sans `Expires` ou déjà périmé | `llms.txt`, `AGENTS.md`, `.well-known/security.txt`, `nginx-discovery.conf`, `scripts/decisions_check.py` (`d28`) |
| D29 | Identifier un paiement par le hash **lu dans la preuve** (`tx_hash_from_proof`), non par l'en-tête optionnel `X-Transaction-Id` | le registre durable doit compter ce qui a été payé, pas ce que le client déclare ; `X-Transaction-Id` reste un simple repli, et c'est `ON CONFLICT (tx_hash) DO NOTHING` (D11) qui rend l'écriture idempotente | `app/payment.py` (`tx_hash_from_proof`), `app/routes/validate.py:82-90` |
| D30 | Journaliser chaque refus 402 en JSONL, IP **pseudonymisée** par défaut (sha256 + sel), purge à 30 jours | sans ce journal, un agent qui découvre `/validate` et repart en 402 est invisible : impossible de distinguer « pas trouvé » de « trouvé mais pas payé ». Minimisation : jamais l'adresse validée, IP en clair seulement sur activation explicite et datée, conservation annoncée réellement appliquée par le rapport quotidien | `app/observability.py`, `app/config.py:47-65`, `.env.example`, `PRIVACY.md` §1-3 |
| D31 | `/metrics/revenue` agrège les revenus **côté Python** depuis `payment_transactions`, fenêtres 24 h / 7 j / 30 j, et publie `ledger.available` + `ledger.error` | la base peut être muette : afficher `0` sans le dire serait trompeur. L'agrégation Python évite une dépendance SQL propriétaire et borne la lecture (5 000 lignes, 3 s) ; la panne devient une donnée de la réponse, jamais une exception | `app/routes/revenue.py`, `app/main.py` |
| D32 | `/dashboard` en **fail-closed** : mot de passe absent = 503, sinon Basic Auth ; page HTML autonome, sans ressource externe | un tableau de bord public exposerait le chiffre d'affaires et les dernières transactions ; une page chargeant un CDN livrerait l'adresse de l'exploitant et casserait hors ligne. Le défaut sûr est « fermé », jamais « ouvert » | `app/routes/dashboard.py`, `app/config.py:67-71` |
| D33 | Le manifeste publie des **schémas dérivés des modèles Pydantic**, des exemples, des cas d'usage, des limites et les valeurs de `deliverability` | un schéma recopié dérive du contrat réel dès la première évolution ; `model_json_schema()` ne le peut pas. Les limites déclarent noir sur blanc que le protocole x402 standard n'est pas implémenté et qu'aucun serveur MCP n'existe : publier le contraire serait une fausse déclaration | `app/routes/manifest.py`, `app/schemas.py` |
| D34 | Corps `402` au format **x402 v1** (`x402Version` + `accepts[]`) et en-tête de règlement `X-PAYMENT-RESPONSE` ; la route accepte `X-PAYMENT` | un agent x402 autonome doit pouvoir payer sans qu'une preuve lui soit fournie par l'exploitant. L'offre publiée dans `/manifest` est produite par la **même** fonction que celle facturée, donc le manifeste ne peut pas diverger du corps `402`. La preuve HMAC (D15) reste acceptée pour l'outillage, sans jamais être présentée comme un règlement x402 | `app/x402.py`, `app/payment.py`, `app/routes/validate.py`, `app/routes/manifest.py` |
| D35 | Vérification **hors ligne** de la signature EIP-712 (`TransferWithAuthorization`, EIP-3009) : domaine, destination, montant, jeton, réseau, fenêtre de validité, récupération du signataire | rien ne sort du serveur avant que la signature soit conforme : un facilitateur lent ou en panne ne doit pas devenir le prétexte d'un service rendu. La correction est prouvée par un **vecteur externe** (la charge utile publiée par la spécification x402 redonne l'adresse documentée) et par le keccak natif du VPS | `app/x402.py` (`eip712_digest`, `recover_signer`), `app/config.py:47-74` |
| D36 | Le service n'est rendu qu'**après règlement** par le facilitateur (`/verify` puis `/settle`), JWT CDP (ES256/EdDSA) joint à l'appel, hash du règlement enregistré au registre (D29) | une autorisation signée n'est pas un encaissement : sans règlement le service serait livré gratis. Tout échec (refus explicite, panne de transport, hash absent, rejeu du nonce) vaut un refus. Le JWT est signé localement et vérifié par l'oracle, sans réseau | `app/x402.py`, `app/payment.py` |
| D37 | Serveur MCP **réel** (`POST /mcp`, JSON-RPC 2.0) exposant l'outil `validate_address`, et carte `/.well-known/mcp-server` **servie par l'application** | la découverte MCP annonçait un endpoint inexistant (constat D28) ; l'endpoint existe désormais : sans état, sans flux SSE, gratuit à la découverte et payant à l'appel. La carte est une route de l'application, jamais une copie statique : elle disparaît avec l'endpoint qu'elle décrit | `app/routes/mcp.py`, `app/main.py`, `nginx-discovery.conf` |
| D38 | Fichiers d'annuaire (`server.json`, `.mcp.json`, `smithery.yaml`) et primitives de la porte de paiement **épinglées** (`eth-abi`, `eth-keys`, `eth-utils`, `eth-hash`, `pycryptodome`) | publier un format dont la source n'est pas vérifiable est précisément ce que D28 interdit : `server.json` suit le schéma officiel du registre MCP, `.mcp.json` est la configuration cliente documentée, et `smithery.yaml` ne reproduit **pas** le schéma local non confirmé (la publication distante se fait par `smithery mcp publish <url>`). Ces paquets n'étaient que des dépendances transitives : la porte de sûreté du paiement ne peut pas dépendre d'un `pip install` ultérieur | `server.json`, `.mcp.json`, `smithery.yaml`, `requirements.txt` |
| D39 | Rétention de 30 jours appliquée par `scripts/prune_discovery_log.py` et le couple `address-validator-retention.{service,timer}` (03:30 UTC, `Persistent=true`) | une durée de conservation annoncée mais jamais appliquée est un mensonge (`PRIVACY.md` §3). La purge est une tâche planifiée et mesurée (l'oracle la lance sur un journal daté), et `Persistent=true` rattrape une purge manquée VPS éteint | `scripts/prune_discovery_log.py`, `address-validator-retention.service`, `address-validator-retention.timer` |

## Notes de provenance

- **D18 et D21 n'étaient référencés nulle part** dans le code : ils n'existaient
  que comme renvois (`requirements-dev.txt`, `.gitignore`). Ils sont documentés
  ci-dessus à partir de ces deux renvois, sans rien inventer.
- **D4 a deux histoires.** L'ancien `GATES.md` donnait à `D4` le contenu
  « toolchain locale Python 3.14 vs cible 3.12 ». La source de vérité est le code
  (`requirements.txt:11`), qui décrit le conflit `pydantic` ; c'est ce contenu que
  porte `D4` dans le ledger actuel (`GATES.md`, un oracle par décision, D1–D26).
  L'observation sur la toolchain reste vérifiable mais n'est pas une décision :
  mesuré le 10/10/2026, le venv du projet est en **Python 3.12.10** et la cible
  Docker est `python:3.12-slim` — ils concordent (voir `TEST_REPORT.md`).
- **Troisième régime de latence du fournisseur, non couvert par D23.** Le
  docstring de D23 décrit un fournisseur qui *refuse* la connexion (~3 s) et un
  fournisseur qui *pend* (coupé à 12 s). Mesuré le 10/10/2026, un fournisseur qui
  **répond une erreur HTTP** (401 d'authentification) coûte 3 allers-retours
  plus 3 s de back-off, soit ~6 s : `@retry` rejoue aussi les 4xx, qui ne sont
  pas rejouables. Conséquence et recommandation en `TEST_REPORT.md`,
  « Limites connues ».
- **Ce qui n'est pas décidé ici** : le comportement du fournisseur et du
  géocodeur lorsqu'ils sont injoignables reste dominé par le réseau réel. La
  latence dégradée résiduelle mesurée et son attribution figurent dans
  `TEST_REPORT.md`.
- **`deploy.sh` était en CRLF** (trouvé en durcissant l'oracle de D20) : les 41
  fins de ligne du script étaient `\r\n`, ce qui donne `/bin/bash^M: bad
  interpreter` sur un VPS Debian — le script ne démarrait pas. Corrigé en LF, et
  D20 refuse désormais un `deploy.sh` contenant `\r\n` (test négatif en
  `TEST_REPORT.md` §8.1).

