# Rapport de tests — Address Validator API

Toutes les valeurs de ce rapport sont **mesurées** sur ce poste le **10/10/2026**
(Windows, Python 3.12.10, `.venv` du dépôt). Aucun chiffre n'est repris d'une
documentation antérieure : les valeurs historiques sont explicitement signalées
comme telles. L'objectif est de pouvoir refaire chaque mesure et retomber sur le
même ordre de grandeur.

## 1. Périmètre et méthode

Les mesures ont été faites avec l'API **réellement démarrée** (`uvicorn
app.main:app --port 8000`) et sans aucune dépendance externe configurée :
PostgreSQL et Redis ne tournent pas, et les identifiants SmartyStreets sont
absents. C'est un choix délibéré : c'est le pire régime réaliste (tout est en
panne sauf le réseau sortant), et c'est donc celui qui met le plus en évidence
les silences d'un oracle.

| Couche | Outil | Nature |
|--------|-------|--------|
| Unitaire / intégration hors-ligne | `pytest` | 46 tests, exécutés via `scripts/run_tests.py` |
| Contrat HTTP réel | `scripts/e2e_check.py` | 22 vérifications sur une instance démarrée |
| Documentation | `scripts/doc_check.py` | comptage des sections numérotées |
| Oracle de portes | `GATES.md` + `gate-lint.mjs` / `gate-check.mjs` | ledger D1–D26 |

Trois régimes sont distingués partout dans ce rapport, car ils ne disent pas la
même chose :

1. **nominal** — fournisseur joignable, cache chaud (non mesurable ici, faute
   d'identifiants) ;
2. **dégradé / réussite de la dégradation** — dépendances absentes, l'API doit
   répondre `200` avec `degraded: true` (c'est le régime mesuré) ;
3. **erreur fournisseur** — le fournisseur répond une erreur HTTP (mesuré : `401`,
   car aucun identifiant n'est configuré). Ce régime n'est **pas** couvert par la
   porte D23, voir §7.

## 2. Inventaire des tests

`pytest --collect-only -q` — 46 tests dans 10 fichiers :

| Fichier | Tests | Ce qu'il couvre |
|---------|-------|-----------------|
| `tests/test_app_smoke.py` | 3 | `/`, `/openapi.json`, `/docs` répondent |
| `tests/test_cache.py` | 7 | cache chaud/froid, ratio, Redis absent, cooldown D25 (dont sa levée) |
| `tests/test_circuit_breaker.py` | 4 | ouverture au seuil, `half-open`, remise à zéro, seuil non atteint |
| `tests/test_latency_budget.py` | 2 | rafale en panne totale (3 appels fournisseur / 1 accès Redis), retour du cache après cooldown |
| `tests/test_manifest.py` | 4 | `/manifest`, prix, schémas, protocole |
| `tests/test_parser.py` | 5 | analyse et canonisation de l'adresse |
| `tests/test_payment.py` | 8 | preuve valide, périmée, future, falsifiée, malformée, secret, forme du `402` |
| `tests/test_routes_extra.py` | 4 | `/health` (+ borne de temps), `/metrics` (+ compteur d'appels non payés) |
| `tests/test_standardizer.py` | 4 | abréviations, casse, `line2` (appartement) |
| `tests/test_validation.py` | 5 | pipeline complet : adresse invalide, abréviations, `degraded`, chemin nominal simulé, intégration réelle |
| **Total** | **46** | |

## 3. Résultats des tests automatisés

Commande : `.\.venv\Scripts\python.exe scripts\run_tests.py`

```text
GATE OK pytest: 45 passed, 0 failed, 1 skipped (46 collectés, exit=0)
```

- **45 passés, 1 ignoré, 0 échec**, code de sortie `0`.
- Le test ignoré est `tests/test_validation.py::test_valid_us_address`, protégé
  par `pytest.mark.skipif(not settings.smarty_auth_id, ...)` et marqué
  `integration` (`tests/test_validation.py:21-29`). Raison : il exige de vrais
  identifiants SmartyStreets et un accès réseau ; il n'a **pas** été exécuté ici.
  Le chemin nominal reste couvert hors-ligne, de façon déterministe, par
  `test_valid_address_with_mocked_provider` (fournisseur simulé `dpv_match_code
  = "Y"`, géocodeur simulé).
- `scripts/run_tests.py` ne lit pas la ligne de résumé humaine de pytest : cette
  ligne **n'existe pas** quand `-q` est lancé avec la sortie redirigée (vérifié :
  stdout muet, `PYTEST_ADDOPTS` vide). L'oracle s'appuie sur le rapport **JUnit
  XML** généré par pytest, donc sur des données structurées.

Les deux tests de `test_latency_budget.py` sont la preuve mécanique du budget :
en rafale dégradée (6 requêtes payantes, tout en panne), le fournisseur n'est
appelé que 3 fois et Redis qu'une seule fois — c'est D23 (disjoncteur) et D25
(cooldown) qui produisent ce comptage, pas une optimisation supposée.

## 4. Recette bout-en-bout (22 vérifications)

Commande : `.\.venv\Scripts\python.exe scripts\e2e_check.py http://127.0.0.1:8000`
(serveur démarré, PostgreSQL et Redis éteints, identifiants fournisseur absents)

