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
| Compte client (depuis D44) : e-mail, nom de boutique, plateforme, **empreinte bcrypt** du mot de passe, **empreinte SHA-256** de la clé API et son préfixe affichable | fournie à l'inscription | `app/accounts.py`, `app/routes/signup.py` |
| Compteurs d'usage du compte : une ligne par validation servie (clé de cache + issue) | produite par l'API en mode clé (D45) | `app/models.py`, `app/accounts.py` |
| Session de l'espace client (D62) : **empreinte** SHA-256 d'un jeton aléatoire, compte, création, échéance. Le jeton lui-même ne vit que dans le cookie du navigateur | produite à la connexion | `app/accounts.py`, `app/routes/connexion.py` |
| Lien « mot de passe oublié » (D63) : **empreinte** SHA-256 du jeton envoyé par mail, compte, création, échéance, date d'usage. Le jeton ne vit que dans le mail du client | produit à la demande du client | `app/accounts.py`, `app/routes/mot_de_passe.py` |
| Compteurs d'échecs de connexion (D62) : par IP et par **empreinte** de l'email visé, jamais l'email en clair | produits par un mot de passe faux | `app/routes/connexion.py`, `app/ratelimit.py` |
| Essai gratuit public (D46) : **aucune ligne en base** et la réponse est `no-store` ; le résultat passe par le **même cache que l'API** (clé SHA-256, résultat standardisé gardé 30 jours, 5 minutes s'il est dégradé, D88) ; une fenêtre de plafond anonyme par IP est tenue (Redis, ou la mémoire du processus) | produite par l'API | `app/routes/validate_form.py`, `app/ratelimit.py` |
| Comptes du tableau de bord d'exploitation (D74) : email et nom des seules personnes de l'exploitation listées dans `PILOTAGE_COMPTES`, **empreinte bcrypt** de leur mot de passe, empreinte SHA-256 des liens « créer ou changer mon mot de passe » (30 minutes, une seule fois) | fournis par l'exploitant (`.env`) et choisis par la personne | `app/pilotage_comptes.py`, `app/routes/dashboard.py` |
| Suivi d'audience (D67) : pour chaque visite d'une page publique, la page, le **pays** de connexion, le nom de domaine du site d'origine (lu dans `Referer`) et, pour un robot, sa famille (Googlebot, GPTBot…) ; pour chaque appel de `/validate`, `/mcp` ou de l'essai gratuit, la route, la méthode MCP, le mode de paiement, le statut, la durée, le pays de connexion, le **code pays** de l'adresse (deux lettres), cache, résultat dégradé, montant encaissé et compte. Le pays est déduit de l'IP **sur le serveur** (base DB-IP, aucun tiers appelé), puis l'IP est oubliée : ni IP, ni adresse postale, ni `User-Agent` complet, ni cookie | produit par l'API | `app/audience.py`, `app/models.py` |

L'appel **payant** (`POST /validate`) ne traite aucun identifiant de personne :
pas de nom, pas d'e-mail, pas de cookie, pas d'identifiant d'appareil. Depuis
D44, un **compte client** existe pour l'abonnement : il porte un e-mail, un nom de
boutique, une plateforme, l'empreinte bcrypt du mot de passe et l'empreinte
SHA-256 de la clé API — jamais un mot de passe ni une clé en clair, et aucune
autre donnée d'identité. L'adresse postale est une donnée à caractère personnel
lorsqu'elle se rattache à une personne (RGPD
art. 4-1) : aussi la clé de cache est-elle un **pseudonyme** (SHA-256), pas une
adresse en clair.

## 2. Finalités

