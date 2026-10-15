# Optimisation SEO / GEO — `kder-ai.com`

Ce document applique le cadre **GEO-Optimizer** au service Address Validator : ce
qui est en place, ce qui manque, dans quel ordre le faire, et comment le mesurer.
Il ne contient **aucun score inventé** : chaque constat vient d'un fichier du
dépôt ou d'une mesure datée.

| Question | Réponse |
|----------|---------|
| **Qui** | l'exploitant (Kder) |
| **Quoi** | rendre les pages publiques citables par les moteurs de réponse IA (ChatGPT Search, Perplexity, Claude, Google AI Overviews) |
| **Comment** | robots.txt + sitemap + JSON-LD + contenu citable, puis mesure du trafic d'agents |
| **Quand** | avant la prochaine campagne de découverte ; à revoir une fois par trimestre |
| **Où** | pages publiques servies par `app/routes/` et fichiers de découverte servis par Nginx |
| **Fait quoi exactement** | ferme les manques mesurés (§ 3) dans l'ordre de la méthode, et laisse une mesure derrière chaque manque fermé |

Sources de méthode (aucune n'est une opinion) :

- **GEO-Optimizer** (Auriti-Labs, `github.com/Auriti-Labs/geo-optimizer-skill`) :
  audit 0–100 en 9 catégories, moteur de citabilité de 47 contrôles, 10 contrôles
  informatifs (WebMCP, injection de prompt, pile de confiance, découpage RAG…) ;
- **Princeton KDD 2024** (GEO-bench, 10 000 requêtes) : les 9 méthodes de contenu
  classées par effet mesuré (§ 2) ;
- **llmstxt.org** : la convention `/llms.txt`.

Les deux premiers ne sont pas installés dans ce dépôt (voir § 5 : le skill
d'amont n'est pas installable par `npx skills`, il n'expose pas de `SKILL.md` au
format attendu). Ce document applique donc **la méthode**, pas l'outil.

---

## 0. Terminologie, pour ne pas se raconter d'histoires

- **SEO** : être trouvé par un moteur de recherche classique (Google, Bing).
- **GEO / AEO** : être **cité** par un moteur de réponse (ChatGPT, Perplexity,
  Claude, AI Overviews). Le trafic ne passe plus par un clic : il passe par une
  phrase générée.
- **Ce qu'on mesure ici, c'est le crawl, pas la citation.** Le service compte les
  agents IA qui **viennent** (`GET /metrics/agents`,
  `scripts/analyser_agents.py`, `scripts/analyser_visites.py`) : il ne sait pas si
  ChatGPT nous a cités. Confondre les deux ferait croire qu'un audit est un
  résultat. La citation se constate ailleurs (questions posées aux moteurs, à la
  main, une fois par trimestre).

---

## 1. État mesuré du site (15/10/2026)

### 1.1 Ce qui est en place

| Élément | Où | État |
|---|---|---|
| `/llms.txt` | `llms.txt`, servi par `nginx-discovery.conf` (`text/markdown`) | ✅ carte machine-lisible du service |
| `/AGENTS.md` | `AGENTS.md`, servi par Nginx | ✅ mode d'emploi des agents autonomes |
| `/.well-known/security.txt` | RFC 9116, servi par Nginx | ✅ canal de signalement |
| `/manifest`, `/openapi.json`, `/docs` | `app/routes/manifest.py` | ✅ manifeste, contrat, documentation |
| `/mcp` + `/.well-known/mcp-server` | `app/routes/mcp.py` (D37) | ✅ découverte MCP gratuite, outil payant |
| Six langues | `app/i18n.py`, gabarits par langue (D57–D61) | ✅ vitrine, guide, essai, tarifs, inscription, espace client |
| `hreflang` relatif | gabarits vitrine et guide (D57/D58) | ✅ chaque page annonce ses variantes |
| `<meta name="robots">` par page | `signup.py`, `validate_form.py`, `dashboard.py` | ✅ `index, follow` en succès, `noindex` en erreur et sur le tableau de bord |
| Trafic des agents IA mesuré | D67 (`GET /metrics/agents`) | ✅ on sait qui vient, par quelle porte |
| Trafic réel lu dans Nginx | D68 (`scripts/analyser_visites.py`) | ✅ sept sections, dont les scans de `/.env`, `/.git/HEAD` |

### 1.2 Ce qui manque (mesuré, pas supposé)

| Manque | Constat | Pourquoi c'est un manque GEO |
|---|---|---|
| **`/robots.txt`** | aucun `location` dans `nginx-discovery.conf`, aucune route dans `app/routes/` : le chemin répond `404` | c'est **la** porte d'accès des robots : sans fichier, un crawler ne sait pas ce qui est permis et les agents ne trouvent pas les pages de découverte |
| **`/sitemap.xml`** | aucun fichier, aucune route (recherche sur tout le dépôt) | les moteurs de réponse découvrent les URL par le plan de site ; sans lui, la découverte repose sur la seule page d'accueil |
| **JSON-LD** | aucun `application/ld+json`, aucun `schema.org` dans `app/templates/` ni dans `app/routes/` | un moteur de réponse ne devine pas qu'une page est un `WebApplication` avec une offre à 0,02 USDC : le balisage le lui dit |
| **Contenu « citable »** | les pages publiques expliquent le service, mais citent peu de **sources externes** et n'encadrent pas les **chiffres avec leur date** | la méthode Princeton classe « citer des sources » (+30–115 %) et « ajouter des statistiques » (+40 %) en tête des effets mesurés |
| **Skill GEO-Optimizer** | `npx skills add Auriti-Labs/geo-optimizer-skill` refuse : le `SKILL.md` d'amont n'a pas de frontmatter `name`/`description` | l'outil d'audit n'est pas là pour rejouer la mesure automatiquement (§ 5) |

**Ce qui n'est pas un manque** : les métadonnées de sécurité, les six langues, la
découverte MCP, `llms.txt` et `AGENTS.md`. Le socle de découverte est en place —
c'est précisément pour cela que des agents IA visitent déjà le service
(`AUDIT_TRAFIC_VPS.md` § 3, D67).

### 1.3 Avertissement daté : le dépôt publié est en avance sur cet arbre

Mesuré le 15/10/2026, et c'est le point le plus important de ce document :

- `origin/main` (https://github.com/Kder974/address-validator, tête `38df78e`
  « Merge pull request #21 from Kder974/seo-technique ») porte des décisions
  **D62 à D66** que cet arbre de travail **ne contient pas** — dont **D65**
  (« référencement technique » : `robots.txt`, `sitemap.xml` avec `hreflang`,
  JSON-LD `schema.org`, canonique, Open Graph) et **D66** (pages `/agents` et
  `/compare` dans les six langues) ;
- les deux historiques n'ont **pas d'ancêtre commun** (`git merge-base main
  origin/main` ne rend rien ; 57 commits côté publié) ;
- le **site public ne sert pas** ce travail : `/robots.txt`, `/sitemap.xml`,
  `/agents` et `/compare` répondent `404` (mesuré à l'instant).

Conséquence pratique : **avant d'écrire un `robots.txt` ou un `sitemap.xml` ici,
reprendre D65 du dépôt publié** — le travail existe, il n'est simplement pas dans
cette copie. C'est aussi la raison pour laquelle les décisions de cette session
portent **D67** (agents IA observables) et **D68** (rapport de visites) : D62 à
D66 sont déjà pris dans l'historique publié, et un identifiant ne se réutilise
jamais.

---

## 2. La méthode, dans l'ordre où elle a été mesurée

### 2.1 Les trois niveaux de robots (GEO-Optimizer, étape 2)

Un `User-Agent` d'IA ne se comporte pas comme un autre, et les éditeurs les
traitent séparément :

| Niveau | Robots | Ce qu'ils font | Décision ici |
|---|---|---|---|
| recherche | `OAI-SearchBot`, `PerplexityBot`, `Claude-SearchBot`, `Googlebot` | indexent pour répondre à une question d'utilisateur | **autorisés** : ce sont eux qui rendent le service citable |
| entraînement | `GPTBot`, `ClaudeBot`, `Google-Extended`, `CCBot`, `Bytespider`… | constituent des corpus d'entraînement | **autorisés** (D67 : on n'écarte pas un agent, on l'observe) |
| à la demande | `ChatGPT-User`, `Claude-User`, `Perplexity-User` | un humain a demandé une page précise | **autorisés** |

`Google-Extended` n'est pas un crawler : c'est un jeton `robots.txt` qui contrôle
l'usage du contenu pour l'entraînement Gemini/Vertex. Il ne gouverne **ni** la
recherche **ni** les AI Overviews (ceux-ci suivent `Googlebot` et les directives
`nosnippet` / `max-snippet`). Bloquer `Google-Extended` n'empêche donc pas
d'apparaître dans un AI Overview — c'est l'erreur classique de ce sujet.

La décision D67 tient : **aucun `Disallow`**. La liste blanche publiée
(`docs/AGENTS-IA.md` § 3) est ce qui permet de savoir qui vient ; fermer la porte
ferait perdre la distribution sans rien protéger (aucune page publique n'est
sensible — les trois chemins sensibles relevés dans l'audit du trafic ne sont pas
des routes du service).

### 2.2 Les neuf méthodes de contenu (Princeton KDD 2024)

| Priorité | Méthode | Effet rapporté | Ce que cela veut dire ici |
|---|---|---|---|
| 1 | Citer des sources | +30–115 % | lier la spécification d'origine : norme USPS, RFC 9116, x402, llmstxt.org |
| 2 | Ajouter des statistiques | +40 % | dater et chiffrer ce qu'on affirme (prix, taux, latence mesurée) |
| 3 | Ajouter des citations | +30–40 % | guillemets attribués : `« … » — Nom, rôle, organisation, année` |
| 4 | Ton d'autorité | +6–12 % | affirmer ce qui est mesuré, sans « nous pensons que » |
| 5 | Fluidité | +15–30 % | phrases courtes, une idée par phrase |
| 6 | Facilité de compréhension | +8–15 % | définir les termes à leur première apparition |
| 7 | Termes techniques exacts | +5–10 % | `x402`, `EIP-3009`, `402`, `MCP` : les mots que les clients emploient |
| 8 | Vocabulaire varié | +5–8 % | éviter la répétition mécanique |
| 9 | Bourrage de mots-clés | ~0 % | **à ne pas faire** : neutre à négatif |

Les chiffres sont ceux publiés par les auteurs de l'étude (GEO-bench) : ils
indiquent un ordre de travail, pas une promesse de trafic.

### 2.3 Ce que le service fait déjà sans l'avoir nommé

`/validate` est décrit en JSON avec ses codes (`401`, `403`, `402`, `429`, `503`)
dans `llms.txt` et le manifeste : c'est de la « facilité de compréhension » (6) et
des « termes techniques exacts » (7) appliqués à une API. Les prix affichés sur
`/pricing` sont **calculés depuis le code** (D48) : c'est de la « statistique
datée » (2) faite correctement, et c'est la raison pour laquelle un chiffre de
cette page ne peut pas mentir.

---

## 3. Plan de travail, dans l'ordre

Chaque étape se termine par une mesure : un manque fermé sans mesure rouverte est
un manque qu'on croira fermé.

### 3.1 Publier `/robots.txt` (priorité 1)

Où : fichier `robots.txt` à la racine du dépôt, servi par un
`location = /robots.txt` dans `nginx-discovery.conf` (même mécanique que
`/llms.txt` : `text/plain`, `Cache-Control: public, max-age=3600`) — c'est ainsi
qu'`AGENTS.md` et `llms.txt` sont déjà servis, et cela survit à un redémarrage de
l'application.

Contenu : `Allow: /` général, `Sitemap: https://kder-ai.com/sitemap.xml`, et un
bloc nommé par famille de robots **commenté** (les trois niveaux du § 2.1), sans
aucun `Disallow`. Ce qui suit compte autant que ce qui est écrit : un `robots.txt`
qui autorise tout et le dit est une déclaration, pas un oubli.

Mesure : `curl -sS -o /dev/null -w '%{http_code}\n' https://kder-ai.com/robots.txt`
→ `200`, puis ajout du chemin à `scripts/verifier_urls_public.py` (tâche 7).

### 3.2 Publier `/sitemap.xml` (priorité 2)

Où : généré depuis les routes réelles (jamais recopié à la main), avec `lastmod`
daté, une entrée par page publique et par langue — les six langues existent déjà
(D57–D61), le plan de site doit les déclarer, `hreflang` compris.

Mesure : `200` sur `/sitemap.xml`, et nombre d'URL égal au nombre de pages
publiques × 6, consigné dans le rapport de session.

### 3.3 Ajouter le JSON-LD (priorité 3)

Types utiles ici, et rien de plus :

- `WebApplication` sur `/` (nom, description, offre payée à l'appel) ;
- `Product` / `Offer` sur `/pricing` (prix en euros, plans Starter et Pro) ;
- `FAQPage` sur `/guide` (les questions que le guide répond déjà) ;
- `Organization` sur `/` (nom, contact `address-validator@kder-ai.com`).

Règle : **le balisage dit ce que la page dit**. Un `Offer` qui annonce un prix
absent de la page est un mensonge lisible par machine.

Mesure : un contrôle qui charge le HTML servi et vérifie la présence des blocs
`application/ld+json` attendus sur les quatre pages.

### 3.4 Rendre le contenu citable (priorité 4)

Sur `/`, `/pricing` et `/guide` : une source externe par affirmation structurante
(spécification USPS, RFC 9116, x402, llmstxt.org), les chiffres accompagnés de
leur date et de leur méthode (« mesuré le … par `scripts/…` »), et deux ou trois
citations attribuées.

Interdit : le bourrage de mots-clés (méthode 9) et toute statistique non sourcée.

Mesure : `scripts/analyser_visites.py` avant/après — les agents qui reviennent et
les pages qu'ils lisent ; c'est la seule mesure dont on dispose côté service.

### 3.5 Mesurer ce qui revient (en continu)

- qui vient : `GET /metrics/agents` (compteurs du processus) ;
- ce qui est écrit : `scripts/analyser_agents.py --since 7d` (journal JSONL) ;
- tout le trafic, humains compris : `scripts/analyser_visites.py --since 7d`
  (journal Nginx) — sa section `top_pages` montre si les nouvelles pages sont
  bien celles qui sont lues.

---

## 4. Ce que ce document ne prétend pas

- **Ce n'est pas un score.** Aucun audit 0–100 n'a été exécuté : ni le CLI `geo`,
  ni le skill ne sont installés (§ 5). Les manques du § 1.2 sont des constats de
  fichiers, pas une note.
- **Il ne promet pas de citation.** Être crawlable rend **éligible** ; être cité se
  décide chez l'éditeur du moteur, pas ici.
- **Il ne traite ni l'accessibilité ni la performance** : autres sujets, autres
  mesures.
- **Il ne ferme rien tout seul** : chaque étape du § 3 est un travail à faire, et
  c'est ce travail qui sera mesuré.

---

## 5. Le skill GEO-Optimizer : état mesuré, et la décision qui reste

Tentatives du 15/10/2026 :

| Commande | Résultat |
|---|---|
| `npx skills add Auriti-Labs/geo-optimizer-skill -l --full-depth` | `No valid skills found. Skills require a SKILL.md with name and description.` |
| `npx skills add geo-optimizer -l` | `repository 'geo-optimizer' does not exist` (aucune résolution par nom) |
| arbre du dépôt (`api.github.com`, `?recursive=1`) | **un seul** `SKILL.md`, à la racine, 12 581 octets, **sans frontmatter** (`# GEO Optimizer` en première ligne) |

Le dépôt d'amont est donc lisible (il a servi à écrire le § 2), mais il n'est pas
installable par le CLI du projet — celui-ci exige un `SKILL.md` portant `name` et
`description`.

**Décision du 15/10/2026 : installation manuelle.** Le skill est en place dans
`.agents/skills/geo-optimizer/` — six fichiers (`SKILL.md` avec le frontmatter
ajouté, `SCORING_RUBRIC.md`, `references/ai-bots-list.md`,
`references/princeton-geo-methods.md`, `references/schema-templates.md`, `LICENSE`
MIT conservée). Le contenu d'amont est intact : le frontmatter est la **seule**
modification, et elle est signalée dans un commentaire en tête du fichier.

Ce que cette installation ne fait pas, et qu'il faut savoir :

- **l'outil n'est pas installé** : ni le CLI `geo`, ni le serveur MCP d'audit — il
  n'y a pas de paquet Python ici. Les commandes citées par le skill
  (`geo audit`, `geo llms`, `geo schema`) ne tournent pas sur cette machine. Ce
  qui est disponible, c'est la **méthode** ;
- **`npx skills update` ne gère pas ce skill** : il n'a pas d'entrée dans
  `skills-lock.json`, puisque le CLI ne l'a jamais installé. La mise à jour se
  fait à la main, procédure décrite dans
  `.agents/skills/geo-optimizer/SOURCE.md` (clone, recopie, nouveau commit à
  jour) ;
- **la règle « jamais éditer `.agents/skills/` à la main » est enfreinte ici
  volontairement**, avec l'accord de l'exploitant et pour une raison mesurée
  (aucun autre moyen). La déviation est consignée dans
  `GATES-AGENTS-IA.md` (porte T8) et dans `SOURCE.md`.

La mesure de cette installation reste celle de la porte T8
(`GATES-AGENTS-IA.md`) : le dossier existe, il est non vide, et son `SKILL.md`
porte `name: geo-optimizer`.

