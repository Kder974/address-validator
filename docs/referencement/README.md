# Référencement — ce qui reste à faire à la main (après D65 et D66)

Le code fait sa part : bloc `<!-- seo -->`, `robots.txt`, `sitemap.xml`, adresse
unique, pages `/agents` et `/compare`. Ce document est la part de l'exploitant :
se déclarer aux moteurs et s'inscrire dans les annuaires d'agents. Établi le
14/10/2026.

| Question | Réponse |
|----------|---------|
| **Qui** | l'exploitant (Kder), avec ses comptes Google, Microsoft, GitHub et Namecheap |
| **Quoi** | 1. Google Search Console · 2. Bing Webmaster Tools · 3. annuaires MCP · 4. catalogues x402 |
| **Quand** | après la mise en ligne de D65 et D66 (sinon le plan du site cite des pages absentes) |
| **Combien de temps** | environ 1 h en tout, plus l'attente des DNS (jusqu'à quelques heures) |
| **Secret** | aucun dans ce document. La clé privée du registre MCP (§ 3.1) reste sur la machine de l'exploitant, **jamais** dans le dépôt |

Contrôle avant de commencer : `https://kder-ai.com/sitemap.xml` doit lister
42 adresses (7 pages publiques × 6 langues) et `https://kder-ai.com/agents`
doit répondre.

---

## 1. Google Search Console

1. Ouvrir <https://search.google.com/search-console>, **Ajouter une propriété**,
   choisir le type **Domaine** et saisir `kder-ai.com` (sans `www`, sans `https`).
2. Google affiche un enregistrement TXT `google-site-verification=…`. Le copier.
3. Namecheap → *Domain List* → `kder-ai.com` → **Manage** → **Advanced DNS** →
   **Add New Record** → *TXT Record* :
   - Host : `@`
   - Value : la valeur copiée
   - TTL : *Automatic*

   **Ajouter** un enregistrement : ne modifier ni le TXT SPF ni le TXT DMARC
   déjà en place (`docs/commercial/DNS-A-CONFIGURER.md`).
4. Revenir dans Search Console et cliquer **Valider**. Si Google ne trouve pas
   encore l'enregistrement, réessayer une heure plus tard.
5. Menu **Sitemaps** : saisir `sitemap.xml` et **Envoyer**. Google doit trouver
   42 adresses.
6. Menu **Inspection de l'URL** : coller `https://kder-ai.com/agents`, puis
   **Demander l'indexation**. Même chose pour `https://kder-ai.com/compare`.

Ce qu'on regarde ensuite, une fois par semaine : **Performances** (les requêtes
qui amènent des visiteurs) et **Pages** (indexées ou non, et pourquoi).
`www.kder-ai.com` doit apparaître comme « Page avec redirection » : c'est voulu.

## 2. Bing Webmaster Tools

Bing compte double : ses résultats nourrissent aussi la recherche de ChatGPT et
Copilot.

1. Ouvrir <https://www.bing.com/webmasters> et se connecter.
2. Choisir **Importer depuis Google Search Console** : le site, sa vérification
   et son plan du site arrivent en une fois. C'est le chemin le plus court, une
   fois le § 1 fini.
3. Sinon : ajouter `https://kder-ai.com/` à la main, vérifier par l'enregistrement
   DNS que Bing propose (même écran Namecheap qu'au § 1), puis **Sitemaps** →
   `https://kder-ai.com/sitemap.xml`.

## 3. Annuaires MCP

Les agents et leurs utilisateurs cherchent des serveurs MCP dans ces annuaires.
Le texte commun est au § 3.6 : le même partout, en anglais, langue des annuaires.

### 3.1 Registre officiel MCP (le plus important)

<https://registry.modelcontextprotocol.io>, tenu par le projet Model Context
Protocol. Un nom en `com.kder-ai/…` prouve que le serveur appartient au domaine :
la preuve passe par un enregistrement TXT.

1. Installer l'outil `mcp-publisher` (instructions dans le dépôt GitHub
   `modelcontextprotocol/registry`).
2. Dans un dossier vide, **hors du dépôt** : `mcp-publisher init`. Il crée un
   `server.json` avec le bon `$schema`. Garder ce `$schema` et remplacer le reste
   par :

   ```json
   {
     "name": "com.kder-ai/address-validator",
     "description": "Validate and standardize US postal addresses. Pay per call in USDC (x402), no account.",
     "version": "1.0.0",
     "remotes": [
       { "type": "streamable-http", "url": "https://kder-ai.com/mcp" }
     ]
   }
   ```

3. Générer une paire de clés ed25519 et poser la **clé publique** dans un TXT
   Namecheap (Host `@`), au format que donne la documentation du registre
   (`v=MCPv1; k=ed25519; p=…`). La **clé privée** ne quitte pas la machine.
