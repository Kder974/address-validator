# Rapport — après-midi du 16/10/2026 : mise en ligne de Claude (Fzed), SMTP et référencement

**Rédigé le :** 16/10/2026, à partir de 07:34 UTC (11:34 à La Réunion)
**Objet :** vérifier l'état après la mise en ligne de Claude, configurer le SMTP du
mail « mot de passe oublié », préparer les inscriptions de référencement
**Méthode :** SSH en lecture seule + deux gestes d'exploitation encadrés (ajout des
`SMTP_*` dans `/opt/address-validator/.env`, redémarrage du service) ; `curl` publics ;
`git fetch` dans les deux arbres locaux
**Ce qui a été fait sur le serveur, exactement :** sauvegarde de `.env`, ajout des
5 lignes `SMTP_*` (droits inchangés), `systemctl restart address-validator`, deux mails
de test envoyés vers `address-validator@kder-ai.com`. **Aucun code déployé, aucun
fichier de Claude modifié, aucune écriture en base, aucun `git commit`/`push`.**

---

## 1. ÉTAT DU VPS

### 1.1 Avant intervention (07:34 UTC)

| Contrôle | Valeur mesurée |
|---|---|
| Service | `active`, PID 124319, démarré **05:59:25 UTC**, `NRestarts=0`, `Restart=always`, `ExecMainStatus=0` |
| Code en place | `app/*.py` **tous datés `2026-10-16 05:59:12`** — c'est le déploiement de la matinée, rien de plus récent |
| `.env` | 2 295 octets, **`-rw-r----- 1 root www-data`**, modifié le **16/10 à 05:12** |
| Lignes mail du `.env` | une seule : `CONTACT_EMAIL=address-validator@kder-ai.com` (ligne 19) — **aucune clé `SMTP_*`** |
| Base (comptage) | `customers` **0** · `password_resets` **0** · `address_validations` **0** · `payment_transactions` **2** · `page_visits` **99** · `api_calls` **164** |
| SGBD | PostgreSQL local `postgresql://validator:…@localhost:5432/address_validator` |
| Python du venv | 3.12.15 |
| Réseau sortant | `ufw` : `deny (incoming), allow (outgoing)` — `smtp-relay.brevo.com` joignable en **587** et **465**, port **25 fermé** |

**Conclusion : le travail de Claude est bien arrivé sur le serveur (les pages répondent),
mais le SMTP n'était pas configuré et le service n'avait pas été redémarré depuis 05:59:25.**

### 1.2 Après intervention (07:50 UTC)

