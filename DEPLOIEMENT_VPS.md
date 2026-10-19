# Déploiement VPS — address-validator (kder-ai.com)

Compte rendu du déploiement réel de l'API payante sur le VPS : ce qui a été
exécuté, les écarts assumés par rapport au plan initial, et les preuves
mesurées. `README.md` §2 décrit le démarrage **local** ; ce document décrit
l'installation **en production**.

- Date : **11/10/2026** — hôte `87.76.197.38` (Debian 12 « bookworm », `v37`).
- Domaine public : **https://kder-ai.com** (+ `www`).
- Journal d'exécution **de référence** : `/var/log/deploy-kder-ai.run6.log` sur
  le VPS — dernier run complet, terminé par `DEPLOIEMENT-KDER-AI: TERMINE`, tous
  les contrôles verts, y compris la simulation de renouvellement du certificat
  (essais antérieurs : `deploy-kder-ai.run1.log` … `.run5.log` ;
  `deploy-kder-ai.log` est le journal du premier essai de la première session).
  C'est le journal du script **versionné** dans le dépôt
  (`address-validator/scripts/deploy-kder-ai.sh`) : les quatre premiers essais
  ont été faits avec des copies hors dépôt.

## 1. Résultat

Le service est **en ligne, testé et sauvegardé** :

| Point | État constaté |
|---|---|
| API | `active (running)` — `uvicorn app.main:app --host 127.0.0.1 --port 8000` sous `www-data` (premier démarrage le 11/10/2026 à 06:09:29 UTC en `0.0.0.0`, corrigé le même jour) |
| Santé interne | `GET /health` → `200` `{"status":"ok","database":"ok","redis":"ok","version":"1.0.0"}` |
| Santé publique | `https://kder-ai.com/health` → `200` (testé depuis un poste extérieur) |
| Découverte | `/manifest`, `/docs`, `/openapi.json`, `/metrics` → `200` |
| Paiement | `POST /validate` sans preuve → `402` ; preuve falsifiée / périmée / malformée → `402` |
| Portefeuille annoncé | `0x0fe9afb42021cbcbed9da7473f619026fe42d339` (variable `CDP_WALLET_ADDRESS`) |
| TLS | Let's Encrypt, `CN=kder-ai.com`, SAN `kder-ai.com` + `www.kder-ai.com`, valide du 11/10/2026 au **09/01/2027**, clé ECDSA ; renouvellement par `certbot.timer` |
| Redirection | `http://kder-ai.com/health` → `301` → `https://kder-ai.com/health` |
| Sauvegardes | `address-validator-backup.timer` → prochaine exécution 12/10/2026 03:00 UTC ; **quatre** sauvegardes de contrôle produites pendant les essais (06:21:18, 06:26:03, 07:20:45, 07:29:31 UTC) |
| Recette projet | `scripts/e2e_check.py` → **22/22 vérifications OK** (exit 0), exécutée sur le serveur |
| Tests unitaires | `pytest` → **61 passés, 1 sauté, 0 échec** (exit 0) : 45 tests d'origine + 16 pour la confirmation on-chain (D27) |
| Oracle de décisions | `scripts/decisions_check.py --all` → **`DECISION OK D1` … `D27`** (exit 0) |
| Durcissement Nginx | HSTS (1 an, `includeSubDomains`), `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, `limit_req` 10 req/s par IP (rafale 20 → `429`) et corps de requête borné à 16 ko — `/etc/nginx/conf.d/address-validator-hardening.conf` |

### 1.1 Durcissements appliqués le 11/10/2026

Cinq correctifs, tous vérifiés sur le serveur et rejoués par le script de
déploiement **versionné** (étapes 0, 7 et 10) :

| # | Correctif | Preuve mesurée |
|---|-----------|----------------|
| 1 | Rotation de `API_SECRET_KEY` **sur le serveur** (64 caractères hexadécimaux, jamais transmis par un poste) et permissions : `.env` 640 `root:www-data`, `backup.sh` 700, sauvegardes 600 | service `active`, `/health` → `200` avec l'ancien secret invalidé (fenêtre 300 s) |
| 2 | Confirmation on-chain du paiement (D27), **optionnelle et désactivée** ; `app/onchain.py` + 16 tests dédiés | `pytest` → 61 passés, 1 sauté ; `decisions_check.py --all` → D1 … D27 `OK` |
| 3 | Durcissement Nginx dans `conf.d/` : HSTS, `nosniff`, `Referrer-Policy`, `limit_req` 10 req/s, corps 16 ko | `nginx -t` OK, reload OK ; 60 appels simultanés → `429` ; en-têtes visibles de l'extérieur (détail en §6) |
| 4 | `uvicorn` lié à `127.0.0.1` (`ExecStart` du service + défaut `app/config.py:18`, `.env` aligné) | `ss -ltn` : la seule écoute est `127.0.0.1:8000` ; `/health` local **et** public → `200` ; port 8000 injoignable de l'extérieur |
| 5 | Script de déploiement **versionné** dans le dépôt et contrôles d'échec ajoutés | 6 contrôles (secret par défaut ou < 32 caractères, variables D27, écoute locale, en-têtes, `429`) ; `.gitattributes` (`* text=auto eol=lf`) pour interdire les CRLF ; run complet rejoué → `DEPLOIEMENT-KDER-AI: TERMINE` |

> Alignement complémentaire du `.env` (11/10/2026, 07:32 UTC) : la variable
> `API_HOST` y valait encore `0.0.0.0` — trace de l'outil de préparation initial —
> alors que `ExecStart` impose déjà `--host 127.0.0.1`. Elle est passée à
> `127.0.0.1` (sauvegarde préalable :
> `/opt/backups/env_avant_alignement_API_HOST_20261011_0732.backup`, `600
> root:root`), service redémarré puis revérifié : `ss -ltn` → seule écoute
> `127.0.0.1:8000`, `/health` local **et** public → `200`, en-têtes HSTS /
> `nosniff` / `Referrer-Policy` présents, 60 appels simultanés sur `/manifest`
> → `42 × 200 + 18 × 429`. Un `uvicorn` lancé à la main ne peut donc plus
> exposer l'API, même si quelqu'un relit `.env`.

> Pourquoi le durcissement Nginx vit dans `conf.d/` : `certbot --nginx` réécrit
> le vhost `sites-enabled/address-validator` à chaque renouvellement, donc un
> `add_header` posé dans le vhost disparaîtrait à la première rotation de
> certificat. Un fichier de `conf.d/` est inclus dans le contexte `http` : le
> `limit_req_zone` y est légal et le fichier survit aux renouvellements.

> Piège rencontré **et corrigé** pendant ces essais : `certbot renew --dry-run
> --force-renewal` applique quand même un **délai aléatoire** (0 à 480 s) en mode
> non interactif — 463 s mesurés au 5ᵉ essai, ce qui faisait expirer le
> `timeout 300` du script et sortir la simulation en échec. L'invocation porte
> désormais `--no-random-sleep-on-renew` : au 6ᵉ essai la simulation rend
> `[OK] renouvellement automatique verifie (simulation complete reussie)` en
> moins d'une minute, et le run va jusqu'à `DEPLOIEMENT-KDER-AI: TERMINE`.

## 2. Environnement installé

| Composant | Version |
|---|---|
| Système | Debian 12 (bookworm), noyau `6.1.0-44-cloud-amd64` |
| Machine | 2 vCPU, 3,9 Go de RAM (568 Mo utilisés, 3,36 Go disponibles), disque 40 Go à 9 % |
| Python applicatif | **3.12.15** (venv `/opt/address-validator/venv`) |
| Python système | 3.11.2 (`/usr/bin/python3`, **non modifié**) |
| uvicorn | 0.32.0 |
| PostgreSQL | 15.19 (`address_validator`, accès TCP `127.0.0.1:5432` vérifié) |
| Redis | 7.0.15 |
| Nginx | 1.22.1 (reverse proxy, TLS, redirection HTTPS) |
| Certbot | 2.1.0 (`certbot.timer` actif) |

## 3. Interpréteur Python : 3.12 compilé (écart n°1)

Debian 12 ne fournit que Python 3.11, et **`python3.12` est absent de
`bookworm-backports`** sur ce miroir (vérifié par `apt-cache policy` : aucun
candidat). Le projet est développé, testé et conteneurisé en 3.12
(`Dockerfile` → `python:3.12-slim`, venv local en 3.12.10) : déployer en 3.11
aurait introduit un écart silencieux entre l'environnement testé et la
production.

CPython **3.12.15** a donc été compilé depuis les sources officielles
(`python.org`), sans PGO (`--enable-optimizations` déclencherait la suite de
tests de profilage : > 20 min sur 2 vCPU, pour aucun gain sur ce service) :

- préfixe : `/opt/python3.12`, liens `python3.12` / `pip3.12` dans `/usr/local/bin` ;
- **aucun paquet Debian modifié** : `/usr/bin/python3` reste 3.11.2 ;
- journal : `/var/log/python312-build.log` ;
- contrôle de fin consigné au journal (lignes 54–55) : `modules natifs OK ; TLS
  = OpenSSL 3.0.22` puis `HTTPS sortant OK (pypi status): 200` — l'interpréteur
  résout ses bibliothèques et sort sur le réseau ;
- preuve d'usage réel : venv créé avec ce Python, dépendances installées, suite
  `pytest` exécutée dessus (45 passés) et API servie depuis ce venv.

`deploy-kder-ai.sh` intègre désormais cette compilation en **repli automatique** :
un VPS neuf est installé sans intervention manuelle.

## 4. Étapes exécutées et preuves

`deploy-kder-ai.sh` (identifiant, `set -euo pipefail`, journalisation horodatée)
a franchi les 11 étapes + le récapitulatif, marqueur final
`DEPLOIEMENT-KDER-AI: TERMINE`.

| Étape | Contenu | Preuve |
|---|---|---|
| 0 | Contrôles préalables (root, réseau, espace disque) | OK |
| 1–2 | Mise à jour système, dépendances, Python 3.12 | OK |
| 3 | PostgreSQL, Redis, Nginx, ufw (22/80/443), unattended-upgrades | `ufw status` : `active`, `Default: deny (incoming)` |
| 4 | Base `address_validator` + rôle applicatif | connexion TCP `127.0.0.1:5432` vérifiée |
| 5 | venv 3.12 + `pip install -r requirements.txt` | uvicorn 0.32.0 dans le venv |
| 6 | `.env` (640 `root:www-data`) + schéma de base (`init_db`) | « schéma initialisé » (idempotent) |
| 7 | Reverse proxy Nginx (`kder-ai.com`, `www.kder-ai.com`) | `nginx -t` réussi, vhost rechargé |
| 8 | Certificat TLS + redirection HTTPS | « Successfully deployed … kder-ai.com and www.kder-ai.com » |
| 9 | Unité systemd `address-validator` | `enabled` + `active (running)` |
| 10 | 6 tests de bout en bout | `/health` 200, `/manifest` 200, `402`, `402`, HTTPS 200/200 |
| 11 | Sauvegardes + mises à jour automatiques | minuteur actif, 2 sauvegardes, `unattended-upgrades` actif |

Vérifications complémentaires, toutes exécutées **sur le serveur, sur le code
déployé** :

- `scripts/e2e_check.py http://127.0.0.1:8000` → **22/22 OK** : endpoints,
  `402` annonçant `0.02 USDC` et `x402`, rejet des preuves périmée / falsifiée /
  malformée, appel payant `200` en **6 ms**, `cache_key` identique au 2ᵉ appel
  avec **`cache_hit` ratio 0,75** (Redis actif), `422` sur charge incomplète.
