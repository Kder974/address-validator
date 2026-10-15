# Agents IA — observabilité (D67)

Ce document est la **liste blanche publiée** du service : qui est reconnu comme
agent IA, comment il est classé, où le lire, et ce qui est journalisé. Il est cité
par le code (`app/observability.py`), par les tests (`tests/test_agents_ia.py`) et
par les deux analyseurs (`scripts/analyser_agents.py`,
`scripts/analyser_visites.py`) : il décrit exactement ce que le service applique.

Ce document n'est **pas** une politique de blocage : aucun agent IA n'est refusé,
aucun `Disallow` n'est ajouté au `robots.txt`, et aucune décision de ce fichier ne
ferme une porte. La décision D67 (voir `DECISIONS.md`) tient en trois verbes :
**classer**, **journaliser**, **publier**.

## 1. Pourquoi observer au lieu de bloquer

L'audit du trafic réel (`AUDIT_TRAFIC_VPS.md` §2-3) a montré que les agents IA et
les annuaires MCP visitent déjà le service : ils lisent `/`, `/pricing`, `/guide`,
`/manifest`, `/llms.txt`, `/.well-known/mcp-server`. Un certain nombre repartent
avec un `402` sur `/validate` — c'est-à-dire qu'ils ont découvert l'offre sans
payer, ce qui est exactement ce qu'un agent qui n'a pas de portefeuille fait.

Deux erreurs symétriques sont donc écartées :

| Réaction | Ce qu'elle coûte |
|---|---|
| Bloquer les agents IA | On coupe la distribution : un agent qui lit `/pricing` est un prospect sans navigateur. C'est lui qui parlera du service à un humain. |
| Les ignorer | On croit que personne ne vient alors que des agents passent, et on ne peut pas dire si la découverte fonctionne. |