| Contrôle | Valeur mesurée |
|---|---|
| `.env` | 2 588 octets, **`-rw-r----- 1 root www-data`** (droits intacts), modifié 07:50 |
| Lignes ajoutées | **63** `SMTP_HOST=smtp-relay.brevo.com` · **64** `SMTP_PORT=587` · **65** `SMTP_USER=bdfa1c001@smtp-brevo.com` · **66** `SMTP_PASSWORD=<masqué>` · **67** `SMTP_FROM=address-validator@kder-ai.com` |
| Sauvegarde | `/root/.env.avant-smtp-20261016` (`-rw-r----- root www-data`, 2 295 o) |
| Redémarrage | `systemctl restart address-validator` → `active`, PID **125849**, `07:50:49 UTC`, `NRestarts=0` |
| Écoute | `ss -ltn` : `127.0.0.1:8000` (l'API reste en local) |
| Contrôles internes | `/health` **200**, `/compte/connexion` **200**, `/compte/mot-de-passe-oublie` **200**, `/agents` **200** |
| Journal | aucune ligne `smtp`/`mail`/`error`/`traceback` après redémarrage |

> Note : les trois premiers `curl` lancés 4 s après le `restart` ont renvoyé `000`
> (port pas encore ouvert) ; relancés aussitôt après, ils répondent `200`. Le service
> était simplement en train de démarrer.

---

## 2. CODES HTTP DES URLS PUBLIQUES (mesuré le 16/10/2026, 07:36 et 07:53 UTC)

| URL | Code | Commentaire |
|---|---|---|
| `https://kder-ai.com/` | **200** | |
| `https://kder-ai.com/health` | **200** | sonde de santé |
| `https://kder-ai.com/pricing` | **200** | |
| `https://kder-ai.com/compte/connexion` | **200** | connexion client (travail de Claude) |
| `https://kder-ai.com/compte/mot-de-passe-oublie` | **200** | page servie (GET) **et** formulaire (POST → 200) |
| `https://kder-ai.com/agents` | **200** | page agents IA, 6 langues |
| `https://kder-ai.com/compare` | **200** | comparatif, 6 langues |
| `https://kder-ai.com/robots.txt` | **200** | |
| `https://kder-ai.com/sitemap.xml` | **200** | **42 `<loc>`** vérifiés |
| `https://kder-ai.com/llms.txt` | **200** | |
| `https://kder-ai.com/manifest` | **200** | |
| `https://kder-ai.com/metrics/agents` | **404** | D67 non exposé par le service déployé |
| `https://kder-ai.com/mcp` | **405** | normal : POST uniquement (un navigateur fait GET) |

**Tout le travail public de Claude répond `200`.** Le `sitemap.xml` liste bien
42 adresses (7 pages × 6 langues) : de `https://kder-ai.com/` à
`https://kder-ai.com/ar/compare`.

### 2.1 Structure du sitemap (contrôle)

---

## 3. ÉTAT GIT — LOCAL CONTRE DISTANT

| Arbre | HEAD local | `origin/main` | Écart (`rev-list --left-right --count`) |
|---|---|---|---|
| `c:\Users\kder_\mon-projet-agent` | `cb37300` (branche `main`) | `ce0aaff` | **18 à gauche, 63 à droite** — histoires **disjointes** |
| `C:\Users\kder_\address-validator-vitrine` | `47e0b6d` (branche `main`) | `ce0aaff` | **0 à gauche, 13 à droite** — simple retard, `fast-forward` possible |

### 3.1 Où est le travail de Claude

Il est **dans `origin/main` = `ce0aaff`**, « Merge pull request #24 from
Kder974/tableau-de-bord-v2 ». Les branches fusionnées le 16/10 (et la veille) portent
sa journée :

| Commit | Branche | Contenu |
|---|---|---|
| `6a58f65` | `tableau-de-bord-v2` | **D69** tableau de bord v2 et vraie page de connexion (`/dashboard/connexion`, cookie `__Host-av_pilotage` signé HMAC) |
| `f49eb7f` | `tableau-de-bord-ecrans` | **D68** écrans argent/API/clients du tableau de bord |
| `9ce0899` | `tableau-de-bord-collecte` | **D67** suivi d'audience (`page_visits`, `api_calls`, GeoIP local) |
| `9fbc010` | `seo-technique` | **D66** pages `/agents` et `/compare` en 6 langues **+ `docs/referencement/README.md`** |
| `0bc79f0` | `six-langues-5-…` | inscription, clé montrée une fois, espace client en 6 langues (**D61**) |

`git fetch origin --prune` (16/10, 07:35 UTC) a aussi ramené deux branches que je n'avais
pas : `espace-client-tableau-de-bord` et `seo-technique`.

### 3.2 Conséquence pratique

- **Le guide de référencement de Claude n'existe pas dans l'arbre local.** Il est à
  `docs/referencement/README.md` **à la racine** de `origin/main` (dépôt
  `github.com/Kder974/address-validator`), alors que mon arbre est un monorepo où le
  projet vit sous `address-validator/`. Je l'ai lu avec `git show` — **rien n'a été
  extrait ni modifié**.
- Les deux arbres n'ont pas la même racine : c'est l'explication de l'écart
  « 18 / 63 ». Tant que cette divergence n'est pas tranchée, tout déploiement depuis
  `mon-projet-agent` continuerait d'écraser le travail de `origin/main` (c'est le
  constat de la visite du matin : la production portait `app/observability.py` du clone
  vitrine, pas du monorepo).