```text
=== 22/22 verifications OK ===   (code de sortie 0)
```

Ce que la recette a réellement observé, groupé par critère de succès :

| Critère | Observations |
|---------|--------------|
| 1 — non payé ⇒ `402` | `POST /validate` sans en-tête → **402** ; le corps annonce `price=0.02` **et** `protocol=x402` |
| 1 — *fail-closed* | preuve **périmée** (horodatage 2009) → 402 ; **signature falsifiée** → 402 ; preuve **malformée** → 402 |
| 2 — payé ⇒ `200` | preuve valide → **200**, `result.standardized` présent, `price_paid == 0.02`, `transaction_id` repris de l'en-tête `X-Transaction-Id` |
| 2 — honnêteté de la dégradation | `degraded=True` alors que le fournisseur est injoignable, `line1='1600 Amphitheatre Parkway'`, `confidence=0.05` |
| 3 — cache | 2ᵉ appel identique → 200 avec le **même `cache_key`** ; Redis absent ⇒ `ratio=0.0` et recalcul complet : la dégradation est annoncée, pas maquillée en succès de cache |
| Entrée | charge utile incomplète → **422** |
| Service | `/`, `/manifest`, `/health`, `/metrics` → 200 ; `/health` expose bien les sous-états `database` et `redis` (`timeout after 2.0s` pour les deux) ; `uptime_seconds > 0` |

`confidence = 0,05` se décompose en `0,00` (livrabilité invalide, fournisseur
injoignable) `+ 0,05` (bonus accordé parce que les coordonnées ont été
obtenues) : le géocodeur a donc bien répondu lors de cette recette, ce qui est
cohérent avec la mesure de la §5.

## 5. Latences mesurées et attribution

### 5.1 Recette réelle (HTTP, depuis l'extérieur)

| Mesure | Valeur |
|--------|--------|
| `GET /health`, 5 exécutions | **1,999 s / 2,006 s / 2,012 s / 2,013 s / 2,023 s** (médiane et max ≈ 2,0 s — c'est la borne D22 : les deux sondes sont en panne et chacune est plafonnée à 2 s) |
| `POST /validate` payant, 1ᵉʳ appel (exécution finale de ce rapport) | **13,839 s** |
| `POST /validate` payant, 2ᵉ appel identique | **11,505 s** |
| `POST /validate` payant, 1ᵉʳ appel (exécution du même jour, un peu plus tôt) | **11,117 s** |
| `POST /validate` payant, 1ᵉʳ appel (rejeu du 10/10/2026, après réécriture du ledger) | **13,864 s** (voir §8) |

L'écart entre les exécutions (11,117 s contre 13,839 s, puis **13,864 s** au rejeu
du §8) montre que le
fournisseur est la variable dominante : la latence du chemin dégradé n'est pas
stable à l'échelle de la dizaine de secondes.

### 5.2 Attribution, en process

| Poste | Coût mesuré | Commentaire |
|-------|-------------|-------------|
| Lecture Redis (backend absent) | **2,03 s** | payée **une seule fois** : ensuite le cooldown D25 renvoie immédiatement |
| Appel fournisseur | **6,77 s** | réponse `401` **rejouée 3 fois** avec back-off |
| Géocodage Nominatim | **0,86 s** | réussi (voir `confidence = 0,05` en §4) |
| Pipeline `validate_full` complet | **7,62 s** | parse + standardize + verify + geocode + score |

Lecture honnête de ces chiffres : la **somme des postes attribués est d'environ
9,7 s**, alors que l'appel payant mesuré de bout en bout vaut **11,5 s à 13,9 s**.
L'écart de 2 à 4 s n'est **pas** attribué à ce stade. Candidats identifiés :
la variance de la latence du fournisseur (les `401` ne répondent pas toujours en
6,77 s) et les tentatives de connexion PostgreSQL de `record_transaction`, qui
sont en meilleur effort et donc invisibles côté réponse. Autrement dit : le
budget de latence est dominé par le réseau, pas par le code de l'API.