Le classement est un **fait**, pas une défense : il ne refuse rien, il ne lève
jamais (une panne d'observabilité ne doit pas devenir une panne de service), et il
coûte un parcours de chaîne par requête.

## 2. Les six classes d'appelants

`app/observability.py` — tuple `CLASSES_APPELANTS`. L'ordre est celui de la
décision : le jeton le plus long l'emporte, donc un agent IA reste un agent IA
même quand son éditeur ajoute son jeton à un `Mozilla/5.0` de navigateur.

| Classe | Ce que c'est | Exemples |
|---|---|---|
| `agent_ia` | agent d'entraînement ou d'assistance d'un éditeur d'IA | `GPTBot`, `ClaudeBot`, `PerplexityBot`, `Google-Extended` |
| `annuaire_mcp` | annuaire ou sonde MCP : il annonce le service à des clients MCP | `mcpbeat`, `rokmcp-collector`, `MCPWatch` |
| `moteur` | moteur de recherche classique : indexation, pas d'entraînement | `Googlebot`, `Bingbot`, `YandexBot` |
| `outil` | programme client : CLI, SDK, aspirateur SEO, scanner | `curl`, `python-requests`, `Nmap`, `AhrefsBot` |
| `navigateur` | vrai navigateur (jetons `AppleWebKit`, `Chrome/`, `Gecko/`…) | Chrome, Firefox, Safari, Edge |
| `inconnu` | ni jeton connu, ni jeton de navigateur (User-Agent vide compris) | — |

Le seul fait que le rapport de visites traite à part est `CLASSES_ROBOTS`
(`agent_ia`, `annuaire_mcp`, `moteur`) : sans lui, un rapport lirait « 90 % de
robots » alors que la moitié du total est un `curl` de recette. Un outil n'est
pas un agent, mais ce n'est pas un visiteur humain non plus.

## 3. Liste blanche publiée — agents IA

Table `AGENTS_IA` du code. Un jeton est cherché **dans** le User-Agent (insensible
à la casse), pas au début de la chaîne : les éditeurs le placent volontiers au
milieu d'un `Mozilla/5.0`.

| Agent | Éditeur | Domaine de vérification |
|---|---|---|
| `GPTBot` | OpenAI | openai.com |
| `OAI-SearchBot` | OpenAI | openai.com |
| `ChatGPT-User` | OpenAI | openai.com |
| `ClaudeBot` | Anthropic | anthropic.com |
| `Claude-User` | Anthropic | anthropic.com |
| `Claude-SearchBot` | Anthropic | anthropic.com |
| `anthropic-ai` | Anthropic | anthropic.com |
| `PerplexityBot` | Perplexity | perplexity.ai |
| `Perplexity-User` | Perplexity | perplexity.ai |
| `Google-Extended` | Google | google.com |
| `Applebot-Extended` | Apple | apple.com |
| `CCBot` | Common Crawl | commoncrawl.org |
| `Bytespider` | ByteDance | bytedance.com |
| `Amazonbot` | Amazon | amazon.com |
| `meta-externalagent` | Meta | meta.com |
| `FacebookBot` | Meta | meta.com |
| `cohere-ai` | Cohere | cohere.com |
| `YouBot` | You.com | you.com |
| `MistralAI-User` | Mistral AI | mistral.ai |
| `DuckAssistBot` | DuckDuckGo | duckduckgo.com |
| `AI2Bot` | Allen Institute for AI | allenai.org |
| `ImagesiftBot` | Imagesift | imagesift.com |
| `Diffbot` | Diffbot | diffbot.com |
| `Timpibot` | Timpi | timpi.io |
| `omgili` | Webz.io | webz.io |

Vingt-cinq entrées au 15/10/2026 : cette table est la source, le compte n'est
qu'une observation datée. L'endpoint `GET /metrics/agents` porte la même liste
(champ `whitelist`) : un agent qui se demande s'il est reconnu n'a pas à lire le
code source.

Un éditeur qui publie un nouvel agent ne modifie que ce fichier et `AGENTS_IA` —
voir §8.

## 4. Annonces MCP, moteurs, outils

**Annuaires et sondes MCP** (`ANNUAIRES_MCP`) : `mcpbeat` (MCPBeat),
`AgentRankProbe` (éditeur non publié), `rokmcp-collector` (non publié),
`MCPWatch` (recherche sécurité MCP), `EveryMCP-Probe` (EveryMCP),
`protogrid-probe` (Protogrid), `GolemreachTrustBot` (Golemreach). Ils sont
classés à part des agents IA parce qu'ils ne s'entraînent pas : ils **annoncent**
le service à des clients MCP, donc ils font la découverte. « non publié » est une
observation, pas un jugement : l'éditeur ne se nomme pas lui-même.

**Moteurs** (`MOTEURS`) : `Googlebot`, `Bingbot`, `MSNBot`, `YandexBot`,
`DuckDuckBot`, `Baiduspider`, `Applebot`, `Yahoo Slurp`, `PetalBot`, `Sogou`,
`Exabot`, `SeznamBot`, `Qwantify`. Les séparer évite de compter `Googlebot` comme
un agent IA : c'est de l'indexation, pas de l'entraînement.

**Outils** (`OUTILS`) : clients HTTP et SDK (`curl`, `wget`, `python-requests`,
`python-urllib`, `Python`, `aiohttp`, `httpx`, `Go-http-client`, `node`,
`undici`, `axios`, `OkHttp`, `Postman`, `Insomnia`, `PowerShell`),
supervision (`UptimeRobot`, `kube-probe`, `Prometheus`, `blackbox_exporter`),
aspirateurs SEO (`SemrushBot`, `AhrefsBot`, `MJ12Bot`, `DotBot`, `DataForSEO`,
`BLEXBot`, `SerpstatBot`, `Screaming Frog`, `Sitebulb`) et scanners (`zgrab`,
`masscan`, `Nmap`, `Nikto`, `sqlmap`, `Nuclei`, `gobuster`, `DirBuster`,
`Censys`, `InternetMeasurement`). Le nom publié sert au classement, jamais à un
refus.

**Jetons de navigateur** (`JETONS_NAVIGATEUR`) : `AppleWebKit`, `Gecko/`,
`Chrome/`, `Chromium/`, `Safari/`, `Firefox/`, `Edg/`, `Opr/`, `Trident/`.
`Mozilla/5.0` seul ne dit rien : tous les robots l'écrivent pour rester
compatibles, donc il n'est pas un jeton de navigateur.

## 5. Où cela se lit

Trois portes, trois usages — aucun des trois ne remplace les autres.

### 5.1 `GET /metrics/agents` — en direct, depuis le démarrage

Compteurs du processus, remis à zéro au redémarrage. Réponse :

| Champ | Contenu |
|---|---|
| `source` | rappelle que le chiffre vient des compteurs du processus |
| `rapport_complet` | la commande qui donne le rapport sur disque |
| `journal` | `actif`, `chemin`, `ip_en_clair` (l'état réel du journal de D30) |
| `whitelist` | la liste blanche publiée (§3), 25 entrées |
| `agents` | requêtes **par classe** — les six classes, même à zéro |
| `top_ip` | adresses pseudonymisées (32 caractères hex) des agents IA et annuaires MCP |
| `top_agents` | agents IA et annuaires MCP, avec éditeur, du plus actif au moins actif |
| `top_pages` | pages demandées par ces mêmes agents |
| `conversion` | paiements acceptés sur `/validate` / requêtes d'agent IA |

```powershell
curl.exe -s https://kder-ai.com/metrics/agents
```

### 5.2 `scripts/analyser_agents.py` — ce qui est écrit

QUI : l'exploitant. QUOI : les cinq mesures (classement, top IP, top agents,
top pages, conversion). COMMENT : agrège le journal JSONL de D30
(`DISCOVERY_LOG_PATH`) et, s'il est lisible, le journal d'accès Nginx au format
`combined` — c'est lui qui porte le User-Agent de tout le trafic. QUAND : après
une campagne de découverte, ou chaque jour avec le rapport. OÙ :
`scripts/analyser_agents.py`. FAIT QUOI EXACTEMENT : cinq sections lisibles, ou
les cinq mesures en JSON (`--json`).

```powershell
.\.venv\Scripts\python.exe scripts\analyser_agents.py --since 7d
.\.venv\Scripts\python.exe scripts\analyser_agents.py --since 7d --json
```

Codes de retour : `0` rapport produit (un rapport vide est une mesure, pas une
erreur) ; `2` une source **nommée explicitement** est illisible.

### 5.3 `scripts/analyser_visites.py` — le trafic réel, sept sections (D68)

QUI : l'exploitant. QUOI : sept sections — chiffres clés, répartition dans le
temps, top pages, top IP, codes de statut, clients, anomalies (sondes de
scanners, chemins sensibles, fuites). COMMENT : lit le journal d'accès Nginx
(`combined`) avec la **même** table de détection que §2, donc l'endpoint et le
rapport ne peuvent pas se contredire sur ce qu'est un agent. QUAND : chaque
semaine, ou juste après une campagne ; `--since 24h` pour relire un incident du
jour. OÙ : `scripts/analyser_visites.py`. FAIT QUOI EXACTEMENT : les sept
sections en clair, ou structurées (`--json`).

```powershell
.\.venv\Scripts\python.exe scripts\analyser_visites.py --since 24h
.\.venv\Scripts\python.exe scripts\analyser_visites.py --acces C:\logs\access.log --since 7d --json
```

Options : `--acces` (défaut `/var/log/nginx/access.log`), `--since` (durée `24h`,
`7d`, `30d`… ou date ISO ; défaut `30d`, la rétention du journal), `--limite`
(taille des tops), `--ip-en-clair`, `--json`. Une fuite est un chemin sensible
servi en `2xx` : un `301` renvoie ailleurs et n'expose rien, donc il n'est pas
compté comme une fuite.

## 6. Ce qui est journalisé, ce qui ne l'est jamais (D30)

Le journal est un fichier **JSONL** (une ligne = un objet JSON) : il se lit avec
`grep`/`jq`, se compte par jour, et se purge sans réécriture partielle.

Écrit uniquement pour les classes `agent_ia` et `annuaire_mcp` : un navigateur ou
un `curl` est **compté**, jamais journalisé. Le journal doit rester lisible par un
humain, pas devenir un journal de trafic.

| Champ | Contenu |
|---|---|
| `ts` | instant UTC (secondes) |
| `event`, `classe` | la classe d'appelant (`agent_ia` ou `annuaire_mcp`) |
| `agent`, `editeur` | nom publié et éditeur issus de la table §3-4 |
| `endpoint`, `method`, `status` | porte demandée, méthode, code de réponse |
| `user_agent`, `referer` | tronqués à 256 caractères |
| `ip`, `ip_pseudonymised` | pseudonyme (32 caractères hex) ou adresse en clair si le mode enquête est actif |

Ce qui n'est **jamais** dans le journal :

- **aucune adresse validée** — ni rue, ni ville, ni code postal : uniquement des
  métadonnées de requête (`PRIVACY.md`) ;
- aucune donnée de compte, aucune clé d'API, aucun corps de requête ;
- aucune visite humaine.

Autres points de mesure du même journal :

- les `402` sans preuve de paiement sont écrits par `log_unpaid_attempt` (même
  fichier, événement `unpaid_request`) : c'est ce qui rend visible le fossé entre
  « découvre » et « paie » ;
- l'adresse lue est la **dernière** entrée de `X-Forwarded-For`, celle que Nginx
  ajoute (D56) ; l'application écoute sur `127.0.0.1` (D21), donc l'en-tête n'est
  digne de foi que derrière ce proxy ;
- la rétention annoncée est appliquée : `DISCOVERY_LOG_RETENTION_DAYS` (30 jours)
  et `scripts/prune_discovery_log.py`, appelé par le rapport quotidien.

## 7. Réglages

| Variable | Défaut | Effet |
|---|---|---|
| `DISCOVERY_LOG_ENABLED` | `true` | journalise les agents IA et annuaires MCP |
| `DISCOVERY_LOG_PATH` | *(vide)* | chemin du JSONL ; vide = journalisation applicative seule (journald) |
| `DISCOVERY_LOG_RAW_IP` | `false` | `true` = adresse en clair — mesure d'enquête datée, à justifier |
| `DISCOVERY_LOG_SALT` | *(vide)* | sel dédié ; vide = dérivé de `API_SECRET_KEY` (dépannage) |
| `DISCOVERY_LOG_RETENTION_DAYS` | `30` | purge au-delà |

Le sel mérite sa ligne : c'est lui qui rend un pseudonyme stable. Deux adresses
pseudonymisées avec le même sel se reconnaissent d'un journal à l'autre (JSONL et
journal Nginx) ; après une rotation de sel, les pseudonymes déjà écrits ne se
comparent plus aux nouveaux.

## 8. Ajouter ou corriger un agent

1. Lire la page publique de l'éditeur (nom du robot, domaine de vérification) :
   un jeton se copie de la source, il ne se devine pas.
2. Ajouter la ligne dans `AGENTS_IA` (`app/observability.py`) : jeton **minuscule**
   (la recherche est insensible à la casse), nom publié, éditeur, domaine.
3. Ajouter le User-Agent **réel** dans `APPELANTS` (`tests/test_agents_ia.py`) —
   un test qui invente sa chaîne ne prouve rien du trafic réel.
4. Ajouter la ligne au tableau §3 de ce document, et à `docs/AGENTS-IA.md` seul si
   la classe n'est ni `agent_ia` ni `annuaire_mcp` (§4).
5. Relancer la porte : `.\.venv\Scripts\python.exe scripts\run_tests.py tests\test_agents_ia.py`
   (elle vérifie entre autres que la liste blanche publiée a la taille de la table).

Deux règles :

- **ne jamais renommer un jeton existant** : il est publié (`GET /metrics/agents`,
  ce document). Un renommage casse la lecture des journaux déjà écrits ;
- un agent mal classé se corrige en le **déplaçant de table**, jamais en
  l'inscrivant dans deux : la table est parcourue du jeton le plus long au plus
  court, un doublon rendrait la classe dépendante de la longueur du jeton.

## 9. Lire un rapport sans se tromper

- **Un `4xx` élevé n'est pas une panne.** L'audit du 11/10/2026 a compté plus de
  mille `4xx` : c'est un mur de scanners (`/.env`, `/.git/HEAD`,
  `vendor/phpunit/.../eval-stdin.php`). C'est la section `anomalies` du rapport de
  visites qui le dit, et c'est pour cela qu'elle existe.