- **Aucun `commit`, aucun `push`, aucun `merge` n'a été fait.** Les branches créées en
  local par une autre session restent telles quelles.

---

## 4. SMTP — CONFIGURÉ ? TESTÉ ?

**Oui, configuré et testé, à trois niveaux.** Détail complet et procédure de reprise :
`DEPLOIEMENT_VPS.md` **§ 12** (nouvelle section écrite ce jour).

| Niveau | Ce qui a été exécuté | Résultat mesuré |
|---|---|---|
| 1. Session | `STARTTLS` + `login` + `noop` depuis le VPS (587) | ✅ `session ouverte et authentifiee (noop=250)` |
| 2. Envoi réel | message de test envoyé à `address-validator@kder-ai.com` | ✅ `message de test accepte par le relais` |
| 3. Chaîne applicative | `envoyer_mail()` du service en production (la fonction que la page appelle) | ✅ réglages lus conformes, appel **sans exception**, **aucune plainte** au journal |
| 4. Bout en bout par la page | POST `https://kder-ai.com/compte/mot-de-passe-oublie` | ⚠️ répond `200` mais **n'envoie rien** : `customers` = **0** → aucun compte, donc aucun lien à envoyer |

### 4.1 Ce qu'il faut regarder dans la boîte Zoho

Deux messages de test ont été envoyés (07:48 et 07:52 UTC) :

1. **« Test SMTP depuis le VPS - visite du 16/10/2026 »** — envoyé par un script, avec
   les identifiants Brevo, pour prouver la clé **et** l'IP.
2. **« Address Validator : test de la chaine applicative (16/10/2026) »** — envoyé par
   la fonction du service en production.

Si les deux arrivent : la chaîne est bonne. Pour aller au bout du contrôle, ouvrir
*Autres options → Afficher l'original* et vérifier `Authentication-Results` :
`spf=pass`, `dkim=pass`, `dmarc=pass` (méthode au § 8 de `docs/commercial/GUIDE-BREVO.md`).

### 4.2 La restriction d'IP Brevo : rien à faire

Le piège documenté le 13/10/2026 (`525 5.7.1 Unauthorized IP address`) **ne s'est pas
produit** : l'authentification a été acceptée du premier coup depuis `87.76.197.38` —
et elle l'est aussi depuis le poste (même clé, même login, `noop=250`). Autrement dit,
le compte Brevo n'a **aucune** restriction d'IP active. Aucun ajout à faire dans
*app.brevo.com → Settings → Security → Authorized IP addresses*.

### 4.3 Le seul test qui reste, et pourquoi il est impossible aujourd'hui

La page « mot de passe oublié » n'envoie **que si le compte existe** (c'est voulu : elle
répond la même chose dans tous les cas, pour ne pas révéler qui est client). Or
`customers` contient **0 ligne** et `password_resets` **0 ligne** : il n'y a aucun
client, donc rien à réinitialiser. Pour éprouver la page elle-même :

1. créer un compte sur `https://kder-ai.com/compte/inscription` ;
2. depuis ce compte, demander `https://kder-ai.com/compte/mot-de-passe-oublie` ;
3. le lien doit arriver dans Zoho et permettre de choisir un nouveau mot de passe.

---

## 5. RÉFÉRENCEMENT — CE QUE DIT LE GUIDE DE CLAUDE, ET CE QU'IL RESTE À FAIRE

Source : `docs/referencement/README.md`, 174 lignes, établi le 14/10/2026, **présent
uniquement dans `origin/main`** (commit `9fbc010`, branche `seo-technique`). Lu avec
`git show`, sans rien extraire.

### 5.1 Le partage des rôles (résumé)

Le code a déjà fait sa part : bloc `<!-- seo -->`, `robots.txt`, `sitemap.xml`,
adresse unique, pages `/agents` et `/compare`. Il reste **quatre gestes d'exploitant**,
environ **1 h** en tout, plus l'attente des DNS :

