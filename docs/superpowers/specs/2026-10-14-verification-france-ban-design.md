# Vérification des adresses françaises par la BAN — design

Date : 14/10/2026 — branche `verification-france-ban` — décision à consigner : **D57**.

## 1. Pourquoi

Le fournisseur actuel est Smarty **US Street** (`app/validation/verifier.py:37`) : il ne
connaît que les États-Unis. Mesuré sur le VPS le 14/10/2026 avec `validate_full` :

| Adresse | Réponse actuelle |
|---|---|
| 55 rue du Faubourg Saint-Honoré, 75008 Paris (réelle) | `validated: true`, `unknown`, 0,55 |
| 999 rue Qui N'existe Pas, 75008 Paris (inventée) | `validated: true`, `unknown`, 0,50 |
| 1600 Amphitheatre Pkwy, 94043 CA | `validated: true`, `residential`, 1,0 |

Pour la France, le service ne distingue pas une adresse réelle d'une adresse inventée, et
répond `validated: true` aux deux. Or le guide (`app/routes/guide.py:360`) dit aux
boutiques « `validated: false` : signalez la commande avant l'expédition » : une adresse
fausse part donc en livraison. La cible commerciale (prospection, client test Tiitoune Art
sur Shopify, à La Réunion) est française.

Le géocodeur de l'IGN (Géoplateforme, base BAN), interrogé depuis le VPS le même jour :

| Requête | Meilleur candidat |
|---|---|
| 55 Rue du Faubourg Saint-Honoré 75008 Paris | `housenumber`, 0,976, même adresse |
| 55 Rue du Faubourg Saint-Honoré **75001** Paris | `housenumber`, 0,811, **corrigé en 75008** |
| 999 Rue Qui N'existe Pas 75008 Paris | `street`, 0,469, « Rue Pasquier » (autre rue) |
| 12 bis rue de la Paix 75002 Paris | `street`, 0,694, rue réelle, numéro absent |
| 1 Rue de Paris 97400 Saint-Denis | `street`, 0,833, rue réelle, numéro absent |
| 12 rue des Lilas 97410 Saint-Pierre | `housenumber`, 0,713, « 12 Allée des Lilas » |
| Chemin Bois de Nefles 97400 Saint-Denis | `street`, 0,665, « Rue du Bois de Nèfles » |

Conditions d'usage publiées : 50 requêtes/s par IP, sans clé, licence Etalab 2.0, temps de
réponse mesuré de 0,13 à 1,4 s. Aucun engagement de service publié (page « niveau de
service » en 404 le 14/10/2026).

## 2. Objectif et critères de succès

1. Une adresse française réelle, trouvée au numéro près, rend `validated: true` et sa forme
   **corrigée** par la BAN (code postal, accents, type de voie) avec ses coordonnées.
2. Une adresse inventée rend `validated: false` ; aucune adresse fausse de l'échantillon
   de calibration n'est classée `deliverable`.
3. La Réunion et les DOM couverts par la BAN (GP, MQ, GF, RE, YT) le sont aussi.
4. Les États-Unis restent servis par Smarty, avec la même règle pour `validated`.
5. Un pays non couvert est refusé **avant** toute demande de paiement, et la liste des pays
   couverts publiée par l'API découle d'une seule source dans le code.
6. Aucun texte public (manifeste, carte MCP, guide, vitrine, `/pricing`, `llms.txt`,
   `server.json`) ne promet plus que ce que le code fait.

Hors périmètre : autres pays (Belgique, Suisse…), Saint-Martin, Saint-Barthélemy,
Saint-Pierre-et-Miquelon (couverture BAN non mesurée), prix par pays, champ « type de
local » (résidentiel ou commercial), traitement par lot (CSV), extension WooCommerce ou
PrestaShop, intégration des maquettes multilingues.

## 3. Architecture

```
adresse reçue
  └─ AddressInput : country obligatoire, ∈ PAYS_COUVERTS ?  non → 422 / -32602 (gratuit)
       └─ paiement (clé API ou x402)
            └─ perform_validation : cache (clé « v2 »)
                 └─ validate_full : FOURNISSEURS[country](adresse standardisée)
                      ├─ US        → smarty (app/validation/verifier.py) + Nominatim
                      └─ FR et DOM → ban (app/validation/ban.py), coordonnées incluses
```