| Finalité | Données mobilisées | Base légale (RGPD art. 6) |
|----------|--------------------|---------------------------|
| Valider l'adresse et estimer sa livrabilité | adresse, résultat standardisé | exécution du contrat (1-b) |
| Facturer à la requête (0,02 USDC) | `tx_hash`, montant | exécution du contrat (1-b) |
| Éviter de payer deux fois le même appel fournisseur | clé SHA-256 + résultat | intérêt légitime : maîtrise des coûts (1-f) |
| Superviser la disponibilité et les pannes | compteurs agrégés | intérêt légitime : sécurité du service (1-f) |
| Piloter le service : visites, pays, origines, appels, temps de réponse, revenu par pays (tableau de bord d'exploitation, D67) | lignes de suivi d'audience, sans IP ni adresse | intérêt légitime : mesure d'audience et pilotage (1-f) |
| Mesurer si le service est **trouvé**, et qualifier les tentatives non payées | métadonnées de requête, IP pseudonymisée | intérêt légitime : sécurité et mesure d'usage (1-f) |

Aucun profilage, aucune décision automatisée produisant un effet juridique,
aucune revente, aucune publicité, aucune donnée transmise à des régies.

## 3. Stockage et durées

| Support | Contenu | Durée | Source |
|---------|---------|-------|--------|
| Redis (cache) | clé `addr:<sha256>` + résultat JSON (composants standardisés, latitude/longitude, score) | **30 jours (TTL)** pour un résultat vérifié ; **5 minutes** pour un résultat **dégradé** (fournisseur injoignable, D47) : « je ne sais pas » n'est pas un verdict, et le garder un mois figerait une panne passagère | `app/cache.py:47-51,109-120` |
| PostgreSQL `payment_transactions` | `tx_hash`, montant, devise, clé de cache | non purgé : **aucune politique de rétention dans le code** | `app/database.py:43-51`, `app/payment.py:140-145` |
| PostgreSQL `address_validations` | schéma prévu (`raw_address JSONB`, `standardized JSONB`, …) | **table jamais alimentée** : le dépôt ne contient qu'un seul `INSERT`, et il vise `payment_transactions` | `app/database.py:27-39`, vérifié le 10/10/2026 |
| `/metrics` | compteurs agrégés en mémoire (aucune adresse) | durée du processus, perdu au redémarrage | `app/routes/metrics.py` |
| Plafond de l'essai gratuit (`/validate-form`, D46) | compteur anonyme par IP (clé `rl:validate-form:<ip>`) : ni adresse, ni résultat | fenêtre fixe de 24 h (TTL Redis, ou mémoire du processus) | `app/ratelimit.py`, `app/routes/validate_form.py` |
| PostgreSQL `customer_sessions` (D62) | empreinte SHA-256 du jeton de session, compte, création, échéance | **12 heures** au plus (`SESSION_HOURS`) ; la ligne est effacée à la déconnexion, et une ligne échue est purgée à la connexion suivante | `app/accounts.py` (`open_session`, `close_session`) |
| PostgreSQL `password_resets` (D63) | empreinte du jeton du lien, compte, création, échéance, usage | **30 minutes** de validité (`PASSWORD_RESET_MINUTES`), une seule fois ; une ligne échue est purgée à la demande suivante, les autres liens du compte sont effacés quand le mot de passe change | `app/accounts.py` (`create_password_reset`, `finish_password_reset`) |
| Demandes de mot de passe oublié (D63) | compteur par IP (`rl:reset:ip:<ip>`) et par empreinte d'email (`rl:reset:compte:<sha256>`) | **1 heure** (TTL Redis ou mémoire du processus) | `app/routes/mot_de_passe.py`, `app/ratelimit.py` |
| Échecs de connexion (D62) | compteur par IP (`rl:login:ip:<ip>`) et par empreinte d'email (`rl:login:compte:<sha256>`) : ni email, ni mot de passe | **15 minutes** (`LOGIN_LOCK_MINUTES`, TTL Redis ou mémoire du processus) | `app/routes/connexion.py`, `app/ratelimit.py` |
| Essai gratuit (D46), base de données | ni adresse, ni résultat ; depuis D67, une ligne de suivi par essai (table `api_calls`, ci-dessous) | — | `app/routes/validate_form.py`, `app/audience.py` |
| PostgreSQL `page_visits` et `api_calls` (D67) | lignes du suivi d'audience décrites au §1 : ni IP, ni adresse postale, ni cookie | **400 jours** (`AUDIENCE_RETENTION_DAYS`) — purge appliquée une fois par jour par le service | `app/audience.py` (`purger`) |
| Base des pays (D67) | fichier DB-IP Lite (CC BY 4.0) sur le serveur, lu localement : aucune donnée envoyée | remplacée chaque mois par `scripts/maj_geoip.sh` | `app/audience.py` (`pays`) |
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
| Prestataire d'envoi de mail (`SMTP_HOST`, par exemple Brevo ou Zoho, D63) | l'adresse email du compte et le lien « mot de passe oublié », **seulement** quand le client le demande ; rien d'autre | selon le prestataire retenu (contrat de sous-traitance à signer) | `app/routes/mot_de_passe.py` (`envoyer_mail`) |
| Reverse proxy / hébergeur | adresse IP source (journaux Nginx), hors application | selon l'hébergeur retenu | `nginx.conf`, `deploy.sh:32` |

- Le code envoie l'adresse au fournisseur **même lorsque les identifiants sont
  absents** (le fournisseur répond alors 401) : la vérification n'a pas de mode
  hors-ligne.
- Le `User-Agent` Nominatim contient `address-validator@kder-ai.com`
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
- Contact : `address-validator@kder-ai.com`.

## 6. Cookies, traceurs, mineurs, évolution

- **Un seul cookie côté clients, strictement nécessaire** (D62) : `__Host-av_session`, posé
  quand un client se connecte à son espace. (Depuis D69, l'exploitant a le sien,
  `__Host-av_pilotage`, posé par la page de connexion du tableau de bord : une
  échéance et une signature HMAC, rien d'autre, `HttpOnly`, `Secure`,
  `SameSite=Strict`, 12 heures au plus ; il ne concerne que l'exploitant.) Il ne contient qu'un jeton aléatoire
  (la base n'en garde que l'empreinte), il est `HttpOnly`, `Secure`,
  `SameSite=Lax`, dure 12 heures au plus et s'efface à la déconnexion. C'est un
  cookie d'authentification demandé par le client lui-même : il n'appelle pas de
  bandeau de consentement. Aucun autre cookie n'est posé, ni sur l'API ni sur les
  pages publiques : la mesure d'audience (D67) se fait côté serveur, sans cookie
  ni identifiant de visiteur, donc sans bandeau de consentement.
- **Aucun traceur, aucun pixel, aucun script tiers** : l'API
  renvoie du JSON, et les pages HTML publiques (`/pricing`, `/signup`,
  `/validate-form`) ainsi que l'espace client (`/compte`) sont **autonomes** :
  aucun script, aucune ressource externe, aucune police distante. Les journaux
  d'accès du reverse proxy sont le seul traceur résiduel, et il vit hors de
  l'application (voir §4).
- Les pages qui affichent ou viennent d'afficher une donnée client (`/compte`, la
  réponse d'un essai gratuit, celle d'une inscription) sont servies en
  `Cache-Control: no-store` et `noindex` : elles ne doivent pas survivre dans un
  proxy. Le formulaire d'essai **vide**, lui, est indexable et cachable quelques
  minutes : il ne contient aucune donnée.
- Le service s'adresse à des **professionnels** et ne collecte pas sciemment de
  données concernant des mineurs.
- Toute évolution qui ajouterait une écriture dans `address_validations`, un
  cookie, un nouveau sous-traitant ou une nouvelle durée de conservation doit
  mettre à jour ce fichier, `DECISIONS.md` et `TEST_REPORT.md` dans le même
  changement.