1. **Google Search Console** ;
2. **Bing Webmaster Tools** (qui nourrit aussi la recherche de ChatGPT et Copilot) ;
3. **annuaires MCP** (le registre officiel, Smithery, Glama, mcp.so, PulseMCP, MCP
   Market, la liste `awesome-mcp-servers`) ;
4. **catalogues x402** (Coinbase Bazaar…).

Contrôle d'entrée, vérifié ce jour : `https://kder-ai.com/sitemap.xml` **doit** lister
42 adresses → **42 `<loc>`** ✅, et `https://kder-ai.com/agents` **doit** répondre →
**200** ✅. On peut donc commencer.

### 5.2 Google — les étapes exactes (≈ 5 min + attente DNS)

1. <https://search.google.com/search-console> → **Ajouter une propriété** → type
   **Domaine** → saisir `kder-ai.com` (sans `www`, sans `https`).
2. Google affiche un enregistrement TXT `google-site-verification=…` : le copier, puis
   Namecheap → *Domain List* → `kder-ai.com` → **Manage** → **Advanced DNS** →
   **Add New Record** → *TXT Record* : Host `@`, Value = la valeur copiée, TTL
   *Automatic*. **Ajouter** — ne pas toucher aux TXT déjà en place.
3. Revenir dans Search Console → **Valider** (si l'enregistrement n'est pas encore vu,
   réessayer une heure plus tard) → **Sitemaps** → saisir `sitemap.xml` → **Envoyer**
   (Google doit trouver 42 adresses) → **Inspection de l'URL** →
   `https://kder-ai.com/agents` → **Demander l'indexation**, puis la même chose pour
   `https://kder-ai.com/compare`.

Ensuite, une fois par semaine : **Performances** (les requêtes qui amènent des
visiteurs) et **Pages** (indexées ou non, et pourquoi). `www.kder-ai.com` doit
apparaître en « Page avec redirection » : **c'est voulu**.

**Le TXT Google n'existe pas encore** : mesuré le 16/10/2026, la zone ne porte que
trois TXT (`brevo-code:…`, `zoho-verification=…`,
`v=spf1 include:zoho.com include:spf.brevo.com ~all`). La valeur
`google-site-verification=…` est fournie **par Google**, à l'étape 1 — elle ne peut pas
être préparée à l'avance.

### 5.3 Bing — les étapes exactes (≈ 2 min, après Google)

1. <https://www.bing.com/webmasters> → se connecter.
2. **Importer depuis Google Search Console** : le site, sa vérification **et** son plan
   du site arrivent d'un coup. C'est le chemin le plus court — à faire **après** § 5.2.
3. Sinon (sans Google) : ajouter `https://kder-ai.com/` à la main, vérifier par
   l'enregistrement DNS que Bing propose (même écran Namecheap qu'en § 5.2), puis
   **Sitemaps** → `https://kder-ai.com/sitemap.xml`.

### 5.4 Annuaires MCP — les étapes exactes (≈ 15 min)

Par ordre d'importance :

1. **Registre officiel MCP** — <https://registry.modelcontextprotocol.io>. Installer
   `mcp-publisher` (dépôt `modelcontextprotocol/registry`), puis dans un dossier vide
   **hors du dépôt** : `mcp-publisher init`, remplacer le contenu de `server.json` par
   celui du guide (`name` `com.kder-ai/address-validator`, `version` `1.0.0`,
   `remotes[]` = `streamable-http` sur `https://kder-ai.com/mcp`), générer une paire
   ed25519, poser la **clé publique** dans un TXT Namecheap (`v=MCPv1; k=ed25519; p=…`),
   garder la **clé privée sur la machine**, puis
   `mcp-publisher login dns --domain kder-ai.com` et `mcp-publisher publish`.
   ⚠️ **Nuance importante** : le registre officiel **est déjà publié depuis le
   11/10/2026** sous le nom `io.github.Kder974/address-validator` v1.0.0
   (`DEPLOIEMENT_VPS.md` § 11.5). Le guide propose un second nom, `com.kder-ai/…`,
   vérifié par DNS : c'est une **seconde fiche** (plus forte, car liée au domaine), pas
   une première inscription. À décider.
