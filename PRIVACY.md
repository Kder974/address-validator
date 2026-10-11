# Confidentialité — address-validator

Ce document décrit quelles données l'API traite, pourquoi, combien de temps et
qui les reçoit. Il décrit le comportement **du code de ce dépôt**, vérifié le
10/10/2026. Il ne remplace pas un avis juridique : la conformité de
l'exploitation (hébergeur, contrat de sous-traitance, clauses de transfert)
reste à la charge de l'exploitant.

## 1. Données traitées

| Donnée | Origine | Où c'est défini |
|--------|---------|-----------------|
| Adresse postale : `street`, `city`, `state`, `zip`, `country` | fournie par le client à chaque appel | `app/schemas.py:11-16` |
| Preuve de paiement : `tx_hash`, horodatage, signature HMAC | en-tête `X-Payment-Proof` | `app/payment.py:37,102-127` |
| Adresse standardisée, latitude/longitude, code DPV, confiance | produite par le pipeline | `app/validation/__init__.py:33-41` |
| Clé de cache = SHA-256 de l'adresse brute canonisée | produite par l'API | `app/routes/validate.py:32-34` |
| Métadonnées de requête en cas de refus `402` : horodatage, endpoint, prix, `User-Agent`, `Referer`, **IP pseudonymisée** | produite par l'API (jamais l'adresse validée) | `app/observability.py:89-113` |

L'API ne traite **aucun identifiant de personne** : pas de compte, pas de nom,
pas d'e-mail, pas de cookie, pas d'identifiant d'appareil. L'adresse postale est
une donnée à caractère personnel lorsqu'elle se rattache à une personne (RGPD
art. 4-1) : aussi la clé de cache est-elle un **pseudonyme** (SHA-256), pas une
adresse en clair.

## 2. Finalités

| Finalité | Données mobilisées | Base légale (RGPD art. 6) |
|----------|--------------------|---------------------------|
| Valider l'adresse et estimer sa livrabilité | adresse, résultat standardisé | exécution du contrat (1-b) |
| Facturer à la requête (0,02 USDC) | `tx_hash`, montant | exécution du contrat (1-b) |
| Éviter de payer deux fois le même appel fournisseur | clé SHA-256 + résultat | intérêt légitime : maîtrise des coûts (1-f) |
| Superviser la disponibilité et les pannes | compteurs agrégés | intérêt légitime : sécurité du service (1-f) |
| Mesurer si le service est **trouvé**, et qualifier les tentatives non payées | métadonnées de requête, IP pseudonymisée | intérêt légitime : sécurité et mesure d'usage (1-f) |

Aucun profilage, aucune décision automatisée produisant un effet juridique,
aucune revente, aucune publicité, aucune donnée transmise à des régies.

## 3. Stockage et durées

| Support | Contenu | Durée | Source |
|---------|---------|-------|--------|
| Redis (cache) | clé `addr:<sha256>` + résultat JSON (composants standardisés, latitude/longitude, score) | 30 jours (TTL) | `app/cache.py:37,98` |
| PostgreSQL `payment_transactions` | `tx_hash`, montant, devise, clé de cache | non purgé : **aucune politique de rétention dans le code** | `app/database.py:43-51`, `app/payment.py:140-145` |
| PostgreSQL `address_validations` | schéma prévu (`raw_address JSONB`, `standardized JSONB`, …) | **table jamais alimentée** : le dépôt ne contient qu'un seul `INSERT`, et il vise `payment_transactions` | `app/database.py:27-39`, vérifié le 10/10/2026 |
| `/metrics` | compteurs agrégés en mémoire (aucune adresse) | durée du processus, perdu au redémarrage | `app/routes/metrics.py` |
| Journal de découverte (JSONL) | métadonnées des requêtes refusées en `402` : horodatage, endpoint, prix, `User-Agent`, `Referer`, IP **pseudonymisée** (sha256 + sel) | **30 jours** — purge appliquée par `scripts/daily_report.sh`, pas seulement annoncée | `app/observability.py:142-158`, `app/config.py:47-65` |
| Journaux applicatifs | avertissements d'échecs non bloquants | selon le journal de journald (non défini dans le code) | `app/payment.py:148-149` |

Points de vigilance assumés :

