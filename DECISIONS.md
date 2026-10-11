# Décisions — address-validator

Journal des écarts assumés par rapport à la spécification d'origine et des
correctifs issus de la recette. Les identifiants `D1..D26` sont **chronologiques**
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