2. **Smithery** — <https://smithery.ai/new> : coller `https://kder-ai.com/mcp`.
   Smithery lit le serveur lui-même (`initialize`, `tools/list`, gratuits) et remplit la
   fiche. Ces deux appels répondent sans clé, le scan doit passer.
3. **Glama, mcp.so, PulseMCP, MCP Market** — bouton « Add server » / « Submit » sur
   chacun (<https://glama.ai/mcp/servers>, <https://mcp.so>, <https://www.pulsemcp.com>,
   <https://mcpmarket.com>) et coller le texte du § 5.5. Glama et mcp.so indexant
   surtout des **dépôts GitHub**, si le dépôt passe en privé, inscrire l'adresse du
   serveur (`https://kder-ai.com/mcp`) et la page `https://kder-ai.com/agents`, pas le
   dépôt.
4. **Liste `awesome-mcp-servers`** — une demande de fusion sur
   <https://github.com/punkpeye/awesome-mcp-servers>, avec exactement :

   ```markdown
   - [Address Validator](https://kder-ai.com/agents) - Validate and standardize US postal addresses over MCP; pay per call in USDC on Base (x402), no account or API key.
   ```

### 5.5 Les éléments à avoir sous la main (préparés)

| Élément | État |
|---|---|
| `sitemap.xml` | ✅ `https://kder-ai.com/sitemap.xml` — **42 `<loc>`**, vérifié le 16/10/2026 |
| TXT de vérification Google | ⏳ **à générer** par Search Console (étape 1 du § 5.2) ; aucun TXT `google-site-verification` dans la zone aujourd'hui |
| TXT DNS à ne pas écraser | `brevo-code:b9cabf23…`, `zoho-verification=zb82668052…`, `v=spf1 include:zoho.com include:spf.brevo.com ~all` (mesurés le 16/10/2026) |
| Ligne à proposer en PR `awesome-mcp-servers` | ✅ ci-dessus (§ 5.4, point 4) |
| Texte commun des annuaires | ✅ ci-dessous |

**Texte commun (guide § 3.6), à coller tel quel :**

- **Name** : Address Validator
- **Short description** (≤ 100 caractères) : `Validate and standardize US postal
  addresses. Pay per call in USDC (x402), no account.`
- **Description** : One MCP tool, `validate_address`, checks a postal address and returns
  it standardized, with GPS coordinates, deliverability (residential, commercial…) and
  a confidence score. Discovery is free (`initialize`, `tools/list`). Each call costs
  0.02 USDC on Base, paid with the x402 protocol (EIP-3009 authorization in the
  `X-PAYMENT` header): the agent needs a wallet, not an account. Optimised for US
  addresses. Shops can use the same service with an API key and a monthly plan.
- **Server URL** : `https://kder-ai.com/mcp` — Streamable HTTP, stateless
- **Discovery card** : `https://kder-ai.com/.well-known/mcp-server`
- **Website** : `https://kder-ai.com/agents`
- **Docs** : `https://kder-ai.com/guide`
- **Pricing** : `https://kder-ai.com/pricing`
- **Contact** : l'adresse de `contact.email` dans `https://kder-ai.com/manifest`
- **Tags** : address-validation, address-verification, geocoding, shipping, e-commerce,
  x402, usdc, base, payments

> Si le prix change (`X402_PRICE`), refaire ce texte **et les fiches déjà publiées** :
> les pages du site suivent toutes seules, pas les annuaires.

### 5.6 Catalogues x402 : pas possible aujourd'hui

Le Bazaar de Coinbase indexe tout seul les services qui encaissent par le facilitateur
Coinbase (CDP), **à condition** que leur réponse `402` décrive le service. Le nôtre
passe bien par ce facilitateur, mais son `402` porte `"outputSchema": null`
(`app/x402.py`) : il n'est donc pas indexé. Y entrer demande une **modification du
code** (décrire le service dans le `402`) puis un contrôle avec l'outil de validation
Coinbase — décision et tests à part. Les autres places x402 (x402scan, PayAPI Market,
relai…) se visitent au cas par cas : vérifier qui l'exploite, ce qu'il demande, et
qu'il ne réclame **jamais** une clé privée de portefeuille.

---

## 6. ACTIONS RESTANTES

| # | Action | Qui | Quand |
|---|---|---|---|
| 1 | Vérifier la réception des **deux mails de test** dans Zoho + `spf=pass/dkim=pass/dmarc=pass` | Kder | maintenant |
| 2 | Éprouver la page « mot de passe oublié » avec un **vrai compte** (`customers` = 0 aujourd'hui) | Kder | dès qu'il y a un compte |
| 3 | **Google Search Console** puis **Bing** (§ 5.2, § 5.3) | Kder (comptes perso) | 5 + 2 min |
| 4 | **Annuaires MCP** (§ 5.4) — dont trancher : seconde fiche `com.kder-ai/…` ou garder `io.github.Kder974/…` | Kder | 15 min |
| 5 | Trancher la **lignée de référence** (`mon-projet-agent` 18/63 vs clone vitrine 0/13) **avant tout déploiement** | Kder | priorité haute |
| 6 | **Alerte d'envoi SMTP** : personne n'est prévenu si les mails cessent de partir (`warning` avalé par le code) | à décider | — |
| 7 | `D67` : `/metrics/agents` répond **404** alors que `page_visits`/`api_calls` se remplissent (99 / 164 lignes) — l'exposition est incomplète en production | à décider | — |
| 8 | Rotation de la clé SMTP : elle a transité par `/tmp` sur le serveur (supprimée depuis) et vit dans `.env.smtp` sur le poste — la renouveler si on veut une remise à zéro | à décider | — |

---

## 7. ANNEXE — méthode, commandes, limites

### 7.1 Ce qui a été fait sur le serveur (la liste complète)

1. `cp -p /opt/address-validator/.env /root/.env.avant-smtp-20261016` (sauvegarde) ;
2. `scp` du `.env.smtp` du poste vers `/tmp/smtp_brevo.env`, `chmod 600` ;
3. ajout des **5 lignes `SMTP_*`** en fin de `/opt/address-validator/.env`
   (avec `sed 's/\r$//'` pour retirer les CRLF) ;
4. `systemctl restart address-validator` ;
5. contrôles `systemctl` + `curl` locaux et publics ;
6. deux mails de test (script, puis fonction du service) ;
7. `rm -f /tmp/smtp_brevo.env /tmp/smtp_block.env` (identifiants temporaires).

**Aucun `chmod`/`chown` sur `.env`** (il est resté `root:www-data 640`), **aucun code
déployé**, **aucun fichier de Claude modifié**, **aucune écriture en base**, aucun
`git commit`/`push`/`merge`.

### 7.2 Documents touchés

| Fichier | Nature |
|---|---|
| `address-validator/DEPLOIEMENT_VPS.md` | **§ 12 ajouté** (« SMTP en production ») — le document s'arrêtait à § 11.5 : il n'y avait ni § 12, ni § 13, ni § 14 |
| `address-validator/scripts/test_smtp_vps.py` | **nouveau** — test de connexion/envoi côté serveur (le pendant de `scripts/test_smtp_connection.py`, qui teste le poste) ; il ne doit pas vivre seulement dans `/tmp` |
| `address-validator/docs/RAPPORT-SMTP-ET-REFERENCEMENT-2026-10-16.md` | ce rapport (nouveau) |

### 7.3 Limites de ce rapport

- L'écart Git est celui mesuré après `git fetch` du 16/10/2026 07:35 UTC ; il bouge à
  chaque `push` de Claude.
- Aucun accès au compte Brevo : la conclusion « pas de restriction d'IP » vient du
  serveur (`525` absent), pas de l'interface.
- Aucun accès à la boîte Zoho : la **réception** des deux mails de test doit être
  confirmée par Kder.
- Le compte `root` du VPS a été utilisé (le seul disponible) ; aucun test n'a été fait
  avec un autre compte.
