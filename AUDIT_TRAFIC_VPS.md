# Audit du trafic — VPS kder-ai.com

Relevé des journaux Nginx de l'API Address Validator, exécuté **en lecture seule**
sur le serveur, à la demande de Kder, le **12/10/2026 entre 13:16 et 13:30 UTC**.

- Hôte : `87.76.197.38` (`v37`, Debian 12), nginx `1.22.1`, service
  `address-validator` `active` depuis le 11/10/2026 17:52:50 UTC, `NRestarts=0`,
  `load average` 0,03.
- Sources : `/var/log/nginx/access.log` (format `combined` par défaut, 654 ko),
  `/var/log/nginx/error.log`, `journalctl -u address-validator`, table
  `payment_transactions` (PostgreSQL), configuration Nginx active.
- Période du journal : **11/10/2026 05:54:30 → 12/10/2026 13:15:09 UTC** (première
  ligne après création du vhost, rotation quotidienne par `/etc/logrotate.d/nginx`).
  Le serveur est en `Etc/UTC` ⇒ « aujourd'hui » = **12/10/2026**.
- Volume : **4 149 requêtes** au total, **2 042 aujourd'hui** ; 546 adresses IP
  distinctes au total, **289 aujourd'hui** (dont 27 ayant demandé un chemin
  sensible).
- Aucune écriture sur le serveur, aucun `reload` : seuls `awk`, `grep`, `sed`,
  `cat`, `getent`, `journalctl`, `psql` (SELECT) ont été lancés (annexe A).

## 1. Chiffres clés

| Code HTTP | Cumul 11–12/10 | Aujourd'hui 12/10 |
|---|---|---|
| 404 | 1 958 | 1 047 |
| 200 | 1 379 | 689 |
| 301 | 187 | 76 |
| 202 | 169 | 125 |
| 429 | 149 | **0** |
| 405 | 102 | 62 |
| 502 | 92 | **0** |
| 400 | 84 | 45 |
| 402 | 19 | **0** |
| 413 | 6 | 3 |
| 422 | 3 | 0 |
| 401 | 3 | 0 |
| 307 | 3 | 0 |
| 499 | 1 | 1 |

La somme dépasse de 6 le nombre de lignes : six entrées ne sont pas des requêtes
HTTP mais un `ClientHello` TLS envoyé sur le port 80 (octets binaires journalisés
comme ligne de requête). Méthodes aujourd'hui : 1 287 `GET`, 701 `POST`, 25 `HEAD`,
7 `OPTIONS`.