- `scripts/decisions_check.py --all` → les 26 décisions du ledger vérifiées
  (HMAC, disjoncteur, refroidissement Redis, `/health` borné, ORM vs DDL,
  géocodage dégradé). La ligne `Échec de Cdp.configure : type object 'CdpClient'
  has no attribute 'configure'` est le comportement défensif attendu de D6.
- `pytest` (venv de test isolé + `PYTHONPATH` vers le venv de production) →
  45 passés, 1 sauté, 0 échec.

## 5. Écarts assumés par rapport au plan initial

Aucun de ces écarts n'est silencieux : chacun est commenté dans
`deploy-kder-ai.sh`, à l'endroit concerné.

**Écart n°1 — `python3.12` absent des dépôts ⇒ compilation de CPython.**
Détaillé au §3. Le script tente d'abord les dépôts (bookworm puis
bookworm-backports), et ne compile qu'en dernier recours.

**Écart n°2 — le paquet `cron` n'est pas installé ⇒ minuteur systemd.**
Le premier passage a échoué sur `crontab: command not found` : l'image Debian
minimale n'embarque pas `cron`, donc la sauvegarde n'était **pas planifiée**.
Remplacé par une paire d'unités `address-validator-backup.service` +
`.timer` (`OnCalendar=*-*-* 03:00:00`, `Persistent=true`,
`RandomizedDelaySec=600`) : aucun paquet supplémentaire, même mécanisme que le
renouvellement Certbot de cette machine, et sorties consultables via
`journalctl -u address-validator-backup`.

**Écart n°3 — `certbot renew --dry-run` peut attendre ~7 minutes ⇒
`--force-renewal` borné par `timeout`.**
Quand le certificat n'est pas encore à renouveler, certbot applique un délai
aléatoire anti-« troupeau » avant de simuler (mesuré : **411,7 s**), ce qui
gelait le script. L'option `--no-random-sleep-on-renew` **n'existe pas** en
certbot 2.1.0 (vérifié dans `certbot --help all` et `renew --help all`).
Retenu : `timeout 300 certbot renew --dry-run --force-renewal` — mesuré à
**131 s**, « all simulated renewals succeeded ». Un échec de cette simulation
n'interrompt pas le déploiement (`ko`, pas `die`) : le certificat en place
reste valide et `certbot.timer` porte le renouvellement.

**Écart n°4 — course au démarrage dans les tests internes.**
La première version enchaînait `curl http://127.0.0.1:8000/health` juste après
`systemctl start` ; uvicorn n'avait pas encore lié le port (l'import du SDK CDP
prend quelques secondes) → `curl: (7)` et arrêt du script à cause de
`set -e`. Corrigé par une **attente de disponibilité** (jusqu'à 60 s, pas de
1 s) avant les tests, et suppression du `| head` qui exposait à SIGPIPE sous
`pipefail`.

**Écart n°5 — permissions héritées d'une archive créée sous Windows.**
Le transfert par `tar` (bsdtar Windows) a produit **61 éléments modifiables par
tout le monde**, dont `/opt/address-validator/app` en `777` — une prise
d'écriture sur le code servi. Corrigé par `chmod -R go-w` puis réglages
explicites (`.env` 640, `backup.sh` 700). Il ne reste que 4 entrées en 0777 :
`venv/bin/python`, `python3`, `python3.12` et `venv/lib64`, qui sont des
**liens symboliques** — Linux force 0777 sur les liens, ce n'est pas un droit.

## 6. Exposition réseau et permissions

Aucun service n'est joignable de l'extérieur en dehors de SSH, HTTP et HTTPS.

