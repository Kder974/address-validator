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
2. **La preuve de paiement est un HMAC à secret partagé (D15) par défaut.** Toute
   personne qui connaît `API_SECRET_KEY` peut forger une preuve valide et obtenir
   un `200` sans payer. C'est documenté (`README.md` §7, `SECURITY.md`) et le
   chemin de mise à niveau est décrit ; en attendant, l'endpoint payant doit être
   considéré comme une démonstration. La confirmation on-chain (D27,
   `app/onchain.py`) **est désormais déployée** (11/10/2026) mais reste
   **désactivée** (`X402_ONCHAIN_VERIFY=false`) : l'activer (`.env` → `true`, puis
   `systemctl restart address-validator`) rend obligatoire un transfert USDC
   confirmé vers `CDP_WALLET_ADDRESS`, tout échec étant *fail-closed*.
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