- **Une conversion à zéro veut d'abord dire « aucune requête ».** `conversion`
  publie les deux nombres (`requetes`, `payees`) : 0 sur 0 n'est pas un échec.
- **Un chemin a souvent plusieurs statuts.** `/validate` répond `200` quand le
  paiement passe et `402` sinon : le rapport affiche `2xx=1 4xx=1`, un « dominant »
  seul cacherait la moitié du fait.
- **L'endpoint repart de zéro au redémarrage**, le script lit ce qui est écrit :
  pour un chiffre cumulé, c'est `scripts/analyser_agents.py` qu'il faut lire.
- **Le journal de D30 ne contient pas les visites humaines.** Pour voir tout le
  monde, c'est le journal Nginx qu'il faut lire
  (`scripts/analyser_visites.py`).

## 10. Limites dites

- **Un User-Agent se falsifie.** Le classement décrit une déclaration, pas une
  identité : le service ne fait ni résolution inverse, ni vérification ASN des
  plages d'éditeurs. Un `Googlebot` annoncé est donc classé `moteur` sans preuve,
  et cela suffit à son usage — compter, pas authentifier.
- **L'adresse n'est fiable que derrière le proxy** (D56/D21) : hors Nginx, la
  dernière entrée de `X-Forwarded-For` est écrite par le client.
- **Le rapport de visites dépend de Nginx** : sur un poste sans `access.log`, il
  n'y a rien à lire — le rapport le dit (`source.lu = false`) et sort en code 0.
- **Pas de découpage géographique ni réseau** : les tops sont par adresse et par
  porte, jamais par pays ni par opérateur.
- **Les pseudonymes ne survivent pas à une rotation de sel** (§7), et un
  pseudonyme n'est pas réversible sans le sel — il compte, il ne désigne personne.