**Lecture immédiate** : aujourd'hui, l'API n'a enregistré **aucun 402** (aucune
tentative de paiement), **aucun 429** (la limite de débit n'a pas servi), **aucun
502** (le service n'a pas bronché). Le trafic du jour est à **51 % du 404** — c'est
du balayage, pas de l'usage.

## 2. Qui est venu aujourd'hui (12/10)

Colonnes mesurées : total de requêtes, dont 404, dont 200/202 (un `202` est la
réponse normale à une notification JSON-RPC du serveur MCP).

| IP | Total | dont 404 | dont 200/202 | Identification | Nature |
|---|---|---|---|---|---|
| `65.21.92.231` | 225 | 0 | 225 | Hetzner, PTR `mail.forcar.org`, UA `mcpbeat/0.1` | moniteur MCP tiers |
| `54.219.220.131` | 62 | 0 | 62 | AWS `us-west-1`, UA `Python/3.11 aiohttp` | client MCP automatisé |
| `40.160.65.14` | 67 | — | 55 | OVH US, UA `node` | client MCP (SDK JS) |
| `74.101.240.155` | 44 | — | 33 | Verizon Fios NY, UA `AgentRankProbe/0.1` | annuaire MCP |
| `46.62.238.162` | 32 | — | 28 | UA `rokmcp-collector/0.2` | annuaire MCP |
| `77.42.121.60` | 10 | — | 10 | UA `MCPWatch/0.1.0` « longitudinal MCP security research » | recherche sécurité |
| `187.77.97.131` | 10 | — | 10 | — | client MCP |
| `159.195.111.209` | 10 | — | 10 | — | client MCP |
| `35.209.101.84` | 277 | 271 | 0 | Google Cloud, UA `Chrome/131` | **scanner** |
| `80.94.95.211` | 258 | 257 | 0 | sans PTR, UA `Safari/416.12` (faux, 2008) | **scanner** |
| `107.172.15.131` | 151 | 145 | — | `colocrossing.com`, UA `Chrome/129` | **scanner** |
| `187.87.144.234` | 49 | 47 | — | UA `libredtail-http` | **scanner** (exploit PHPUnit) |
| `103.216.170.129` | 49 | 46 | — | — | **scanner** |
| `45.198.224.123` | 32 | 32 | — | — | **scanner** |
| `45.138.12.6` | 36 | 24 | — | même `/24` que `.51` et `.52` | **scanner** |

Les valeurs « — » signifient « hors du top 12 de la colonne », pas « zéro ».
Aux deux extrêmes, la séparation est nette et sans mélange : `65.21.92.231` et
`54.219.220.131` ne font **que** du 200/202, `35.209.101.84` et `80.94.95.211` ne
font **que** du 404.

Deux IP de la liste ne sont pas des visiteurs :

- `87.76.197.38` = **le VPS lui-même** (305 requêtes au total : 249 × `/manifest`,
  18 × `/health`, 15 × `/validate`, 4 × `/mcp`, UA `curl/7.88.1` et
  `python-httpx/0.27.2`). Essentiellement le 11/10, jour du déploiement.
- `92.130.44.231` = `lfbn-reu-1-452-231.w92-130.abo.wanadoo.fr` — **Orange, réseau
  « reu » (La Réunion)** : votre propre poste (UA `WindowsPowerShell/5.1` et
  `curl/8.21.0`). C'est la seule IP extérieure qui a touché `/validate`.

## 3. Les agents IA ont bien utilisé l'API MCP

| Indicateur (cumul) | Valeur |
|---|---|
| `POST /mcp` | 894 |
| `GET /mcp` | 75 |
| dont `200` | 724 |
| dont `202` (notification JSON-RPC) | 169 |
| dont `404` | 11 |
| dont `413` (corps > 16 ko, refusé par Nginx) | 4 |
| dont `402` | 4 |
| dont `307` | 3 |

Sept clients différents ont obtenu des réponses `200` sur `/mcp` aujourd'hui — mais
ce sont des **robots d'annuaires et de supervision MCP**, pas des clients finaux :
`mcpbeat` (liveness check), `AgentRankProbe`, `rokmcp-collector`, `MCPWatch`
(recherche sécurité), `EveryMCP-Probe`, `protogrid-probe`, `GolemreachTrustBot`,
`Infrawatch`, `utopian-foundry-probe`, `agent-tools.cloud-crawler` (via Cloudflare),
puis des clients réels `node` (SDK JS) et `Python aiohttp`.

Activité répartie sur toute la journée depuis 00:05 UTC, par vagues de 3 à 9
requêtes par créneau : c'est du sondage automatique régulier, pas un usage humain.

Trois familles de sondes révèlent ce que le marché cherche et que l'API n'expose
pas encore : `POST /v1/message:send` (protocole **A2A**), `POST /sse` (transport
**SSE** de MCP, 3 + 3 requêtes) et `GET /.well-known/oauth-protected-resource`
(4 requêtes).

## 4. Les scanners : 27 IP, aucune fuite

Chemins demandés (cumul) : `/.env`, `/.env.prod`, `/.env.local`, `/.env.dev`,
`/.git/config`, `/.git/HEAD`, `/.svn/entries`, `/.svn/wc.db`, `/.hg/dirstate`,
`/.hg/requires`, `/.hg/store/requires`, `/wp-json/batch/v1`, `/wp-login.php`,
`/xmlrpc.php`, `/info.php`, `/phpinfo.php`, `/config.json` et surtout
`/…/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php` (CVE-2017-9841 : exécution
de code à distance sur les applications PHP mal configurées) — 140 requêtes de ce
seul chemin, toutes en `404`.

**Aucune de ces requêtes n'a obtenu autre chose qu'un `404`** : sur les chemins
sensibles, la répartition est 1 027 × `404`, 48 × `502` (fenêtre de déploiement du
11/10, voir §6), 25 × `301`, 2 × `429`, 1 × `400`. Aucun `200` ⇒ aucun `.env`,
aucune clé, aucun dépôt exposé : l'application n'a pas la route et Nginx sert des
fichiers qui n'existent pas.

Les plus insistants, avec la preuve qui fonde la proposition de blocage (§8) :

| IP | Preuve mesurée |
|---|---|
| `35.209.101.84` | 271 requêtes sur 277 en `404`, dont **255** sur des chemins sensibles (Google Cloud) |
| `80.94.95.211` | 257 requêtes sur 258 en `404`, dont **177** sur des chemins sensibles |
| `107.172.15.131` | 145 sur 151 en `404`, dont 43 sur des chemins sensibles |
| `187.87.144.234` | 140 requêtes sur `eval-stdin.php` (UA `libredtail-http`), 47 en `404` aujourd'hui |
| `147.182.252.93` | 128 requêtes en `404` sur des chemins sensibles (DigitalOcean) |
| `45.138.12.51`, `.52`, `.6` | trois IP du **même /24** : 75, 78 et 24 requêtes sur des chemins sensibles |
| `167.71.233.141`, `103.216.170.129`, `129.213.151.234` | 43, 43 et 18 requêtes sur des chemins sensibles, toutes en `404` |

`104.244.74.39` (80 requêtes sur des chemins sensibles au total, mais 8 seulement
aujourd'hui) appartient à une plage d'un réseau social connu : à surveiller, **pas
à bloquer** sur ce seul volume.

## 5. Paiement : 19 × 402, tous le 11/10, aucun tiers

| Fait | Mesure |
|---|---|
| Codes `402` au total | 19 — **0 aujourd'hui** |
| Dont `POST /validate` | 15 : 9 × le VPS (`87.76.197.38`), 6 × `92.130.44.231` (votre IP, Réunion) |
| Dont `POST /mcp` | 4 : 3 × `92.130.44.231`, 1 × le VPS |
| `POST /validate`, autres codes | 5 × `200` et 3 × `422`, **tous** depuis le VPS ou votre poste |
| `POST /validate` par un tiers | **aucun** |
| Registre (`payment_transactions`) | **2 lignes**, toutes deux `test_mode = true`, 0,02 USDC chacune (`0xe2e-tx-1`, `0x-tache1`), le 11/10 à 06:30:43 et 06:56:43 |
| `/metrics/revenue` (13:17 UTC) | `real_revenue_usdc = 0`, `real_validations_paid = 0` ; `total = 0,04` USDC (recette) ; fenêtres 24 h / 7 j / 30 j : 0 réel |

Conclusion : **personne n'a essayé de payer** et le service n'a encaissé aucun
revenu réel. Les seuls appels au point de vente venaient de l'exploitant.

### 5.1 Point ouvert (hors périmètre trafic) — clos par le code le 12/10/2026

Le journal montre **5 réponses `200` sur `/validate`** le 11/10 (06:56:43,
11:56:18, 12:00:49 ×2, 12:02:04), toutes depuis le VPS avec l'agent
`python-httpx/0.27.2` — donc des scripts de recette, jamais un tiers. Or le
registre ne contenait alors que **2 lignes**, d'`id` 1 et 5 (les `id` 2 à 4 sont
absents), avec une dernière observation à 06:56:43 : les quatre `200` de 11:56 à
12:02 n'ont laissé **aucune trace en base**.

Ce n'est pas une énigme de journal mais un trou de code, et il est refermé. À
l'époque, `record_transaction` renvoyait un booléen que **personne ne lisait**
(W2, porte D41) et la preuve à secret partagé ouvrait un `200` sans règlement
hors recette déclarée (W1, porte D27). Un `200` sans ligne était donc possible
de deux façons au moins — rejeu **idempotent** d'une même preuve (`ON CONFLICT
DO NOTHING` : la séquence d'`id` est consommée, la ligne ne l'est pas) ou échec
d'inscription **silencieux**. Le journal tranche la question de l'attribution
(agent `httpx`, donc l'exploitant) et non la mécanique ; cette dernière n'a plus
d'incidence sur le chiffre publié, puisque les deux lignes écrites étaient
marquées de recette (D40) et que `real_revenue_usdc = 0` ne dépend d'aucune des
deux hypothèses.

Le point n'est donc plus « à confirmer par un test » : il est **fermé dans le
code**, et la fermeture est mesurée.

| Régime observé en production | Le 11/10 | Depuis le 12/10/2026 |
|---|---|---|
| Échec de l'inscription au registre | `200` servi **sans ligne** | `500` et `ledger_write_failures_total` qui monte (W2, porte **D41**) |
| Preuve à secret partagé hors recette déclarée | `200` **sans règlement** | refusée : la chaîne décide (W1, porte **D27**) |
| Contrat rendu par `POST /validate` | non mesuré à la route | mesuré par `tests/test_onchain_payment_route.py` (W3, porte **D42**) |

**Suite donnée** : `scripts/decisions_check.py d41 d42` reproduit les deux
refus — une régression devient un échec de porte, plus une observation à
expliquer. Le chiffre de recette publié (0 USDC réel) reste inchangé.

## 6. Anomalies du journal : toutes élucidées, aucune attaque

| Signal | Explication vérifiée |
|---|---|
| 92 × `502` (cumul), **0 aujourd'hui** | Tous dans la fenêtre de démarrage du 11/10, 06:06–06:09 UTC (plus 1 à 07:06, 07:32 et 12:00) : Nginx servait déjà alors qu'`uvicorn` n'écoutait pas encore. 48 de ces `502` portent sur des chemins sensibles, donc sur des scanners présents au mauvais moment. Course au démarrage, ni panne ni attaque. |
| 149 × `429` (cumul), **0 aujourd'hui** | **Auto-infligés** par `limit_req` (10 req/s) : 118 × le VPS lui-même (script de déploiement en boucle sur `/manifest`), 27 × votre IP, 4 × `81.171.72.135`. Le garde-fou a fonctionné, mais c'est notre propre trafic qu'il a freiné. |
| « 157 » dans un premier comptage | Faux positif de ma propre mesure : quand la ligne de requête n'a pas de protocole, la taille du corps glisse sur le champ du code. Histogramme recalculé (§1) : ces 11 lignes sont des `404`. |
| 84 × `400` | Requêtes **malformées** : 9 lignes sans requête (`"" 400 0`), le reste venant de sondes (`101.36.97.88`, `88.214.25.123`, `66.132.195.104`…) et du binaire TLS reçu sur le port 80. Aucun impact. |
| `POST /mcp?api_key=…` (6 requêtes) | `agent-tools.cloud-crawler/0.1` (via Cloudflare) met **sa propre** clé en paramètre d'URL. Ce n'est pas un secret du projet — l'API ne fait aucune authentification par clé — mais la valeur se retrouve en clair dans `access.log` : rappel de ne jamais accepter un jeton en paramètre d'URL. |
| `POST /mcp` → `413` (4) | Corps supérieur à 16 ko, **refusé par Nginx** avant l'application : le garde-fou fonctionne. |
| 561 × `GET /` (dont 102 × `405`) | La racine n'accepte pas `GET` ⇒ `405`. Robots testant la racine, dont 9 requêtes de `zgrab` (scanner) et 96 d'un UA Edge 90 usurpé. |

## 7. Réponses aux questions posées

1. **D'où vient le trafic ?** Aujourd'hui : **1 047 réponses `404`**, dont **1 027
   sur des chemins de fuite de secrets** (51 % du volume), depuis des hébergeurs
   (Google Cloud, ColoCrossing, DigitalOcean, Oracle) ; **712 requêtes sur `/mcp`**
   (35 %), servies en `200`/`202` à des robots d'annuaires ; **76 redirections**
   HTTP → HTTPS (4 %) ; le reste : méthodes non autorisées (`405`) et requêtes
   malformées. **Aucun humain identifiable** hors l'exploitant — le seul poste
   humain du journal, c'est le vôtre (Orange, La Réunion).
2. **Quelqu'un a-t-il utilisé l'API ?** Oui côté MCP : 724 `200` + 169 `202` au
   total, par une douzaine de clients distincts et nommés — mais ce sont des
   annuaires, des sondes de disponibilité et un projet de recherche en sécurité
   MCP, **pas des clients finaux**. Côté payant (`/validate`) : **aucun tiers**,
   seulement le VPS et votre poste.
3. **Tentatives de paiement ?** 19 codes `402`, **tous le 11/10**, tous depuis le
   VPS ou votre poste ; **aucun aujourd'hui** ; aucun paiement réel (2 lignes en
   base, toutes deux marquées recette).
4. **Trafic légitime ou suspect ?** Les deux, mais **aucun abus** : la moitié est
   hostile par nature (scanners de secrets) et **inoffensive** (0 fuite, tout en
   `404`), l'autre moitié est le canal de découverte de l'API. Rien de suspect côté
   paiement, aucune tentative d'exploitation applicative, aucune indisponibilité.
5. **Recommandations** : §8.

## 8. Recommandations

**Rien n'a été appliqué** : ce paragraphe est une proposition, pour décision.

### 8.1 À faire (gain immédiat, risque nul)

1. **Exempter le VPS et la boucle locale de `limit_req`.** Aujourd'hui, le compte
   de l'exploitant est dans le même seau que les scanners : 118 des 149 `429` du
   11/10 venaient du VPS lui-même, pendant le déploiement. Une requête de
   surveillance qui prend un `429` ressemble à une panne.
2. **`server_tokens off;`** — la version de Nginx est publiée dans les en-têtes et
   dans `error.log` (`nginx/1.22.1`). C'est une aide au ciblage, gratuite à retirer.
3. **Bloquer les 9 adresses les plus insistantes** de §4 (elles cumulent 271, 257,
   145, 140, 128, 78, 75 et 43 requêtes, toutes en `404`, sans jamais un seul `200`
   utile). À faire avec `deny` dans `conf.d/`, valable pour tout le serveur.
4. **Installer `fail2ban`** avec un filtre sur les chemins de secrets. C'est la
   bonne réponse de fond : automatique, réversible, expire tout seul, et elle
   suivra les scanners quand ils changeront d'adresse (contrairement à une liste
   manuelle).

### 8.2 À considérer

- **Mesurer plutôt que bloquer les annuaires MCP** (`mcpbeat`, `AgentRankProbe`,
  `rokmcp-collector`, `MCPWatch`) : ils font ~35 % du trafic et **0 % du revenu**,
  mais ce sont eux qui vous font trouver par les agents. Les bloquer reviendrait à
  fermer le canal de distribution. Si le volume devient un coût, mettre une zone
  `limit_req` dédiée à `/mcp` (5 req/s, rafale 30) — cela exige de toucher le vhost,
  donc à faire avec précaution (Certbot le réécrit).
- **Journaliser `$uri` au lieu de `$request`** : la chaîne de requête est
  aujourd'hui écrite en clair dans `access.log` (démontré par les 6 requêtes
  `?api_key=…`). Aucun secret du projet n'y est passé, mais la règle vaut pour
  l'avenir.
- **`proxy_http_version 1.1;` + `proxy_set_header Connection "";`** dans le vhost :
  `uvicorn` reçoit aujourd'hui du HTTP/1.0 (une connexion TCP par requête). C'est
  de la performance, pas de la sécurité — et cela n'ouvre pas le transport SSE.
- **`limit_conn`** (`SECURITY.md` §6 le suggérait déjà) contre les clients qui
  ouvrent beaucoup de connexions.
- **`apt install whois`** si vous voulez un jour attribuer une adresse à son
  hébergeur depuis le serveur. Je ne l'ai pas fait ici, et je n'ai pas interrogé de
  service externe de géolocalisation : cela aurait transmis les adresses de vos
  visiteurs à un tiers sans nécessité. L'identification de §2 vient du DNS inverse
  et de l'en-tête `User-Agent`.

### 8.3 À ne pas faire

- **Ne pas bloquer par `User-Agent`** : c'est contournable en une ligne, et cela
  risquerait de bloquer un vrai client (les clients MCP honnêtes s'annoncent, mais
  pas toujours).
- **Ne pas toucher à la limite de débit elle-même** : 10 req/s n'a jamais été le
  problème ; seuls les 20 premiers octets de la liste `deny` sont utiles.
- **Ne pas bloquer la plage `104.244.74.0/21`** (80 requêtes de sondage) : trop
  large, et le volume ne le justifie pas.

### 8.4 Les règles, prêtes à copier (NON appliquées)

Ajout à la fin de `/etc/nginx/conf.d/address-validator-hardening.conf` — **en
remplaçant** la ligne existante
`limit_req_zone $binary_remote_addr zone=addr_validator:10m rate=10r/s;` (Nginx
refuse deux zones de même nom) :

```nginx
# 4. Limite de debit : ne pas freiner l'exploitant (mesure du 12/10/2026 :
#    118 des 149 x 429 du 11/10 venaient des scripts du VPS lui-meme).
geo $addr_validator_limit_key {
    default           $binary_remote_addr;
    87.76.197.38      "";
    127.0.0.1         "";
}
limit_req_zone $addr_validator_limit_key zone=addr_validator:10m rate=10r/s;

# 5. Ne plus publier la version de Nginx.
server_tokens off;

# 6. Refuser les scanners les plus insistants (preuves : AUDIT_TRAFIC_VPS.md §4).
#    Toutes ces IP n'ont produit que des 404, jamais un seul 200 utile.
deny 35.209.101.84;     #  271/277 en 404, dont 255 sur chemins sensibles
deny 80.94.95.211;      #  257/258 en 404, dont 177 sur chemins sensibles
deny 107.172.15.131;    #  145/151 en 404
deny 187.87.144.234;    #  140 requetes sur eval-stdin.php (CVE-2017-9841)
deny 147.182.252.93;    #  128 en 404 sur chemins sensibles
deny 167.71.233.141;    #   43 en 404 sur chemins sensibles
deny 103.216.170.129;   #   43 en 404 sur chemins sensibles
deny 129.213.151.234;   #   18 en 404 sur chemins sensibles
deny 45.138.12.0/24;    # trois IP du meme /24 : 75, 78 et 24 requetes
```

Application, avec sauvegarde préalable et contrôle de syntaxe :

```bash
sudo cp /etc/nginx/conf.d/address-validator-hardening.conf \
        /root/hardening.avant-deny.$(date +%Y%m%d%H%M)
# ... edition du fichier ...
sudo nginx -t && sudo systemctl reload nginx
```

Retour arrière en une commande :

```bash
sudo cp /root/hardening.avant-deny.<horodatage> \
        /etc/nginx/conf.d/address-validator-hardening.conf
sudo nginx -t && sudo systemctl reload nginx
```

`fail2ban` (à préférer à la liste `deny` comme mécanisme principal) :

```ini
# /etc/fail2ban/filter.d/nginx-secret-scan.conf
[Definition]
failregex = ^<HOST> .* "(?:GET|HEAD|POST) [^"]*(?:/\.env|/\.git|/\.svn|/\.hg|/phpinfo\.php|/info\.php|/wp-login\.php|/xmlrpc\.php|eval-stdin\.php)[^"]*" (?:404|403)
ignoreregex =

# /etc/fail2ban/jail.d/nginx-secret-scan.local
[nginx-secret-scan]
enabled  = true
filter   = nginx-secret-scan
logpath  = /var/log/nginx/access.log
findtime = 1h
maxretry = 20
bantime  = 24h
```

## 9. Méthode, limites, et ce qui n'a pas été touché

- **Rien n'a été modifié sur le serveur** : aucun fichier écrit, aucun
  `systemctl reload/restart`, aucune règle Nginx changée, aucune écriture en base
  (uniquement des `SELECT`). Les quatre scripts d'analyse ont été exécutés en flux
  (`bash -s` sur l'entrée standard) : aucun fichier déposé sur le VPS.
- **Ce document** est local au dépôt de travail, **non committé**.
- Limites assumées : `whois` n'est pas installé sur le VPS et **aucun service
  externe de géolocalisation n'a été interrogé** (pour ne pas transmettre les
  adresses des visiteurs à un tiers) — l'attribution de §2 repose sur le DNS
  inverse, l'en-tête `User-Agent` et les plages d'hébergeurs connues.
- Le journal couvre deux jours (11 et 12/10) : les commandes 1 à 3 de la demande
  portent sur le cumul, les chiffres « aujourd'hui » sont donnés séparément.
- Les journaux Nginx ne disent **pas** quels outils MCP les agents ont appelés
  (seulement `POST /mcp` → `200`). Pour le savoir, il faut instrumenter
  l'application (compteur par nom d'outil dans `/metrics`), pas Nginx.

## Annexe A — commandes exécutées (toutes en lecture seule)

```bash
ssh -i ~/.ssh/address_validator_vps root@87.76.197.38
tail -1 /var/log/nginx/access.log            # format de log
wc -l /var/log/nginx/access.log              # volume
awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -20
awk '{print $7}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -20
awk -F'"' '{print $6}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -20
awk '{for(i=1;i<=NF;i++) if ($i ~ /^[0-9][0-9][0-9]$/) {print $i; break}}' \
     /var/log/nginx/access.log | sort | uniq -c | sort -rn   # codes fiables
getent hosts <ip>                            # DNS inverse des 20 premieres IP
grep -Eai '\.env|\.git|\.svn|\.hg|phpinfo|\.php|wp-' /var/log/nginx/access.log
grep -o 'limiting requests...client: <ip>' /var/log/nginx/error.log | awk '{print $NF}'
cat /etc/nginx/sites-available/address-validator /etc/nginx/conf.d/*.conf
journalctl -u address-validator --since today --no-pager | grep -Eai 'mcp|validate'
journalctl -u address-validator --since '2026-10-11 11:50' --until '2026-10-11 12:10' \
     --no-pager | grep -a validate
sudo -u postgres psql -d address_validator -c '\d payment_transactions'
sudo -u postgres psql -d address_validator -c 'select * from payment_transactions'
curl -s http://127.0.0.1:8000/metrics/revenue
systemctl show address-validator -p ActiveEnterTimestamp -p NRestarts
```

## Annexe B — les 8 mesures demandées, telles quelles

**1. Top 20 des IP (cumul 11–12/10)**

```
305 87.76.197.38      277 35.209.101.84     258 80.94.95.211      225 65.21.92.231
188 92.130.44.231     182 45.138.12.52      151 107.172.15.131    137 40.160.65.14
128 147.182.252.93     90 45.138.12.51       80 104.244.74.39      79 45.198.224.123
 61 54.219.220.131     60 74.101.240.155     60 146.70.134.142     49 187.87.144.234
 49 167.71.233.141     49 103.216.170.129    38 129.213.151.234    36 45.138.12.6
```

**2. Top 20 des URL (cumul)**

```
974 /mcp              599 /                 376 /manifest           89 /favicon.ico
 62 /robots.txt        36 /.env              31 /health             27 /.env.dev
 26 /.env.prod         26 /.env.local        25 /validate           23 /.git/config
 20 «400»              12 /.well-known/security.txt                 10 /llms.txt
 10 /config.json        9 /info.php           9 /.git/HEAD           8 /wp-json/batch/v1
  8 /phpinfo.php
```

> La ligne `20 «400»` est un artefact de la commande : sur les requêtes
> malformées (sans protocole), le champ 7 contient le **code HTTP**, pas l'URL.
> Aucune URL nommée « 400 » n'existe.

**3. Top 20 des User-Agents (cumul)**

```
284 curl/7.88.1
281 Mozilla/5.0 (X11; Linux x86_64) … Chrome/131.0.0.0 Safari/537.36
257 Mozilla/5.0 (Macintosh; U; PPC Mac OS X; en-us) … Safari/416.12     <- usurpe, 2008
225 mcpbeat/0.1 (+https://mcpbeat.com/bot/; liveness check)
185 Mozilla/5.0 (Windows NT 10.0; Win64; x64) … Chrome/124.0.0.0 Safari/537.36
184 -                                                                  <- aucun en-tete
174 node                                                               <- client MCP (SDK JS)
151 Mozilla/5.0 (Windows NT 10.0; Win64; x64) … Chrome/129.0 Safari/537.36
141 libredtail-http                                                    <- scanner PHPUnit
128 Go Web Pentest Tool
124 Mozilla/5.0 (l9scan/2.0.…; +https://leakix.net)
 96 Mozilla/5.0 (Windows NT 10.0; Win64; x64) … Chrome/90.0.4430.85 … Edg/90.0.818.46
 87 l9explore/1.2.2
 80 Mozilla/5.0 … (compatible; Claude-User/1.0; +claude-user@anthropic.com)
 73 Mozilla/5.0 (Windows NT; Windows NT 10.0; fr-FR) WindowsPowerShell/5.1.26100.9444
 70 Mozilla/5.0 (compatible; Let's Encrypt validation server; +https://www.letsencrypt.org)
 61 Python/3.11 aiohttp/3.14.5                                         <- client MCP
 60 Mozilla/5.0 (Linux; Android 6.0; Nexus 5 …) … Chrome/130.0.0.0 Mobile Safari/537.36
 60 AgentRankProbe/0.1 (+https://spark.tail6ec125.ts.net/developers)   <- annuaire MCP
 51 curl/8.21.0
```

**4. Requêtes aujourd'hui (12/10/2026)** : `2042`
**5. `POST /mcp`** : `915` (cumul)
**6. `POST /validate`** : `23` (cumul)
**7. Codes `402`** : `19` (cumul ; **0 aujourd'hui**)
**8. `POST /mcp` + `200`** : `724` (cumul)

## Annexe C — comment relire ce rapport demain

Les mêmes commandes, appliquées au seul jour courant, donnent la tendance :
le nombre de `402` mesure l'intérêt réel pour l'API payante, le couple
(`200` sur `/mcp` **par User-Agent**) mesure qui vous suit, et le ratio
`404 / total` mesure la pression des scanners. Les trois se lisent en une ligne :

```bash
sudo grep "$(date +%d/%b/%Y)" /var/log/nginx/access.log | \
  awk '{for(i=1;i<=NF;i++) if ($i ~ /^[0-9][0-9][0-9]$/) {print $i; break}}' | \
  sort | uniq -c | sort -rn
```