- **Une table unique** dans `app/validation/__init__.py` :
  `FOURNISSEURS = {"US": smarty, "FR": ban, "GF": ban, "GP": ban, "MQ": ban, "RE": ban, "YT": ban}`
  et `PAYS_COUVERTS = sorted(FOURNISSEURS)`. Le schéma d'entrée, le manifeste, la
  description de l'outil MCP et le champ `coverage` de la carte MCP lisent cette table.
  Ajouter un pays = ajouter une ligne (et un module si le fournisseur est nouveau).
- **Contrat d'un fournisseur** : une fonction `(standardized: dict) -> dict` qui rend
  `deliverability`, `confidence`, `standardized` (forme corrigée ou `None`),
  `suggestion` (forme proposée ou `None`), `lat`, `lng` (ou `None`) et, en cas d'échec
  d'appel, `error`. `validate_full` assemble la réponse ; il appelle Nominatim seulement
  si le fournisseur n'a pas donné de coordonnées.
- **Nouveau module `app/validation/ban.py`** : un appel
  `GET https://data.geopf.fr/geocodage/search?q=<line1> <zip> <city>&index=address&limit=1`,
  avec le `User-Agent` de `geocoder.py`, le même budget de temps et de reprise que Smarty
  (D23), et **son propre disjoncteur** (instance de `CircuitBreaker`) : une panne de l'IGN
  ne coupe pas les États-Unis, et inversement. Le code postal n'est pas passé en filtre :
  c'est ce qui permet de le corriger.
- Le paiement, le registre, la clé d'API, les quotas et le mode dégradé ne changent pas.

## 4. Verdict commun à tous les pays

| `deliverability` | Sens | `validated` |
|---|---|---|
| `deliverable` | adresse trouvée au numéro près | **true** |
| `incomplete` | voie trouvée, numéro (ou appartement) absent | false |
| `invalid` | adresse introuvable | false |
| `unknown` | fournisseur injoignable (`degraded: true`) | false |

Règle unique : `validated = (deliverability == "deliverable")`. Les valeurs
`residential` et `commercial` disparaissent (aucun appel extérieur n'a jamais été servi :
journal des refus et `access.log` du VPS, 11 au 14/10/2026).

**France (meilleur candidat de l'IGN)** :

- `deliverable` si `type == "housenumber"`, `score >= SEUIL_NUMERO` et le numéro rendu
  égale le numéro saisi (comparaison en minuscules, espaces retirés : `12 bis` = `12bis`).
  Le numéro saisi est `street_number` suivi du suffixe `bis`, `ter` ou `quater` quand
  `street_name` commence par l'un d'eux (le découpage actuel lit « 12 bis rue… » comme
  `12` + « bis rue… »).
  `standardized` devient la forme BAN : `line1 = name`, `city = city`, `zip = postcode`,
  `state` = dernier élément de `context` (« Île-de-France », « La Réunion »),
  `line2` et `country` repris de la saisie ; coordonnées = `geometry.coordinates`
  (ordre longitude, latitude) ; `confidence = score`.
- `incomplete` si `type` vaut `street` ou `locality` et `score >= SEUIL_RUE`.
  `standardized` reste la saisie ; `suggestion` = la voie BAN (même forme, sans numéro).
- `invalid` sinon (score sous les seuils, numéro différent, aucun candidat, type
  `municipality`). `suggestion` = le candidat s'il atteint `SEUIL_SUGGESTION`.
- `unknown` + `degraded: true` si l'appel échoue (réseau, 4xx/5xx dont 429, disjoncteur
  ouvert) ; cache 5 minutes comme aujourd'hui (D47).

`SEUIL_NUMERO`, `SEUIL_RUE` et `SEUIL_SUGGESTION` sont des constantes de `ban.py` fixées
par la calibration (§7) ; valeurs de départ 0,80, 0,60 et 0,50 d'après le §1. Une
suggestion ne remplace jamais la saisie : c'est la boutique qui décide.

**États-Unis (code DPV de Smarty)** : `Y` → `deliverable` (0,99) ; `S` → `deliverable`
(0,95) ; `D` → `incomplete` (0,60) ; `N`, vide ou absent → `invalid` (0,0). Le bonus
de +0,05 quand Nominatim géocode est conservé, plafonné à 1,0.

## 5. Contrat public