4. `mcp-publisher login dns --domain kder-ai.com` (avec la clé privée), puis
   `mcp-publisher publish`.
5. Contrôle : chercher `address-validator` dans le registre.

Le format exact du TXT et les options de `login dns` peuvent changer : suivre la
documentation du registre au moment de l'inscription, pas ce résumé.

### 3.2 Smithery

<https://smithery.ai/new> : coller `https://kder-ai.com/mcp`. Smithery lit le
serveur lui-même (`initialize`, `tools/list`, gratuits) et remplit sa fiche. Le
service répond sans clé à ces deux appels, le scan doit donc passer.

### 3.3 Glama, mcp.so, PulseMCP, MCP Market

Chacun a un bouton d'ajout de serveur (« Add server », « Submit ») sur son site :
<https://glama.ai/mcp/servers>, <https://mcp.so>, <https://www.pulsemcp.com>,
<https://mcpmarket.com>. Coller le texte du § 3.6.

**Attention** : Glama et mcp.so indexent surtout des dépôts GitHub. Si le dépôt
passe en privé, inscrire l'adresse du serveur (`https://kder-ai.com/mcp`) et la
page `https://kder-ai.com/agents`, pas le dépôt.

### 3.4 Liste « awesome-mcp-servers »

Une demande de fusion (pull request) sur
<https://github.com/punkpeye/awesome-mcp-servers>, en suivant le format du README
de la liste (catégorie, légende, ordre alphabétique). Ligne proposée :

```markdown
- [Address Validator](https://kder-ai.com/agents) - Validate and standardize US postal addresses over MCP; pay per call in USDC on Base (x402), no account or API key.
```

### 3.5 Ce qu'il ne faut pas écrire

Le service fait une seule chose : valider une adresse. Ne pas écrire
« worldwide » ni « global » : la vérification est optimisée pour les États-Unis
et partielle ailleurs. Ne pas promettre de ressource MCP, d'invite ni de flux
SSE : le serveur n'en a pas (`AGENTS.md`, « Ce qui n'existe pas »).

### 3.6 Texte commun

- **Name** : Address Validator
- **Short description** (100 caractères au plus) : Validate and standardize US
  postal addresses. Pay per call in USDC (x402), no account.
- **Description** : One MCP tool, `validate_address`, checks a postal address
  and returns it standardized, with GPS coordinates, deliverability
  (residential, commercial…) and a confidence score. Discovery is free
  (`initialize`, `tools/list`). Each call costs 0.02 USDC on Base, paid with
  the x402 protocol (EIP-3009 authorization in the `X-PAYMENT` header): the
  agent needs a wallet, not an account. Optimised for US addresses. Shops can
  use the same service with an API key and a monthly plan.
- **Server URL** : `https://kder-ai.com/mcp` — Streamable HTTP, stateless
- **Discovery card** : `https://kder-ai.com/.well-known/mcp-server`
- **Website** : `https://kder-ai.com/agents`
- **Docs** : `https://kder-ai.com/guide`
- **Pricing** : `https://kder-ai.com/pricing`
- **Contact** : l'adresse de `contact.email` dans `https://kder-ai.com/manifest`
- **Tags** : address-validation, address-verification, geocoding, shipping,
  e-commerce, x402, usdc, base, payments

Si le prix change (`X402_PRICE`), refaire ce texte et les fiches déjà publiées :
les pages du site suivent toutes seules, pas les annuaires.

## 4. Catalogues x402

### 4.1 x402 Bazaar (Coinbase) : pas encore possible

Le Bazaar indexe tout seul les services qui encaissent par le facilitateur
Coinbase (CDP), **à condition** que leur réponse 402 décrive le service (schéma
d'entrée et de sortie). Le service passe bien par ce facilitateur
(`X402_FACILITATOR_URL`), mais son 402 HTTP porte `"outputSchema": null`
(`app/x402.py`) : il n'est donc pas indexé aujourd'hui.

Pour y entrer, il faut une **modification du code**, avec sa propre décision et
ses tests : décrire le service dans le 402, puis le contrôler avec l'outil de
validation de Coinbase (<https://docs.cdp.coinbase.com/x402/validate-endpoint>).
Elle n'est pas dans cette branche.

### 4.2 Autres places de marché x402

Il en naît chaque mois (par exemple x402scan, PayAPI Market, relai). Avant de
s'inscrire, vérifier sur le site : qui l'exploite, ce qu'il demande (souvent
l'adresse du service et rien d'autre), et qu'il ne demande **jamais** une clé
privée de portefeuille. Texte : celui du § 3.6.