- L'adresse **brute** n'est pas stockée en clair dans la clé de cache, mais le
  **contenu** de la valeur Redis contient les composants d'adresse issus de
  l'entrée : la donnée est donc conservée jusqu'à 30 jours.
- La colonne `address` de `payment_transactions` reçoit en réalité la **clé de
  cache** (`app/routes/validate.py:69` appelle `record_transaction(..., key)`),
  c'est-à-dire un pseudonyme, et non l'adresse lisible.
- La table `address_validations` contient une colonne `raw_address` : toute
  brique future qui l'alimenterait persisterait l'adresse brute. La
  vérification du 10/10/2026 a montré qu'aucun code ne l'écrit aujourd'hui ; ce
  document devra être mis à jour si cela change.
- Le journal de découverte n'écrit **rien sur disque** tant que
  `DISCOVERY_LOG_PATH` est vide (journald seul). Quand il est actif, l'IP est
  pseudonymisée : le pseudonyme est stable (même sel ⇒ même valeur) mais non
  réversible sans le sel. `DISCOVERY_LOG_RAW_IP=true` la met en clair : cette
  activation doit rester une enquête d'abus **datée**, jamais un réglage de
  confort, et le journal doit être purgé à la fin de l'enquête.

## 4. Destinataires, sous-traitants et transferts

| Destinataire | Ce qui lui est transmis | Où | Source |
|--------------|-------------------------|----|--------|
| SmartyStreets | adresse complète (`street`, `city`, `state`, `zipcode`) + identifiants API | États-Unis → **transfert hors UE à encadrer** (clauses contractuelles types, accord de sous-traitance) | `app/validation/verifier.py:37,99-110` |
| Nominatim / OpenStreetMap | adresse recomposée | infrastructure de la Fondation OSM, soumise à sa politique d'usage | `app/validation/geocoder.py:16-35` |
| Coinbase CDP / Base (JSON-RPC) | clés d'API et adresse du portefeuille — **aucune adresse postale** ; quand la vérification on-chain est active (D27), seul le **hash de transaction** est transmis à un nœud Base public | États-Unis | `app/payment.py:57-71`, `app/onchain.py:74-84` |
| Reverse proxy / hébergeur | adresse IP source (journaux Nginx), hors application | selon l'hébergeur retenu | `nginx.conf`, `deploy.sh:32` |

- Le code envoie l'adresse au fournisseur **même lorsque les identifiants sont
  absents** (le fournisseur répond alors 401) : la vérification n'a pas de mode
  hors-ligne.
- Le `User-Agent` Nominatim contient `Kdera597420@gmail.com`
  (`app/validation/geocoder.py:19`, réglage `CONTACT_EMAIL`) : c'est une donnée du responsable de
  traitement, pas de la personne concernée.

## 5. Droits et exercice

L'API n'associe pas l'adresse à une identité : pour retrouver ou effacer une
entrée, il faut la **clé de cache**, délivrée dans chaque réponse
(`result.cache_key`).

| Demande | Procédure | Outil |
|---------|-----------|-------|
| Accès | relever l'entrée Redis correspondant à la clé fournie | `GET addr:<clé>` |
| Effacement | supprimer l'entrée Redis (`DEL addr:<clé>`) et, sur demande justifiée, la ligne `payment_transactions` du `tx_hash` | Redis / SQL |
| Opposition | cesser d'appeler le service ; aucune donnée n'est collectée en dehors de l'appel | — |

- Délai de réponse cible : 30 jours. Aucune automatisation n'existe dans le
  code : la procédure est manuelle et doit être tenue par l'exploitant.
- Réclamation possible auprès de la CNIL (France).
- Contact : `Kdera597420@gmail.com`.

## 6. Cookies, traceurs, mineurs, évolution

- **Aucun cookie, aucun traceur, aucun pixel** : l'API renvoie du JSON et ne
  sert aucune page web. Les journaux d'accès du reverse proxy sont le seul
  traceur résiduel, et il vit hors de l'application (voir §4).
- Le service s'adresse à des **professionnels** et ne collecte pas sciemment de
  données concernant des mineurs.
- Toute évolution qui ajouterait une écriture dans `address_validations`, un
  cookie, un nouveau sous-traitant ou une nouvelle durée de conservation doit
  mettre à jour ce fichier, `DECISIONS.md` et `TEST_REPORT.md` dans le même
  changement.