- **Entrée** : `AddressInput.country` devient **obligatoire** (plus de défaut `US`),
  normalisé en majuscules, et doit appartenir à `PAYS_COUVERTS` ; le schéma JSON publie
  la liste (`enum`). Un pays absent ou non couvert rend `422` en HTTP et `-32602` en MCP
  avec la liste des pays couverts, avant le paiement : la validation du modèle précède
  déjà `payment_result` dans `/validate` (FastAPI) et dans `mcp.py:203`. Aucun quota de
  clé d'API n'est consommé.
- **Sortie** : nouveau vocabulaire de `deliverability`, règle de `validated` ci-dessus,
  champ facultatif `suggestion: dict | None` (mêmes clés que `standardized`).
- **Cache** : la clé devient `sha256("v2:" + adresse)` ; les anciennes réponses
  françaises erronées ne sont plus lues et expirent seules.
- **`/metrics`** : ajout de `ban_circuit_breaker` à côté de `provider_circuit_breaker`
  (qui reste celui de Smarty).
- **Textes à aligner** :
  - `app/routes/manifest.py` : `deliverability_values`, exemples, limite de couverture ;
  - `app/routes/mcp.py` : description de l'outil, `coverage` de la carte (pays de la
    table, séparés par des virgules) ;
  - `app/routes/guide.py` : vocabulaire, exemples, ligne `422`, exemple CSV (`pays`
    obligatoire) ;
  - `app/routes/pricing.py` : FAQ « Couvrez-vous La Réunion et l'international ? » ;
  - `app/templates/accueil.html` : bloc de couverture (France et DOM : vérifiée) ;
  - `app/routes/validate_form.py` : pays par défaut `FR`, verdict et suggestion affichés,
    message pour un pays non couvert ;
  - `llms.txt`, `server.json`, `README.md`, `DECISIONS.md` (D57).

## 6. Erreurs et dégradé

Un fournisseur ne lève jamais d'exception vers la route : tout échec devient
`deliverability: "unknown"`, `confidence: 0`, `error` renseigné, donc `degraded: true`.
L'IGN qui répond `429` (plus de 50 requêtes/s depuis le VPS) est traité comme une panne.
`ponytail:` un seul VPS partage ce plafond de 50 requêtes/s ; au-delà, compte authentifié
IGN ou file d'attente.

## 7. Tests, calibration, mise en ligne

- **Tests sans réseau** : réponses réelles de l'IGN enregistrées dans `tests/fixtures/ban/`
  (les sept requêtes du §1 au minimum) ; un test par branche du verdict France, un par
  code DPV, le refus d'un pays non couvert sur `/validate`, `/mcp` et clé d'API (aucun
  `402`, aucun quota consommé), la clé de cache v2, la panne de l'IGN (dégradé, disjoncteur
  séparé). Les tests et portes qui attendent `residential` sont mis à jour
  (`tests/test_validation.py`, `test_manifest.py`, `test_validate_form.py`,
  `test_validate_api_key.py`, `scripts/decisions_check.py`), et une porte `d57` s'ajoute.
- **Calibration, une fois en réel** : `scripts/calibrer_ban.py` interroge l'IGN sur un
  échantillon d'environ 40 adresses tirées de la BAN (au moins un tiers au 974) et leurs
  variantes faussées (code postal, faute de frappe, numéro inexistant, rue inventée), puis
  imprime le tableau vrai/faux par seuil. Aucune adresse de client. Seuils retenus : les
  plus bas qui ne classent **aucune** variante inventée en `deliverable`. Le tableau et les
  seuils vont dans D57.
- **Point d'arrêt** : si plus d'un tiers des adresses réelles du 974 sortent `incomplete`,
  on en discute avant de livrer (vérifications manuelles accrues pour Tiitoune Art).
- **Mise en ligne** : PR vers `main`, fusion, `scripts/deploy-kder-ai.sh`, puis contrôle en
  production : les trois adresses du §1, une adresse allemande refusée en `422` sans `402`,
  `/manifest` et `tools/list` à jour.

## 8. Risques

- **Service public sans engagement** : l'IGN peut ralentir ou changer d'API ; le
  disjoncteur et le mode dégradé couvrent la panne, pas un changement de format (la porte
  `d57` et les fixtures le détectent au prochain test).
- **Modèle « freemium » de l'IGN** : les gros utilisateurs peuvent devoir payer, seuils non
  publiés. À surveiller quand le volume dépassera quelques milliers d'appels par jour.
- **Changement de contrat** (`country` obligatoire, nouvelles valeurs) : sans client
  extérieur aujourd'hui ; Tiitoune Art est prévenue avant la mise en ligne si elle a déjà
  intégré l'API.