Le poste fournisseur est lui-même le symptôme d'un défaut de logique, détaillé
en §7 : le `@retry(stop_after_attempt(3))` rejoue des erreurs **4xx**, qui ne
sont pas rejouables par nature.

### 5.3 Valeurs historiques (citées, **non** re-mesurées ici)

Ces nombres proviennent des commentaires du code, qui les déclarent « constatés
en recette » : `15,2 s` pour une réponse dégradée avant D23
(`app/validation/verifier.py:13`, `tests/test_latency_budget.py:3`), `4,88 s`
de reliquat après D23 mais avant le cooldown D25
(`tests/test_latency_budget.py:3-4`), et `6,03 s` pour `/health` avant D22
(`app/routes/health.py:9`, `tests/test_routes_extra.py:21`). Ils n'ont pas été
reproduits dans cette session et sont donc à traiter comme de l'historique, pas
comme une preuve actuelle.

## 6. Qualité des oracles : savent-ils échouer ?

Un oracle qui ne peut pas échouer ne prouve rien. Chaque oracle utilisé ici a été
exercé en **négatif**, c'est-à-dire dans la situation où il doit refuser :

| Oracle | Test négatif | Résultat |
|--------|--------------|----------|
| `scripts/doc_check.py` | fichier de 2 sections alors que 10 sont exigées | refus + code de sortie `1` (voir ci-dessous) |
| `scripts/run_tests.py` | — | s'appuie sur le rapport **JUnit XML** ; un test en échec ou une erreur de collecte produit un code de sortie non nul, sans dépendre d'une ligne de résumé humaine |
| `scripts/e2e_check.py` | preuve falsifiée / périmée / malformée | les trois sont bien comptées comme attendues (`402`), et un `200` inattendu ferait échouer la vérification |
| `gate-lint.mjs` | ledger incomplet | rejette un `CHECK:` sans `EXPECT:` exécutable (voir `GATES.md`) |
| `gate-check.mjs` | porte absente ou non approuvée | une porte absente est un échec, pas un succès silencieux |

Preuve du test négatif de `doc_check.py`, exécutée le 10/10/2026 sur un fichier
temporaire de deux sections numérotées (`## 1.`, `## 2.`), supprimé après la mesure :

```text
> .\.venv\Scripts\python.exe scripts\doc_check.py tmp_doc_neg.md 10
DOC FAIL tmp_doc_neg.md sections=2 expected=10 numbering=[1, 2]
exit=1
```

Versions positives, la même session :

```text
DOC OK README.md sections=10 chars=17038   (exit=0)
DOC OK PRIVACY.md sections=6 chars=6401    (exit=0)
DOC OK SECURITY.md sections=6 chars=7452   (exit=0)
```

Principe de conception retenu pour tous les oracles de ce dépôt :
**échouer fermé** sur une entrée absente ou illisible. Un rapport manquant, un
fichier de résultats vide ou une porte non documentée doivent produire un échec,
jamais un vert par défaut.

## 7. Limites connues

Ces limites sont énoncées explicitement plutôt que dissimulées derrière un
« 45 tests passés ».

1. **Le régime « le fournisseur répond une erreur HTTP » n'est pas couvert par
   la porte D23.** D23 borne la *durée* d'un appel (connexion 2 s, lecture 4 s)
   et fait *court-circuiter* le fournisseur après 3 échecs, mais elle ne borne
   pas le **nombre de rejeux d'une erreur non rejouable**. Mesure : une réponse
   `401` est rejouée 3 fois et coûte **6,77 s** ; l'appel payant complet atteint
   alors 11,5 à 13,9 s. Un `@retry` qui ne filtre pas les `4xx` transforme une
   erreur de configuration immédiate en attente de plusieurs secondes. Aucune
   porte de `GATES.md` ne vérifie ce point : c'est un manque de la définition de
   D23, signalé en `DECISIONS.md`.
2. **Le chemin nominal n'était pas mesuré sur ce poste — il l'est désormais en
   production.** Faute d'identifiants SmartyStreets dans l'environnement local,
   la livrabilité réelle (`residential` / `commercial`) n'est prouvée ici que par
   simulation (`dpv_match_code = "Y"`, `tests/test_validation.py:87-116`) ; le
   seul test d'intégration réel est ignoré (`1 skipped`). **Mesure du 11/10/2026
   sur `https://kder-ai.com`** (clés du `.env` du VPS, preuve HMAC valide) :
   SmartyStreets répond `200` (`dpv_match_code = "Y"`, `rdi = "Commercial"`,
   `active = "Y"`) et `/validate` renvoie `validated = true`,
   `deliverability = "residential"`, `confidence = 1.0`, `degraded = false`.
   La limite ne porte donc plus que sur l'environnement de recette local
   (détail du diagnostic : `DEPLOIEMENT_VPS.md` §8.1).