| Port | Service | Écoute sur | Joignable d'Internet |
|---|---|---|---|
| 22 | `sshd` | `0.0.0.0` + `::` | oui (clé `address_validator_vps` uniquement) |
| 80 | `nginx` | `0.0.0.0` | oui → redirection `301` vers HTTPS |
| 443 | `nginx` | `0.0.0.0` | oui (TLS + proxy vers `127.0.0.1:8000`) |
| 8000 | `uvicorn` | `127.0.0.1` | **non** (boucle locale uniquement : plus d'écoute `0.0.0.0`) |
| 5432 | `postgres` | `127.0.0.1` + `::1` | non |
| 6379 | `redis` | `127.0.0.1` + `::1` | non |

`ufw status verbose` : `Status: active`, `Default: deny (incoming), allow
(outgoing)`, seules règles entrantes `22/tcp`, `80/tcp`, `443/tcp` (v4 et v6).
`iptables -L INPUT -n` : `Chain INPUT (policy DROP)`.

> `systemctl is-active ufw` renvoie `inactive (dead)` : c'est **normal**,
> `ufw.service` est de type *oneshot* et se termine après avoir posé les
> règles. L'état réel se lit avec `ufw status verbose` et `iptables -L`.

Permissions en place : `chmod -R go-w /opt/address-validator` (code non
modifiable par `www-data` ni par les autres comptes), `.env` en `640`
`root:www-data` (lisible par le service, invisible aux autres), `backup.sh` en
`700`, sauvegardes en `600 root:root`.

Le vhost `/etc/nginx/sites-enabled/address-validator` porte
`server_name kder-ai.com www.kder-ai.com`, `proxy_pass http://127.0.0.1:8000`
et `listen 443 ssl` avec le certificat Certbot. Il est réécrit par
`certbot --nginx` à chaque renouvellement : il ne porte donc **aucun** en-tête
de sécurité ni limitation de débit.

Le durcissement vit dans
`/etc/nginx/conf.d/address-validator-hardening.conf`, versionné à la racine du
projet sous le même nom et inclus dans le contexte `http` :

- `Strict-Transport-Security: max-age=31536000; includeSubDomains` ;
- `Referrer-Policy: no-referrer` ;
- `X-Content-Type-Options: nosniff` ;
- `limit_req_zone ... rate=10r/s` + `limit_req ... burst=20 nodelay` : au-delà
  de la rafale, `429` — 60 appels simultanés sur `/manifest` donnent
  **27 × 200 + 33 × 429** au run de référence (28 × 200 + 32 × 429 au 4ᵉ essai,
  42 × 200 + 18 × 429 lors d'un envoi manuel : le partage exact dépend de la
  synchronisation des appels, les `429` non) ;
- `client_max_body_size 16k` (une adresse postale ne fait pas 16 ko).

`nginx -t` et `nginx -s reload` passent, et les en-têtes sont visibles depuis
l'extérieur (`curl -sSI https://kder-ai.com/health`).

## 7. Exploitation courante

Toutes les commandes ci-dessous sont exécutées en `root@87.76.197.38`
(`ssh -i %USERPROFILE%\.ssh\address_validator_vps root@87.76.197.38`).

| Besoin | Commande |
|---|---|
| État du service | `systemctl status address-validator --no-pager` |
| Redémarrer | `systemctl restart address-validator` |
| Journaux applicatifs | `journalctl -u address-validator -f` |
| Journaux Nginx | `tail -f /var/log/nginx/access.log /var/log/nginx/error.log` |
| Santé locale | `curl -s http://127.0.0.1:8000/health` |
| Santé publique | `curl -sI https://kder-ai.com/health` (GET, pas HEAD : l'API répond `405` à `HEAD`) |
| Sauvegarder maintenant | `/opt/address-validator/backup.sh` |
| Voir les sauvegardes | `ls -l /opt/backups` |
| Prochaines échéances planifiées | `systemctl list-timers address-validator-backup certbot` |
| Certificat | `certbot certificates` |
| Mettre le code à jour | recopier les sources dans `/opt/address-validator` (hors `venv/`, `.env`), puis `systemctl restart address-validator` — procédure exacte, vérifiée au §11 |
| Marquer les essais de recette déjà écrits | `cd /opt/address-validator && venv/bin/python scripts/migrate_test_mode.py` (simulation) puis `--apply` (écriture) — idempotent, voir §11.2 |
| Revenu réel (chiffre publiable) | `curl -s http://127.0.0.1:8000/metrics/revenue` → `real_revenue_usdc` ; les essais de recette sont dans `test_*`, le registre entier dans `total_*` (D40) |
| Rejouer tout le déploiement | `bash /opt/address-validator/deploy-kder-ai.sh` (copie du script versionné, idempotent) |
| Durcissement Nginx | `cat /etc/nginx/conf.d/address-validator-hardening.conf` |
| Écoute de l'API | `ss -ltn` → seule la ligne `127.0.0.1:8000` doit apparaître |
| Journal du déploiement | `/var/log/deploy-kder-ai.run6.log` (dernier run complet, journal de référence) |

`backup.sh` produit, à chaque exécution, un dump PostgreSQL
(`db_<AAAAmmJJ_HHMMSS>.sql`) et une copie du `.env`
(`env_<AAAAmmJJ_HHMMSS>.backup`) dans `/opt/backups`, en `600 root:root`, avec
purge automatique au-delà de **30 jours**. Deux sauvegardes de contrôle ont été
réalisées pendant le déploiement (`06:21:18` et `06:26:03`).

## 8. Points de vigilance

À connaître avant de faire payer un client.

1. ~~**Le fournisseur d'adresses n'est pas actif.**~~ **Corrigé le 11/10/2026 :
   le fournisseur est actif et le chemin nominal est mesuré en production.** Le
   diagnostic initial (« le compte répond `402 Active subscription required` »)
   était une confusion : le `402` observé venait de **notre** API, qui refuse
   toute preuve de paiement invalide — la preuve de test
   `X-Payment-Proof: test_tx:1234567890:test_signature` a un horodatage de 2009
   et une signature bidon, donc `402 Payment Required` *avant* tout appel
   fournisseur (c'est le contrôle 10.4 du script, et il est correct). Preuves
   obtenues depuis le VPS, service en production (PID 48238) :

   | Contrôle | Commande | Résultat mesuré |
   |---|---|---|
   | Appel fournisseur direct | `GET https://us-street.api.smarty.com/street-address` (clés du `.env`) | **`200`**, `dpv_match_code: "Y"`, `rdi: "Commercial"`, `active: "Y"` |
   | `POST /validate` | preuve HMAC valide (même secret que le serveur) | **`200`**, `validated: true`, `deliverability: "residential"`, `confidence: 1.0`, `degraded: false` |
   | Preuve de test bidon | `X-Payment-Proof: test_tx:1234567890:test_signature` | **`402`** — fail-closed attendu, aucune requête fournisseur |

   Les clés du `.env` correspondent à la **paire n°1** de
   `Bureau\smarty.env` (empreintes SHA-256 identiques) ; cette paire n°2 du même
   fichier est également valide (`200`) et sert de secours. En revanche, la
   paire présente dans `AddressValidatorAPI.env` répond `401 Authentication
   required` : **ne pas l'utiliser**. Reste une nuance de vocabulaire à
   connaître : `deliverability` est dérivé du code DPV (`Y` → `residential`,
   `scorer.py:17-23`), alors que le `rdi` du fournisseur (`Commercial` sur
   l'adresse de test) n'est pas exposé dans la réponse HTTP.
2. **La preuve de paiement est un HMAC à secret partagé (D15).** Toute personne
   qui connaît `API_SECRET_KEY` peut forger une preuve *bien signée* — mais depuis
   le durcissement W1 (12/10/2026) elle n'obtient plus de `200` pour autant : la
   confirmation on-chain (D27, `app/onchain.py`) est **active par défaut** et
   exige, sur Base, un transfert USDC confirmé vers `CDP_WALLET_ADDRESS` pour au
   moins `X402_PRICE`, tout échec étant *fail-closed*. Les seuls `200` sans
   encaissement vérifié sont donc une **recette déclarée** (`TEST_MODE=true`,
   ligne marquée `test_mode` et exclue du revenu réel) ou un règlement x402 réel
   (`X-PAYMENT`). Contrôle à faire avant tout changement de version :
   `X402_ONCHAIN_VERIFY` doit valoir `true` dans `/opt/address-validator/.env` —
   sinon **toutes** les preuves à secret partagé sont refusées en `402` ; une
   recette en cours est le seul cadre où `false` est légitime, et il porte alors
   `TEST_MODE=true`.
   **État mesuré le 11/10/2026 à 17 h 55 UTC : la vérification on-chain est
   active en production**, contrairement à la phrase ci-dessus
   (`X402_ONCHAIN_VERIFY=true`, `X402_FACILITATOR_VERIFY=true`,
   `X402_REQUIRE_SETTLEMENT=true` dans `/opt/address-validator/.env`). La voie
   HMAC n'est donc plus une porte d'entrée : une preuve signée avec le secret
   partagé, sans règlement x402 confirmé, est refusée en `402`. C'est exactement
   ce que mesurent les **cinq portes restées rouges** sur le serveur (D7, D15,
   D27, D29, D34 : toutes des variantes de « la preuve HMAC historique ne sert
   plus »). Le `--all` du serveur donne `DECISION OK` sur les 35 autres portes,
   D40 comprise ; l'oracle **pré-D40**, extrait du snapshot et rejoué sur le même
   service, échoue sur *les mêmes cinq* (`diff` vide) ⇒ écart d'exploitation
   assumé, pas régression du code. À surveiller : la vente est *fail-closed*,
   elle exige le facilitateur CDP **et** un RPC Base joignables.
   Le 11/10/2026, le code exécuté a été aligné sur le dépôt
   (`app/config.py`, `app/payment.py`, `app/onchain.py`, `tests/test_onchain.py`,
   `.env.example`, `deploy.sh` — md5 identiques de part et d'autre) : **avant cet
   alignement, `X402_ONCHAIN_VERIFY=true` était un no-op silencieux** et
   `scripts/decisions_check.py --all` échouait sur `D27`. `pytest` n'est pas
   installé dans le venv du serveur (dépendances de dev) : les 61 tests tournent
   sur le poste, et l'oracle `scripts/decisions_check.py --all` tourne sur le
   serveur → `DECISION OK D1…D27`.
3. **Le secret doit être traité comme tel** : `API_SECRET_KEY` vit dans
   `/opt/address-validator/.env` (640, hors dépôt). Rotation :
   `openssl rand -hex 32` → éditer `.env` → `systemctl restart address-validator`.
   La vérification on-chain est désormais déployée (§8.2) et le secret a déjà été
   tourné le 11/10/2026 : toute rotation ultérieure suit la même procédure.
4. **Ne jamais passer `.env` en `600 root`.** Le service tourne sous
   `www-data` : `600 root:root` casserait le démarrage. Le bon réglage est
   `640 root:www-data`.
5. **Ne pas relancer `_tmp_prepare_env.py`.** Il génère un **nouveau** mot de
   passe de base, qui ne correspondrait plus au rôle PostgreSQL existant :
   l'API tomberait en `database: error`. Utiliser l'outil, c'est repartir d'un
   `.env` cohérent, pas réparer l'existant.
6. ~~**`uvicorn` écoute sur `0.0.0.0:8000`.**~~ **Corrigé le 11/10/2026.**
   `ExecStart` porte `--host 127.0.0.1` (et le défaut de `app/config.py` aussi) :
   `ss -ltn` ne montre plus aucune écoute `0.0.0.0:8000`, et le port 8000 n'est
   joignable ni de l'extérieur ni depuis un autre hôte. Le contrôle est rejoué
   par le script de déploiement (étape 10.6), qui échoue si l'écoute revient.
7. ~~**Pas de limitation de débit (README §7).**~~ **Corrigé le 11/10/2026.**
   `limit_req_zone` (10 req/s par IP, rafale 20) dans
   `nginx-hardening.conf` : 60 appels simultanés sur `/manifest` produisent des
   `429` (mesuré 27 × 200 + 33 × 429 au run 6 ; 28 × 200 + 32 × 429 au run 4),
   et le contrôle 10.8 du script le vérifie à chaque déploiement. Reste à faire si le volume augmente : `fail2ban` pour
   bannir les IP qui insistent.
8. ~~**Pas d'en-tête de sécurité.**~~ **Corrigé le 11/10/2026.** HSTS (1 an,
   `includeSubDomains`), `X-Content-Type-Options: nosniff` et
   `Referrer-Policy: no-referrer` sont posés par `nginx-hardening.conf` — donc
   hors du vhost réécrit par Certbot. Contrôlé par l'étape 10.7 du script.
9. **Le test de renouvellement du certificat est une simulation.**
   `--force-renewal --dry-run` ne consomme pas le quota Let's Encrypt ; le
   renouvellement réel est fait par `certbot.timer` (prochaine échéance
   automatique, certificat valide jusqu'au **09/01/2027**).
10. **La sauvegarde tourne à 03:00 UTC + jusqu'à 10 min d'aléa**
    (`RandomizedDelaySec=600`) et rattrape une exécution manquée
    (`Persistent=true`). Elle n'est **pas** répliquée hors du VPS : un coffre
    distant reste à prévoir.

## 9. Artefacts

Poste Windows (`C:\Users\kder_\`) :

| Fichier | Rôle |
|---|---|
| `Desktop\deploy-kder-ai.sh` | **Copie de travail** du script de déploiement (octets identiques à `address-validator\scripts\deploy-kder-ai.sh`, SHA256 vérifié) |
| `Desktop\address-validator.env` | `.env` de production (secrets) |
| `Desktop\wallet.env` | Adresse du portefeuille USDC (`CDP_WALLET_ADDRESS`) |
| `Desktop\av-body.json` | Charge utile des tests `curl` |

VPS :

| Chemin | Rôle |
|---|---|
| `/opt/address-validator/` | Code, `venv` (Python 3.12.15), `.env` |
| `/opt/address-validator/deploy-kder-ai.sh` | Copie du script, **versionné** dans le dépôt (`address-validator/scripts/deploy-kder-ai.sh`) |
| `/opt/address-validator/nginx-hardening.conf` | Durcissement Nginx, installé dans `conf.d/` par l'étape 7 |
| `/etc/nginx/conf.d/address-validator-hardening.conf` | HSTS + `nosniff` + `Referrer-Policy` + `limit_req` (voir §6) |
| `/opt/address-validator/backup.sh` | Sauvegarde dump + `.env`, purge 30 j |
| `/opt/address-validator/scripts/e2e_check.py`, `scripts/decisions_check.py` | Recette et oracle de décisions |
| `/opt/backups/` | Dumps et copies `.env` (600) |
| `/etc/systemd/system/address-validator.service`, `address-validator-backup.{service,timer}` | Service et minuteur |
| `/etc/nginx/sites-enabled/address-validator` | Reverse proxy + TLS |
| `/var/log/deploy-kder-ai*.log`, `/var/log/python312-build.log` | Journaux de déploiement et de compilation |
| `/opt/python3.12/` | Interpréteur compilé (hors paquets Debian) |

Fichiers temporaires restants, sans rôle en exploitation mais utiles au
diagnostic : `/root/deploy-kder-ai.sh` (copie de secours),
`/root/build-python312.sh`, `/root/diag*.sh`, `/root/step11.sh`,
`/root/perm.sh`, `/root/verif.sh`, `/tmp/venv-test` (venv de test jetable),
`/tmp/*.log`, `av-body.json` et `wallet.env` déposés par les tests.

## 10. Recommandations

Par ordre de valeur.

1. ~~**Souscrire l'abonnement SmartyStreets** : sans lui, le service répond en
   mode dégradé et ne vaut pas 0,02 USDC par requête.~~ **Sans objet le
   11/10/2026** : l'abonnement (essai) est actif, le chemin nominal répond
   `validated: true` / `degraded: false` (voir §8.1). Surveiller plutôt la
   consommation : un `401` (identifiants) ou un `402` (crédits) côté fournisseur
   fait repasser l'API en mode dégradé — vérifiable avec `scripts/e2e_check.py`.
2. ~~**Verser `deploy-kder-ai.sh` dans le dépôt** (par ex.
   `address-validator/deploy/`).~~ **Fait le 11/10/2026** :
   `address-validator/scripts/deploy-kder-ai.sh` — même fichier que la copie de
   `/opt` et celle du bureau (SHA256 identique, fins de ligne LF). Les copies
   `Desktop\` et `/root/` ne sont plus que des commodités.
3. ~~**Lier `uvicorn` à `127.0.0.1`** et ajouter au vhost les en-têtes de
   sécurité (`Strict-Transport-Security`, `X-Content-Type-Options: nosniff`)
   ainsi qu'un `limit_req`.~~ **Fait le 11/10/2026** — dans un fichier de
   `conf.d/` et non dans le vhost, que `certbot --nginx` réécrit (voir §6). Les
   contrôles 10.6 à 10.8 du script de déploiement le rejouent à chaque run.
4. **Supervision externe** : un minuteur systemd qui interroge
   `https://kder-ai.com/health` et alerte (Telegram) en cas d'échec complète le
   dispositif — même esprit que les agents de supervision trading du dépôt.
5. ~~**Vérification on-chain du paiement x402** (chemin décrit dans `README.md`
   §7) avant toute exposition commerciale.~~ **Fait le 11/10/2026** :
   `X402_ONCHAIN_VERIFY=true` en production (mesuré au §8.2). Reste la rotation
   de `API_SECRET_KEY` au moment de l'exposition commerciale.
6. **Sauvegarde hors site** (copie chiffrée des dumps vers un stockage distant).
7. **Inscrire le service dans un annuaire x402** : `/manifest` est déjà prêt.
8. Nettoyer les fichiers temporaires listés au §9 et ajouter `_tmp*` au
   `.gitignore` de la racine (les scripts de travail de la session en cours
   apparaissent sinon dans `git status`).

> Ces écarts ne sont **pas** consignés dans `DECISIONS.md` : la convention du
> dépôt réserve `DECISIONS.md`/`GATES.md` aux décisions du produit vérifiables
> par un oracle exécutable. Ce sont des choix d'exploitation, ils appartiennent
> à ce rapport.

## 11. Mise à jour du 11/10/2026 (17 h UTC) — D40 : la recette n'est plus du revenu

Le registre de paiement publiait **0,04 USDC de revenu fictif** : les deux essais
de recette payés par l'exploitant le 11/10/2026 (`06:30:43` et `06:56:43` UTC)
étaient comptés par `/metrics/revenue` comme des paiements clients. Le correctif
D40 (commit `b48c165`, porte D40 dans `GATES.md`) marque ces lignes `test_mode` et
sépare les trois lectures du registre. Procédure rejouable et preuves mesurées :

### 11.1 Code déployé

| Contrôle | Commande | Résultat mesuré |
|---|---|---|
| Artefact | `git archive --format=tar -o av-d40.tar address-validator-export` (arbre **identique** à `main:address-validator`, commit `b48c165`) | `sha256=f7fd755b1944f7e802900227764d0071d73bcbcbde92eebc98a0aaa385b3fcbc` |
| Transfert | `sha256sum /tmp/av-d40.tar` sur le VPS | **identique** au local |
| Snapshot de retour | `tar czf /opt/backups/code_avant_d40.tgz --exclude=./venv --exclude=./__pycache__ -C /opt/address-validator .` | 281 051 o |
| Empreinte avant/après | `find . -path ./venv -prune -o -type f -print0 \| sort -z \| xargs -0 sha256sum`, puis `diff` | **18 fichiers modifiés ou ajoutés** (15 modifiés + 3 nouveaux : `LICENSE`, `TEST_PAYMENT_REEL.md`, `scripts/migrate_test_mode.py`) ; `.env` **absent du diff** (intact) ; `requirements.txt` **inchangé** ⇒ aucun `pip install` |
| Code en place | `sha256sum app/routes/revenue.py app/database.py app/payment.py app/config.py scripts/migrate_test_mode.py scripts/decisions_check.py` | **6/6 identiques** au dépôt |
| Redémarrage | `systemctl restart address-validator` | `active`, `Schéma de base de données initialisé`, `/health` → `200` `{"status":"ok","database":"ok","redis":"ok"}` |

Retour arrière : `tar xzf /opt/backups/code_avant_d40.tgz -C /opt/address-validator
&& systemctl restart address-validator`.

### 11.2 Rattrapage des lignes déjà écrites (`migrate_test_mode.py`)

| Étape | Commande | Résultat mesuré |
|---|---|---|
| Simulation | `venv/bin/python scripts/migrate_test_mode.py` | `COLONNE test_mode ajoutee`, `CANDIDATES 2` (`0xe2e-tx-1` et `0x-tache1`, 0,02 USDC, 06:30:43 / 06:56:43), `MIGRATE DRY-RUN candidates=2 cutoff=2026-10-11 12:00:00`, « rien n'a ete ecrit » |
| Écriture | `venv/bin/python scripts/migrate_test_mode.py --apply` | `MIGRATE OK applied=2 candidates=2` puis `VERIF marquees=2 non_marquees=0` |
| Rejeu | idem | `applied=0 candidates=0`, `marquees=2` ⇒ **idempotence prouvée en production** |

Le seuil `TEST_MODE_CUTOFF_UTC` (défaut `2026-10-11T12:00:00+00:00`) date le
rattrapage : rien n'est deviné depuis le montant ni depuis l'adresse. Le `.env`
de production ne porte **pas** `TEST_MODE` ⇒ `recording_now: false` : le service
n'enregistre pas de recette et un vrai paiement ne peut pas être marqué `test_mode`
par accident.

### 11.3 Ce que publie `/metrics/revenue`

| | Avant (11/10 17:51 UTC) | Après (11/10 17:53 UTC) |
|---|---|---|
| `revenue_usdc` (alias D31) | 0,04 | **0** |
| `real_revenue_usdc` / `real_validations_paid` | (champ absent) | **0** / **0** |
| `total_revenue_usdc` / `total_validations_paid` | 0,04 / 2 | 0,04 / 2 |
| `test_revenue_usdc` / `test_validations_paid` | (champ absent) | 0,04 / 2 |

`real + test = total` (0 + 0,04 = 0,04) ✓, HTTP `200` en local **et** sur
`https://kder-ai.com/metrics/revenue` (même document, `cutoff_utc` et
`recording_now` publiés). `/dashboard` met le réel en avant, la recette dans sa
propre carte.

### 11.4 Oracle sur le serveur

| Contrôle | Commande | Résultat mesuré |
|---|---|---|
| Porte D40 | `venv/bin/python scripts/decisions_check.py D40` | `DECISION OK D40` |
| Oracle complet | `venv/bin/python scripts/decisions_check.py --all` | **35 portes `DECISION OK`** (D1…D40) ; 5 rouges : D7, D15, D27, D29, D34 — voir §8.2, durcissement on-chain assumé |
| Test discriminant | oracle **pré-D40** rejoué depuis le snapshot sur le même service | **mêmes 5 échecs**, `diff` vide ⇒ pré-existant |
| Tests unitaires | `pytest` sur le serveur | **impossible** : `No module named pytest` (dépendances de dev absentes du venv de production) — les **122 passés / 1 sauté** sont mesurés sur le poste, l'oracle tourne sur le serveur |

### 11.5 Registre MCP

`io.github.Kder974/address-validator` **version 1.0.0** est publiée :
`GET registry.modelcontextprotocol.io/v0/servers?search=address-validator` → `200`,
entrée `status: active`, `publishedAt: 2026-10-11T17:49:24Z`, `isLatest: true`,
`remotes[] = https://kder-ai.com/mcp`. Le `publish` a été fait depuis l'arbre plat
de la branche `address-validator-export` (dépôt `github.com/Kder974/address-validator`,
`server.json` servi à la racine) — donc le même contenu que celui déployé ici.

---

## 12. SMTP en production (16/10/2026) — le mail « mot de passe oublié »

La page `/compte/mot-de-passe-oublie` (D63) n'envoie rien tant que les variables
`SMTP_*` sont absentes : le code l'écrit au journal (`mot de passe oublie : SMTP_HOST
vide, aucun mail envoye`) et répond **la même chose** dans tous les cas. Le 16/10/2026
au matin, la production était exactement dans cet état : le `.env` ne portait qu'une
ligne `CONTACT_EMAIL` (mesuré le 16/10/2026 à 07:34 UTC).

### 12.1 Ce que le service lit

`app/config.py` :

| Variable | Rôle | Défaut |
|---|---|---|
| `SMTP_HOST` | serveur d'envoi — **vide = aucun mail, jamais une erreur** | `""` |
| `SMTP_PORT` | `465` = TLS implicite (`SMTP_SSL`) ; tout autre port = `SMTP` + `STARTTLS` | `587` |
| `SMTP_USER` | login du relais (chez Brevo : `…@smtp-brevo.com`) | `""` |
| `SMTP_PASSWORD` | clé SMTP (chez Brevo : `xsmtpsib-…`, **pas** `xkeysib-…`) | `""` |
| `SMTP_FROM` | expéditeur affiché — vide → `CONTACT_EMAIL` | `""` |

Aucune autre variable n'est lue : `SMTP_FROM_NAME`, présent dans le `.env.smtp` du
poste, ne sert pas côté service.

Sortie réseau : `ufw` a `Default: allow (outgoing)`. Mesuré le 16/10/2026 :
`smtp-relay.brevo.com` joignable en **587** et **465**, port **25 fermé** (normal).

### 12.2 Ce qui a été fait (16/10/2026, 07:50 UTC)

| Étape | Commande | Résultat mesuré |
|---|---|---|
| Sauvegarde | `cp -p /opt/address-validator/.env /root/.env.avant-smtp-20261016` | `-rw-r----- root www-data`, 2 295 o |
| Ajout | 5 lignes `SMTP_*` ajoutées en fin de `.env` (`SMTP_FROM_NAME` exclu) | lignes **63–67** |
| Droits | fichier modifié sur place : aucun `chmod`, aucun `chown` | resté **`-rw-r----- root www-data`** |
| Redémarrage | `systemctl restart address-validator` | `active`, PID 125849, `07:50:49 UTC`, `NRestarts=0` |
| Contrôles | `/health`, `/compte/connexion`, `/compte/mot-de-passe-oublie`, `/agents` | **`200`** en local (`127.0.0.1:8000`) **et** en public |

### 12.3 Comment le refaire (procédure)

```bash
# 0. Depuis le poste : les identifiants (fichier local, ignoré par git)
scp -i ~/.ssh/address_validator_vps address-validator/.env.smtp root@87.76.197.38:/tmp/smtp_brevo.env
ssh -i ~/.ssh/address_validator_vps root@87.76.197.38 'chmod 600 /tmp/smtp_brevo.env'

# 1. Sur le serveur : sauvegarde, extraction des cinq clés, ajout en fin de fichier
ssh -i ~/.ssh/address_validator_vps root@87.76.197.38 '
  cp -p /opt/address-validator/.env /root/.env.avant-smtp-20261016
  grep -E "^(SMTP_HOST|SMTP_PORT|SMTP_USER|SMTP_PASSWORD|SMTP_FROM)=" /tmp/smtp_brevo.env \
    | sed "s/\r$//" >> /opt/address-validator/.env
  rm -f /tmp/smtp_brevo.env
  systemctl restart address-validator
'
```

Trois règles, sans exception :

1. **Ne jamais `chmod` ni `chown` le `.env`** : `>>` sur un fichier existant ne change
   pas ses droits. Il doit rester `root:www-data 640` — c'est la cause exacte de la
   boucle de redémarrages du 16/10 au matin (`PermissionError: … '.env'`, 314 essais).
2. **Nettoyer les `\r`** (`sed "s/\r$//"`) : le `.env.smtp` du poste est en CRLF ; un
   `\r` collé à la clé SMTP fait échouer l'authentification en `535`.
3. **Supprimer le fichier temporaire** après usage : il porte la clé en clair.

### 12.4 Comment tester — trois niveaux

| Niveau | Où | Commande | Ce que ça prouve |
|---|---|---|---|
| 1 | poste | `.venv\Scripts\python.exe scripts\test_smtp_connection.py` | le `.env.smtp` du poste est complet ; STARTTLS + authentification (aucun mail) |
| 2 | serveur | `venv/bin/python /tmp/test_smtp_vps.py --fichier /tmp/smtp_brevo.env --envoi` (script du dépôt : `scripts/test_smtp_vps.py`, copié par `scp`) | Brevo accepte **la clé et l'IP du serveur** ; un vrai message part |
| 3 | serveur | appel de `app.routes.mot_de_passe.envoyer_mail(...)` dans le venv | la **chaîne applicative** (les réglages que lit la page) envoie |

Mesuré le 16/10/2026, 07:48–07:52 UTC :

- niveau 2 : `session ouverte et authentifiee (noop=250)`, `message de test accepte par
  le relais` — **aucun `525`** : Brevo n'a pas de restriction d'IP sur ce compte, ou
  l'IP `87.76.197.38` y figure déjà. Rien à changer dans *app.brevo.com → Settings →
  Security → Authorized IP addresses* (voir `docs/commercial/GUIDE-BREVO.md`, piège 10) ;
- niveau 3 : `smtp_host = smtp-relay.brevo.com`, `smtp_port = 587`,
  `smtp_user = bdfa1c001@smtp-brevo.com`, mot de passe présent (90 caractères),
  `smtp_from = address-validator@kder-ai.com`, appel terminé **sans exception** et
  **aucune plainte** au journal : l'envoi est accepté.

Contrôle final, côté humain : ouvrir la boîte Zoho et vérifier `Authentication-Results`
(`spf=pass`, `dkim=pass`, `dmarc=pass`), comme au § 8 de `GUIDE-BREVO.md`.

**Test de bout en bout par la page : impossible aujourd'hui.** La page n'envoie le lien
que si le compte existe (`customers` = **0** ligne, `password_resets` = **0** au
16/10/2026). Le POST sur `/compte/mot-de-passe-oublie` répond bien `200`, mais ne
déclenche aucun envoi. Pour l'éprouver pour de vrai : créer un compte
(`/compte/inscription`), puis demander la réinitialisation depuis ce compte.

### 12.5 Retirer (rollback)

```bash
# soit restaurer la sauvegarde d'avant la modification
ssh -i ~/.ssh/address_validator_vps root@87.76.197.38 '
  cp -p /root/.env.avant-smtp-20261016 /opt/address-validator/.env
  systemctl restart address-validator; systemctl is-active address-validator'
```

- Le redémarrage n'est pas urgent : un envoi qui échoue est **avalé** par le code
  (`except (SMTPException, OSError): logger.warning`), la page ne tombe jamais.
  Les mails cessent simplement de partir, et le journal le dit.
- `cp -p` conserve les droits de la sauvegarde (`640 root:www-data`) : vérifier
  `ls -la /opt/address-validator/.env` après restauration.
- La sauvegarde `/root/.env.avant-smtp-20261016` contient les secrets du service :
  la garder en `root` (elle l'est), et la supprimer quand elle n'est plus utile.

### 12.6 Points de vigilance

- **Aucune alerte si l'envoi casse.** Le service écrit un `warning` et continue :
  personne n'est prévenu. Une surveillance sur le journal (`mot de passe oublie :
  envoi refuse`) ou sur `/health` ne suffit pas à voir un SMTP mort — c'est le même
  angle mort que la panne du 16/10 au matin.
- **`525` = IP, `535` = clé.** Ne pas chercher la clé quand le serveur répond
  `525 5.7.1 Unauthorized IP address` : c'est l'adresse IP de la machine. Deux clés
  valides ont déjà été refusées en `525` le 13/10/2026.
- **Le `.env` n'est pas versionné** : ce qu'il contient n'est écrit nulle part
  ailleurs. Toute variable ajoutée à la main doit être notée ici et dans
  `.env.example` (qui liste déjà `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
  `SMTP_PASSWORD`, `SMTP_FROM`, lignes 114–121).
- **Une seule boîte en jeu** : `SMTP_FROM` = `address-validator@kder-ai.com` (Zoho),
  dominé par le SPF `include:brevo.com` mesuré le 16/10/2026. Un envoi depuis une
  autre adresse perdrait ses signatures — voir `docs/commercial/DNS-A-CONFIGURER.md`.

---

## 13. Mise à jour du code : un seul geste, vérifié, réversible (16/10/2026)

Le déploiement du 16/10/2026 au matin a montré la fragilité de la méthode
manuelle : le code a été recopié **en deux temps** (`app/` d'abord, le reste
ensuite) et le service a redémarré entre les deux, sur un arbre incomplet ;
quelques minutes plus tard, un `.env` illisible pour `www-data` (`600 root`)
provoquait 314 redémarrages en boucle. Deux causes distinctes, un même remède :
l'étape 12 du script `scripts/deploy-kder-ai.sh`, mode `--maj`, qui remplace le
code **en une seule commande**, contrôle ce qui doit l'être **avant** le
redémarrage, mesure `/health` **après**, et revient en arrière toute seule si le
service ne répond pas.

### 13.1 La commande : deux étapes, poste puis serveur

```bash
# 1. poste Windows - fabriquer l'archive depuis le clone vitrine (copie de travail unique)
cd C:\Users\kder_\address-validator-vitrine
git status --short          # l'arbre doit être propre, ou ne contenir que ce que vous déployez
tar -czf "$env:TEMP\av-maj.tgz" app scripts docs
scp -i $env:USERPROFILE\.ssh\address_validator_vps "$env:TEMP\av-maj.tgz" root@87.76.197.38:/tmp/

# 2. serveur - un seul geste, et un code retour exploitable
ssh -i ~/.ssh/address_validator_vps root@87.76.197.38 \
  'bash /opt/address-validator/deploy-kder-ai.sh --maj /tmp/av-maj.tgz'
```

L'archive ne contient **que** `app/`, `scripts/` et `docs/` : ni `venv/`, ni
`.env`, ni les journaux, ni les `.exe`. Le serveur garde les siens.

Deux variantes utiles : `SOURCE=/chemin/de/code bash deploy-kder-ai.sh --maj`
(le code est déjà sur le serveur, par exemple dans
`/opt/address-validator-nouveau`) et `bash deploy-kder-ai.sh -h` (rappel de
l'usage). `TAMPON` et `SAUVEGARDE` sont surchargeables de la même façon.

### 13.2 Les neuf étapes, et ce que chacune prouve

| Étape | Ce qui est fait | Ce que ça prouve |
|---|---|---|
| M1 | service actif et `/health` interrogé **avant** toute écriture | l'état de référence est sain : un échec après coup est imputable au nouveau code |
| M2 | archive extraite dans `/opt/address-validator-nouveau`, puis `app/`, `scripts/`, `docs/` exigés, `>= 100` fichiers dans `app/`, `>= 50` gabarits HTML | le transfert est complet (mesure du 16/10/2026 : 167 fichiers, 72 gabarits) |
| M3 | `cp -a app scripts docs` dans `/opt/address-validator-avant-<horodatage>`, avec la liste des dossiers dans `.dossiers` | l'état d'avant est sur le disque, **hors** du dossier servi |
| M4 | `tar -C <source> -cf - app scripts docs \| tar -C /opt/address-validator -xf -` | **une seule** commande écrit l'arbre : plus aucun instant où le site est à moitié remplacé |
| M4 bis | `chmod -R a+rX` sur les trois dossiers | `www-data` lit ce qui vient d'être copié (un tar fabriqué sous Windows ne porte pas toujours les bons droits) |
| M5 | `comm -13` compare les fichiers du serveur et ceux qui viennent d'être déployés | les restes sont **listés** dans `<sauvegarde>/fichiers-en-trop.txt`, jamais supprimés |
| M6 | droits du `.env`, absence de CR, lecture réelle par `www-data`, 5 variables SMTP | la cause exacte de la boucle de redémarrages du matin est écartée **avant** le redémarrage |
| M7 | `systemctl restart`, puis `/health` chaque seconde pendant 30 s ; base et Redis lus dans le corps | le service répond vraiment (`"status":"ok"`) et l'on sait si PostgreSQL et Redis sont joignables |
| M8 | remise du code de M3, redémarrage, nouveau test de 30 s | le retour arrière est automatique, mesuré, et son résultat est annoncé |
| M9 | codes HTTP publics de `/health`, `/manifest`, `/` et `/en/pricing`, puis synchronisation de la copie de travail du script | ce que voit un visiteur est vérifié, pas seulement la boucle locale |

La sortie du script est explicite : `MISE-A-JOUR-KDER-AI: TERMINEE` (code 0) ou
`MISE-A-JOUR-KDER-AI: ECHEC` (code 1), avec le chemin de la sauvegarde. De quoi
enchaîner un appelant (agent de supervision, tâche n8n) sans analyser le texte.

### 13.3 Le `.env` : la règle du 16/10/2026

`/opt/address-validator/.env` doit rester **`-rw-r----- root:www-data`** (640).

- Le service tourne sous `www-data` (`address-validator.service`, `User=www-data`) :
  un `.env` en `600 root` le rend illisible, `PermissionError` au démarrage,
  `Restart=always` → boucle (314 redémarrages mesurés le 16/10/2026).
- M6 **corrige** les droits s'ils ont dérivé, puis vérifie la lecture réelle
  avec `sudo -u www-data test -r` : un `chmod` seul ne prouve rien.
- Le mode `--maj` **n'écrit jamais** dans le `.env` : il ne l'ajoute pas à
  l'archive, ne l'écrase pas, n'y ajoute aucune variable. Une variable nouvelle
  s'ajoute à la main en fin de fichier (§12.3) et se note dans `.env.example`.
- Le script refuse de continuer si le `.env` porte des CR : un `\r` collé à la
  clé SMTP fait échouer l'authentification en `535` (§12.3, règle 2).

### 13.4 Retour arrière : déclencheur, portée, limites

Déclencheur unique : aucune réponse de `/health` conforme à `"status":"ok"` dans
les 30 secondes qui suivent le redémarrage. S'y ajoutent, par construction, la
copie interrompue (M4 : le retour arrière est lancé sans attendre) et un
`systemctl restart` refusé (le service ne répond alors pas : M8 suit).

Portée : `app/`, `scripts/`, `docs/` — les trois dossiers copiés, et rien
d'autre. Le `.env`, `venv/`, la base PostgreSQL, Redis, Nginx et le certificat ne
sont **jamais** touchés par une mise à jour de code : il n'y a rien à remettre en
état de ce côté. Une **suppression** de fichier est bien annulée (la sauvegarde
est une copie complète de l'arbre d'avant), mais un fichier **ajouté** par la
nouvelle version reste sur le disque après un retour arrière : c'est un des
« fichiers en trop » que M5 signale, à retirer à la main.

Retour arrière manuel, plus tard (la commande exacte est imprimée dans le
récapitulatif du script) :

```bash
cd /opt/address-validator-avant-<horodatage> \
  && tar -cf - app scripts docs | tar -C /opt/address-validator -xf - \
  && systemctl restart address-validator
```

### 13.5 Ce que la mise à jour ne fait pas

Le mode `--maj` n'installe rien et ne supprime rien : c'est un choix. Un script
de mise à jour ne doit pas pouvoir défaire PostgreSQL, Nginx ou Certbot par
accident.

| Situation | Ce qu'il faut faire |
|---|---|
| `requirements.txt` change (dépendance nouvelle, montée de version) | installation complète (`ADMIN_EMAIL=... bash deploy-kder-ai.sh`), ou `venv/bin/pip install -r requirements.txt` à la main puis `--maj` |
| variable nouvelle dans `.env` (ex. les `SMTP_*` du §12) | l'ajouter à la main en fin de fichier, **sans** `chmod` ni `chown` (§12.3), puis `systemctl restart` (ou `--maj`) |
| migration de base à appliquer | `venv/bin/python scripts/<migration>.py` **avant** `--maj` (voir §11.2 pour `migrate_test_mode.py`) |
| Nginx, certificat, pare-feu, unité systemd | modification manuelle, documentée §6 à §9 : `--maj` n'y touche pas |
| fichier supprimé du dépôt | il reste sur le serveur : il apparaît dans `fichiers-en-trop.txt` (M5), à retirer à la main |
| fichier **racine** modifié (`AGENTS.md`, `llms.txt`, `README.md`, `DECISIONS.md`, `GATES.md`…) | le mode `--maj` ne copie que `app/ scripts/ docs/` : copier ces fichiers à la main (`scp` vers `/tmp`, puis `install -o root -g root -m 664` dans `/opt/address-validator/`) — `AGENTS.md` et `llms.txt` sont lus par Nginx à cet endroit |
| `venv/` ou le service absents, disque plein | `--maj` s'arrête en M1 ou M4 et le dit : reprendre par l'installation complète |

### 13.6 Vérifier après coup

```bash
systemctl is-active address-validator
curl -s http://127.0.0.1:8000/health
ls -la /opt/address-validator/.env          # root www-data 640
ls -d /opt/address-validator-avant-*        # une sauvegarde par mise a jour
journalctl -u address-validator -n 50 --no-pager
```

Garder une ou deux sauvegardes, retirer les plus anciennes : elles contiennent le
code, jamais les secrets (le `.env` n'y est pas copié). Un dossier
`/opt/address-validator-nouveau` peut rester entre deux mises à jour : il est
écrasé par l'extraction suivante.

## 14. Mise en production de D78 (17/10/2026) — transport MCP x402 v2

Compte rendu de la mise en ligne du commit `f6458e4` (fusion `7d761e4`, décision
D78) sur `87.76.197.38`, par `deploy-kder-ai.sh --maj` (section 13).

| Élément | Mesure du 17/10/2026 |
|---|---|
| Archive déployée | `av-maj-d78.tgz`, 6 734 Ko, 238 entrées — 123 fichiers dans `app/`, 72 gabarits HTML ; `sha256 8c484738467c130b…c6a0d8e62a` |
| Source | clone `Kder974/address-validator`, `main` après fusion de la PR #32 |
| Sauvegarde | `/opt/address-validator-avant-20261017-201253` (253 fichiers) — retour arrière : `cd <sauvegarde> && tar -cf - app scripts docs \| tar -C /opt/address-validator -xf - && systemctl restart address-validator` |
| M7 (santé) | `{"status":"ok","database":"ok","redis":"ok","version":"1.0.0"}`, `0` redémarrage systemd, `.env` `root:www-data 640` lu par `www-data` |
| M9 (public) | `/health`, `/manifest`, `/`, `/en/pricing` → `200` |
| Fichiers en trop (M5) | `app/templates/accueil.html` (reste d'un ancien dessin, absent du dépôt) → retiré ; aucun autre |
| Fichiers racine mis à jour à part | `AGENTS.md`, `llms.txt`, `README.md`, `DECISIONS.md`, `GATES.md` (le mode `--maj` ne copie que `app/ scripts/ docs/`) ; hashes identiques au dépôt, `AGENTS.md` et `llms.txt` relus par Nginx depuis `/opt/address-validator/` |
| Hashes déployés = locaux | `app/routes/mcp.py` `101eac66…17f5e530`, `scripts/e2e_check.py` `fefa266a…2936d47c`, `scripts/deploy-kder-ai.sh` `9f6c2e37…55b9773a` |

**Mesure du changement, avant/après**, sur `https://kder-ai.com/mcp` (`tools/call`
sans paiement, même corps) :

| | Avant (D77, déployé le 17/10 à 18:06) | Après (D78) |
|---|---|---|
| Statut HTTP | `402` | `200` |
| Corps | erreur JSON-RPC `-32002` « Payment required » | résultat d'outil `isError: true`, `structuredContent` = `PaymentRequired` v2 |
| Offre | `data.accepts[]` v1 | `x402Version: 2`, `resource: mcp://tool/validate_address`, `eip155:8453`, `amount: 20000` |
| Cohérence | — | `content[0].text` = `structuredContent` (vérifié) |

`POST /validate` sans paiement reste un `402` x402 v1 : le transport HTTP n'a pas
changé. La découverte publique suit : `/manifest` publie
`payment.meta_key = x402/payment`, `resource_url = mcp://tool/validate_address`,
`accepts[0].amount = 20000` et `errors.isError` ; `/AGENTS.md` (4 mentions de
`x402/payment`) et `/llms.txt` (2 mentions de `params._meta`) sont à jour ; la
page `/agents` des six langues rend le nouveau texte (`isError`, `x402/payment`).

Limites de cette mise à jour, dites : le mode `--maj` ne copie **pas** les
fichiers racine — `AGENTS.md` et `llms.txt` ont dû être copiés à la main (§13.5) ;
le dossier `/opt/address-validator-nouveau` reste sur le serveur (écrasé à la
prochaine mise à jour) ; l'ancienne archive
`address-validator-backup-20261013_033928.tgz` (49 Mo) n'a pas été supprimée.

## 15. Mise en production de D79/D80 (17/10/2026) — BAN et contrat du 402

Compte rendu de la mise en ligne des commits `88d2239` (D79, D80) et `5ec03e9`
(D79 précisée) sur `87.76.197.38`, par `deploy-kder-ai.sh --maj` (section 13).

| Élément | Mesure du 17/10/2026 |
|---|---|
| Archive | `av-maj-d79d80.tgz` puis `av-maj-d79b.tgz` (6 741 Ko, 124 fichiers `app/`, 72 gabarits) ; `sha256 f9336b47…f76dd5f` puis `2025e87c…a55d81e` |
| Source | `main` après PR #34 (D79/D80) puis PR #35 (lancement du script de purge) |
| Sauvegardes | `/opt/address-validator-avant-20261017-211957` (252 fichiers, avant D79/D80), `/opt/address-validator-avant-20261017-212623` (254 fichiers, après le correctif de lancement) |
| M7 (santé) | `{"status":"ok","database":"ok","redis":"ok"}`, `0` redémarrage systemd, `.env` conforme (`root:www-data 640`, lu par `www-data`) |
| M9 (public) | `/health`, `/manifest`, `/`, `/en/pricing` → `200` ; M5 : aucun fichier en trop |
| Fichiers racine | `AGENTS.md`, `llms.txt`, `README.md`, `DECISIONS.md`, `GATES.md` copiés à la main (§13.5), hashes identiques au dépôt |

**Incident de déploiement, mesuré et corrigé** : le premier `--maj` a réussi, mais
`venv/bin/python scripts/purger_cache_ban.py` a échoué en
`ModuleNotFoundError: No module named 'app'` (le script n'ajoutait pas la racine
du dépôt à `sys.path`, contrairement à `scripts/e2e_check.py`). Corrigé par la
PR #35 (commit `5ec03e9`, un test lance réellement le script depuis la racine),
puis redéployé : le second `--maj` est passé sans incident.

**Purge du cache France/outre-mer** (obligatoire : les verdicts faux d'avant D79
vivaient 30 jours) :

    venv/bin/python scripts/purger_cache_ban.py            # simulation : 4 entrées
    venv/bin/python scripts/purger_cache_ban.py --apply    # 4 supprimées
    venv/bin/python scripts/purger_cache_ban.py            # vérification : 0

Détail des 4 entrées supprimées (toutes `validated: true`,
`deliverability: unknown`, calculées avant le correctif) : `addr:8780d1…` Lyon
75005 (l'adresse signalée « 8 rue madelaine »), `addr:5c93e5…` Paris 75005,
`addr:789192…` et `addr:d2d9f5…` Le Port 97420.

**Vérifications en direct après déploiement** :

| Contrôle | Mesure |
|---|---|
| Formulaire `/validate-form`, adresse signalée | HTTP `200`, « Adresse non reconnue », 1,52 s |
| Pipeline déployé (BAN réelle, depuis le VPS) | `8 rue madelaine Lyon 75005` → `validated: false` (0,33 s) ; `8 Rue de la Paix 75002` → `validated: true` ; `10 Rue de la République 69001` → `true` ; `1 Rue de Paris 97400 Saint-Denis` → `true` ; `8 Rue de la Paix` en 75005 → `false` |
| `402` public (D80) | `network` racine `base`, `outputSchema` objet de 1 066 octets (`result` présent), corps 1 872 octets, en-têtes 3 101 octets (< 4 Ko), `PAYMENT-REQUIRED` présent, offre v1 `maxAmountRequired=20000` |
| Découverte | `/AGENTS.md` et `/llms.txt` citent la Base Adresse Nationale et `outputSchema` ; `/manifest.verification` publié ; les six langues de `/agents` citent la BAN |
| Service | `active`, `0` redémarrage systemd, `/health` `200` |

Limites dites : le plafond d'essai gratuit (10 par IP et par jour) a été atteint
pendant les vérifications (les adresses valides ont donc été vérifiées par le
pipeline déployé sur le VPS, pas par le formulaire) ; trois sauvegardes
`/opt/address-validator-avant-*` cohabitent (201253, 211957, 212623) — en garder
une ou deux ; `/opt/address-validator-nouveau` reste sur le serveur.

## 16. Recette réelle x402 sur Base Mainnet (18/10/2026)

Première validation de bout en bout du paiement **en conditions réelles**, sur le
réseau principal Base, contre le service en production
`https://kder-ai.com/validate`. Aucun changement de code n'a été nécessaire :
c'est le `402` livré par D80 (`outputSchema` + `network` à la racine) qui a servi.

| Élément | Mesure |
|---|---|
| Transaction | `0x47370e66ca49e5a01c80b5afdde727c858b612ac1437628d7d2ef2786b28aece` |
| Explorateur | https://basescan.org/tx/0x47370e66ca49e5a01c80b5afdde727c858b612ac1437628d7d2ef2786b28aece (secours vérifié : https://base.blockscout.com/tx/0x47370e66ca49e5a01c80b5afdde727c858b612ac1437628d7d2ef2786b28aece) |
| Réseau | Base Mainnet (chainId 8453), USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| Bloc | 52 756 000, statut succès |
| Gaz | payé par le facilitateur-relais `0x64cc42b1ce598e3abcfbb64df4688521ddbf1f0a` ; l'acheteur n'avait **aucun ETH** — paiement **gasless** confirmé |
| Transfert | 0,02 USDC de l'acheteur `0xfd165b321F36cF443a0Ca50db2E7EC5539fB0D91` vers le wallet du service `0x0fE9AFb42021Cbcbed9Da7473f619026Fe42d339` |
| Soldes | wallet du service 1,04 → **1,06 USDC** ; acheteur 0,50 → 0,48 USDC |
| Durée | `402` → signature EIP-3009 → règlement on-chain → `200` en **2,76 s** |

Offre publiée dans le `402` : `network: "base"`, `maxAmountRequired: "20000"`
(0,02 USDC), asset USDC mainnet, `payTo: 0x0fE9…2d339`, domaine EIP-712
`USD Coin v2`, `outputSchema` non nul (schéma Pydantic de `ValidationResponse`).

Réponse de l'API (`HTTP 200`), telle que le client la reçoit :

```json
{
  "result": {
    "validated": true,
    "standardized": {
      "line1": "8 Rue de la Paix",
      "line2": "",
      "city": "Paris",
      "state": "IDF",
      "zip": "75002",
      "country": "FR"
    },
    "latitude": 48.8688085,
    "longitude": 2.3309554,
    "deliverability": "residential",
    "confidence": 1.0,
    "degraded": false,
    "cache_key": "b1934eb5dbe6e509bb57aecf9b6fb635291070d73c749b99842f989bc5c641a0"
  },
  "price_paid": 0.02,
  "transaction_id": "0x47370e66ca49e5a01c80b5afdde727c858b612ac1437628d7d2ef2786b28aece"
}
```

En-tête `X-PAYMENT-RESPONSE` : `{"success": true, "transaction": "0x4737…b28aece", "network": "base", "payer": "0xfd16…0D91"}`.

Comptabilité côté production, mesurée en base après le règlement :

- `payment_transactions` : `0x4737…b28aece`, 0,02 USDC, `test_mode = false` —
  **revenu réel** (18/10/2026 05:29:07 UTC), et non une recette marquée ;
- `api_calls` : `402` à 05:29:04, puis `200` mode `x402` pour 0,02 USDC à
  05:29:07.

Hygiène des secrets : le wallet acheteur et les scripts de recette restent **hors
du dépôt** (dossier local ignoré) ; `.env` est couvert par `.gitignore` (`*.env`)
et les 337 fichiers suivis ont été audités avant ce commit — aucune clé privée,
aucune clé d'API réelle (`.env.example` ne contient que des placeholders).

## 17. Mise à jour D81/D82 et inscription au catalogue Coinbase (18/10/2026)

État constaté et vérifié après les fusions #38 (référencement, D81) et #40
(motifs de refus, D82) : le code et les documents racine étaient déjà déployés
sur le VPS (déploiement du 18/10 à 09:01 UTC, fichiers identiques au dépôt,
colonne `api_calls.refus` présente). Aucune nouvelle mise à jour de code n'était
donc nécessaire ; ce compte rendu consigne la vérification et les actions
d'exploitation menées depuis.

### Vérifications (mesurées le 18/10/2026)

| Contrôle | Mesure |
|---|---|
| Code déployé == dépôt | hashes identiques pour `app/seo.py`, `app/exploitation.py`, `app/pilotage_page.py`, `app/payment.py`, `app/models.py`, `app/database.py`, `scripts/indexnow.py`, `DECISIONS.md`, `GATES.md`, `README.md` |
| Portes unlazy | D81 et D82 approuvées et **vertes : ALL MET (83 portes)** |
| Oracles | **82/82 OK** (`scripts/decisions_check.py --all`, second passage ; le premier a rendu un échec D67 transitoire, non reproduit) |
| Tests | **862 passés, 0 échec, 1 ignoré** (863 collectés, `scripts/run_tests.py`) |
| IndexNow | clé servie `200` à `/819a36fb2a9140f4f142b6e80ae19cc3.txt` ; `scripts/indexnow.py --envoyer` → **HTTP 202**, 42 pages signalées |
| Titres D81 | accueil français 57 car., accueil anglais 59, tarifs 50, guide 47, essai gratuit 60 — tous ≤ 60 |
| D82 en production | colonne `api_calls.refus` créée (`VARCHAR(20)`) ; aucun 402 depuis le déploiement, donc pas encore de motif observé en production |

### Inscription au catalogue Coinbase (Bazaar)

L'issue #37 demandait un paiement **v2** pour déclencher l'inscription (le
paiement v1 du 18/10, §16, ne porte ni `resource` ni `extensions`). Un appel
payant v2 a été exécuté le 18/10/2026 à 14:15 UTC avec le `PaymentPayload` v2
complet (`accepted`, `resource`, `extensions.bazaar`) :

| Élément | Mesure |
|---|---|
| Transaction | `0x0ea8c6f9788c90cf690d93608e20c7a1b56bbf4f77bdcb7fe949717605cd283a` |
| Explorateur | https://basescan.org/tx/0x0ea8c6f9788c90cf690d93608e20c7a1b56bbf4f77bdcb7fe949717605cd283a |
| Réseau | Base Mainnet, `eip155:8453`, USDC mainnet |
| Bloc / statut | 52 771 778, succès ; gaz payé par le facilitateur (acheteur sans ETH) |
| Réponse API | `HTTP 200`, `result.validated = true`, `price_paid 0.02`, `transaction_id` ci-dessus |
| En-tête | `PAYMENT-RESPONSE` : `success: true`, réseau `eip155:8453` |
| Registre | `payment_transactions` : 0,02 USDC, `test_mode = false` (14:15:02 UTC) |
| Soldes | wallet du service 1,06 → **1,08 USDC** ; acheteur 0,48 → 0,46 USDC |

Effet vérifié dans le catalogue : `GET /v2/x402/discovery/search?query=kder-ai.com`
rend **1 ressource** — `https://kder-ai.com/validate`,
`serviceName: Address Validator`, `payTo 0x0fE9…2d339`, `amount 20000`,
`network eip155:8453`, `extensions.bazaar` avec l'exemple d'entrée/sortie,
`lastUpdated 2026-10-18T14:15:03.004Z` ; et
`GET /v2/x402/discovery/merchant?payTo=0x0fE9…2d339` rend `total: 1`. L'issue
#37 est donc satisfaite.

Limites dites : l'erreur D67 du premier passage d'oracles n'a pas été reproduite
(deuxième passage 82/82) ; les 402 et leurs motifs restent à observer en
production ; l'import du site dans **Bing Webmaster Tools** (issue #39) reste
une action humaine (connexion au compte Google/Microsoft), l'IndexNow ne fait
qu'accélérer la découverte.

## 18. Le VPS passe à la BAN Geoplateforme (19/10/2026)

Mise en ligne du commit mergé `911667b` (PR #45, D79 précisée) : l'ancienne
adresse `api-adresse.data.gouv.fr` (Sunset annoncé au 31/01/2026) est remplacée
par la Géoplateforme de l'IGN, `data.geopf.fr/geocodage/search/`.

| Élément | Mesure |
|---|---|
| Archive | `av-maj-ban.tgz` (6 744 Ko, 124 fichiers `app/`, 72 gabarits), `sha256 65a4ca80…bb9b6` |
| Sauvegarde | `/opt/address-validator-avant-20261019-112921` (260 fichiers) |
| Déploiement | `deploy-kder-ai.sh --maj` : M1 à M9 OK, M5 « mêmes fichiers » |
| Santé | `/health` 200, base et Redis joignables, `.env` conforme, **0 redémarrage systemd** |
| Docs racine | `DECISIONS.md` (`637a3853…`) et `README.md` (`54b350c6…`) synchronisés, hashes identiques au dépôt |

Vérifications en réel après redémarrage :

- URL effectivement chargée : `https://data.geopf.fr/geocodage/search/` ;
- cas signalé « 8 rue madelaine, Lyon, 75005 » → `validated: false`, `invalid` ;
- « 8 Rue de la Paix, Paris, 75002 » → `validated: true`, `residential` ;
- « 1 Rue de Paris, Saint-Denis, 97400 » → `validated: true`, `residential` ;
- `degraded: false` partout (la BAN répond) ; hashes `ban.py`, `manifest.py`,
  `decisions_check.py` identiques à `main`.

État d'avant le déploiement, dit : la production tournait encore sur `e437c18`
(ancienne URL BAN, `DECISIONS.md`/`README.md` d'avant) — un seul commit mergé de
retard, désormais résorbé. Les PR ouvertes #41 à #44 et #46 (D83 à D87) ne sont
pas déployées : elles ne sont pas mergées, c'est voulu.

Retour arrière : `cd /opt/address-validator-avant-20261019-112921 && tar -cf - app scripts docs | tar -C /opt/address-validator -xf - && systemctl restart address-validator`.

## 19. Fusion D83→D87 et déploiement (19/10/2026)

Fusion des cinq PR empilées de Fzed (#41 à #44, #46) sur `main`, puis mise en
ligne par `deploy-kder-ai.sh --maj`. Les branches contenaient chacune les
précédentes : l'ordre de fusion #41 → #42 → #43 → #44 → #46 est sans conflit.

| Élément | Mesure |
|---|---|
| Fusion | `main` = `cb8797b` (5 merge commits, PR #41–#44 et #46) |
| Portes | D83 à D87 **PASS**, `ALL MET (88)` ; preuves automatiques enregistrées ; commit `cb8797b` |
| Tests | **898 passés, 0 échec, 1 ignoré** (899 collectés) |
| Oracles | 86/87 en passage complet ; **D67 instable** (échec d'ordonnancement, isolement et porte OK, non reproduit sur un passage précédent) — à stabiliser |
| Archive | `av-maj-d83-d87.tgz`, `sha256 ed40a69c…8858d2`, 124 fichiers `app/`, 72 gabarits |
| Sauvegarde | `/opt/address-validator-avant-20261019-135141` (260 fichiers) |
| Santé | `/health` 200, base et Redis OK, `.env` conforme, **0 redémarrage systemd** |
| Docs racine | `DECISIONS.md` `55ac2f11…`, `README.md` `73bc8ea2…`, `GATES.md` `51ffc962…` synchronisés |

Vérifications en réel après redémarrage (hashes des fichiers identiques à `main`) :

- **D87** : `/` répond 200 avec la nouvelle vitrine (CSS animé + `prefers-reduced-motion`),
  champ unique `address`, prix par adresse affichés ;
- **D86** : `POST /validate-form` avec « 8 rue de la paix 75002 paris » → 200,
  « Adresse reconnue », « Rue de la Paix » corrigée ;
- **D84** : BAN « 8 rue de la paix / paris » → `8 Rue de la Paix / Paris` ;
  Smarty « 1600 amphitheatre pkwy » → `1600 Amphitheatre Parkway / Mountain View` ;
- **D85** : Apple HQ (1 Infinite Loop, Cupertino) → `deliverability: commercial`,
  confiance 1.0 (RDI de SmartyStreets, plus le DPV) ;
- **D83** : `/compare` affiche **19** prix convertis avec « ≈ » et le prix dollars
  entre crochets ; `/en/compare` garde les prix dollars ;
- **D79/D80/D82** inchangés (BAN Geoplateforme, 402, motifs de refus).

Retour arrière : `cd /opt/address-validator-avant-20261019-135141 && tar -cf - app scripts docs | tar -C /opt/address-validator -xf - && systemctl restart address-validator`.
