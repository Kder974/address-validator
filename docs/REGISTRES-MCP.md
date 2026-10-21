# Registres MCP — où nous sommes publiés (coût : 0 €)

> Règle appliquée : **aucun listing payant**. Toutes les démarches ci-dessous
> sont gratuites. Dernière mise à jour : 21/10/2026.

| Registre | Statut | Démarche | Lien |
|---|---|---|---|
| **Official MCP Registry** (`registry.modelcontextprotocol.io`) | ✅ **publié v1.0.1** | API officielle (`/v0.1/auth/github-at` + `/v0.1/publish`), namespace `io.github.Kder974` | https://registry.modelcontextprotocol.io/?q=io.github.Kder974 |
| **GitHub MCP Registry** (`github.com/mcp`) | ✅ alimenté par le registre officiel | pas de soumission séparée : GitHub lit le registre MCP officiel | https://github.com/mcp |
| **mcp.so** | ✅ soumis (en attente d'indexation) | commentaire sur l'issue de soumission n°1 | https://github.com/chatmcp/mcpso/issues/1#issuecomment-6315040477 |
| **Glama** (`glama.ai/mcp/servers`) | ⏳ à faire par Kder (compte GitHub) | bouton « Add Server » (gratuit) ; auto-découverte pas encore constatée | https://glama.ai/mcp/servers |
| **awesome-x402-mcp-services** | ✅ PR ouverte | entrée « Address validation » ajoutée à la liste | https://github.com/Recall-Kitchen/awesome-x402-mcp-services/pull/112 |
| **xpaysh/awesome-x402** | ✅ PR ouverte | entrée dans « Production Implementations » | https://github.com/xpaysh/awesome-x402/pull/1936 |

## Ce qui est publié

- **Serveur :** `io.github.Kder974/address-validator` (version **1.0.1**)
- **Endpoint :** `https://kder-ai.com/mcp` (streamable HTTP, sans clé)
- **Outil :** `validate_address` — validation US (USPS DPV) et France (BAN),
  standardisation, géocodage, score de livrabilité.
- **Prix :** 0,02 USDC par appel sur Base (x402 v2) ; `initialize` et `tools/list`
  gratuits.
- **Snippet copier-coller :** https://kder-ai.com/agents#mcp-payer
- **Découverte :** carte `/.well-known/mcp-server`, `llms.txt`, `AGENTS.md`.

## Fiche préparée pour Glama (soumission manuelle gratuite)

À saisir dans « Add Server » sur https://glama.ai/mcp/servers :

- **Name :** Address Validator
- **URL :** https://kder-ai.com/mcp
- **Repository :** https://github.com/Kder974/address-validator
- **Description :** Validate and standardize US and French delivery addresses
  (USPS DPV + Base Adresse Nationale): corrected components, geocoding and a
  residential/commercial deliverability score. x402 USDC on Base (0.02/call);
  initialize and tools/list are free.
- **Tags :** address-validation, shipping, e-commerce, x402, agents
- **Snippet :** https://kder-ai.com/agents#mcp-payer

## Comment rafraîchir le registre officiel

Le fichier `server.json` est la source. À chaque release :

1. mettre à jour `version` (le registre refuse une version déjà publiée) ;
2. lancer le workflow **Publish to MCP Registry** (`.github/workflows/publish-mcp.yml`)
   ou, à la main : `mcp-publisher login github` puis `mcp-publisher publish` ;
3. vérifier `https://registry.modelcontextprotocol.io/v0.1/servers/io.github.Kder974%2Faddress-validator/versions/latest`.

Le registre est en **preview** : une remise à zéro reste possible avant GA.