3. **PostgreSQL n'est jamais exercé en succès.** La base est éteinte pendant
   toutes les mesures : `record_transaction` et l'écriture éventuelle de
   `address_validations` ne sont donc pas couvertes par une preuve réelle, et
   l'échec d'écriture est silencieux (meilleur effort).
4. **Le cache chaud n'est pas mesuré de bout en bout.** Redis étant absent, la
   recette observe le chemin dégradé (`ratio=0.0`) ; D5 n'est prouvé que par des
   tests utilisant un Redis simulé (`tests/test_cache.py`).
5. **Écart d'attribution non expliqué (2 à 4 s)** entre la somme des postes
   mesurés en process (~9,7 s) et la latence observée de bout en bout
   (11,5–13,9 s). §5.2 liste les candidats, sans preuve.
6. **Mesures mono-poste.** Aucune intégration continue, aucune charge
   concurrente, aucune mesure de débit, de contention ou de fuite mémoire. Les
   chiffres de ce rapport décrivent un poste Windows avec les dépendances
   éteintes, pas une production.
7. **Deux états de la même porte ne sont pas distinguables par le client** :
   « fournisseur interrogé et en échec » et « disjoncteur ouvert, aucun appel
   réseau » produisent tous deux `degraded = true`. L'information n'existe que
   dans `/metrics` (D24).

## 8. Ledger de portes : G0 + D1–D26

`GATES.md` ne contient plus que des oracles exécutables : **une porte par décision**
de `DECISIONS.md`, chaque `CHECK:` appelant `scripts/decisions_check.py <id>`, qui
imprime `DECISION OK <id>` (code 0) ou `DECISION FAIL <id>: <raison>` (code 1) et
rejoue **toutes** les portes même après un échec. Aucune porte n'est manuelle.
Rejeu intégral du 10/10/2026, après réécriture du ledger :

```text
> node .cline\skills\unlazy\scripts\gate-lint.mjs address-validator\GATES.md
LINT OK

> node .cline\skills\unlazy\scripts\gate-check.mjs --approve --jobs 4 address-validator\GATES.md
GATES.md: 27 gates
ALL MET (27 met)
exit=0, 13,7 s
```

Les 27 portes sont **G0** (le ledger se lint lui-même, zéro *finding* d'erreur) puis
**D1 à D26**. Chaque `EVIDENCE:` du ledger est écrite par `gate-check.mjs` : elle
porte le `sha256` de la définition de la porte, le code de sortie, le `sha256` de la
sortie observée et la correspondance exacte avec l'`EXPECT:`. Une porte dont la ligne
`CHECK:` change après coup est donc détectée comme non rejouée, et une porte absente
compte comme un échec, jamais comme un succès silencieux.

Mesures de la même session que ce rejeu (§5.1 et §6) : `scripts/run_tests.py`
→ **45 passed, 0 failed, 1 skipped** (46 collectés, `GATE OK pytest`, exit 0) et
`scripts/e2e_check.py` → **22/22 vérifications OK** (exit 0), appel payant en
**13,864 s**.

### 8.1 Défaut trouvé en durcissant l'oracle : `deploy.sh` en CRLF

En ajoutant une assertion à D20, un défaut réel est apparu : `deploy.sh` était
**entièrement en CRLF** (41 fins de ligne `\r\n`) alors que c'est un script
`#!/bin/bash` destiné à un VPS Debian/Ubuntu. Lancé en `./deploy.sh`, il échoue
avant toute instruction : `/bin/bash^M: bad interpreter: No such file or
directory` — un déploiement censé « échouer vite » ne démarrait pas du tout. Le
fichier a été réécrit en LF (seuls les octets de fin de ligne changent, aucune
ligne de commande n'est touchée) et D20 porte désormais l'assertion
`b"\r\n" not in (ROOT / "deploy.sh").read_bytes()`.

Test négatif du nouvel oracle, exécuté le 10/10/2026 sur une copie CRLF
temporaire, restaurée ensuite à l'identique (empreinte `sha256`
`5591325061AA…` vérifiée avant et après) :

```text
> .\.venv\Scripts\python.exe scripts\decisions_check.py D20
DECISION FAIL D20: deploy.sh contient des fins de ligne CRLF (/bin/bash^M sur le VPS Debian)
exit=1
```

Le ledger complet, rejoué après ces changements, reste vert : **27 portes,
`ALL MET`** (`reran: 27`), exit 0, 13,5 s.


