# Découvrabilité — address-validator

Ce document décrit **comment un agent autonome peut trouver cette API**, ce qui
est publié pour cela, ce qui ne l'est **pas** et pourquoi, et comment vérifier
que la publication tient après un déploiement. Il décrit le comportement du code
de ce dépôt, mesuré le 10/10/2026.

Principe directeur : **ne publier qu'une capacité qui répond**. Un fichier de
découverte est une promesse ; une URL de découverte qui renvoie 404 coûte plus
cher que son absence, parce qu'un agent perd son tour et retient que le service
ment.

## 1. Ce qui est publié

| URL | Source du texte | Pour qui |
|-----|-----------------|----------|
| `https://kder-ai.com/llms.txt` | `llms.txt` (statique, Nginx) | LLM et agents qui lisent un résumé en texte |
| `https://kder-ai.com/AGENTS.md` | `AGENTS.md` (statique, Nginx) | agents qui suivent une procédure d'intégration |
| `https://kder-ai.com/.well-known/security.txt` | `.well-known/security.txt` (statique, Nginx) | chercheurs en sécurité (RFC 9116) |
| `https://kder-ai.com/mcp` | `app/routes/mcp.py` (application) | agents MCP : `initialize`, `tools/list` (gratuit), `tools/call` (payant) |
| `https://kder-ai.com/.well-known/mcp-server` | `app/routes/mcp.py` (application) | agents qui découvrent un serveur MCP par `.well-known` (brouillon mcp1) |
| `https://kder-ai.com/manifest` | `app/routes/manifest.py` (application) | clients x402 qui lisent les schémas, l'offre et les limites |

Les trois premiers sont servis en **statique** par Nginx, via le fragment
`nginx-discovery.conf`, inclus dans le bloc `server` du vhost. Ils ne passent pas
par l'application : ils répondent même si le service Python est arrêté.

Les deux chemins MCP, eux, sont des **routes de l'application**. C'est
volontaire : la carte décrit un endpoint réel, donc elle doit disparaître avec
lui. Une copie statique servie par Nginx survivrait à l'arrêt du service et
ferait déclarer aux agents une URL morte. `nginx-discovery.conf` dit explicitement
de ne pas les aliaser.

Le fichier `nginx-discovery.conf` est lui aussi relu par la porte **D28**
(`scripts/decisions_check.py D28`) : présence, encodage, fins de ligne, et surtout
**chaque chemin `kder-ai.com/...` cité doit correspondre à une route mesurée de
l'application**. La porte refuse aussi un `security.txt` sans `Expires` ou déjà
périmé.

Le manifeste de l'API (`GET /manifest`) complète ces fichiers pour un client
automatique : schémas dérivés des modèles Pydantic, exemples, cas d'usage,
limites explicites et offre de paiement (porte **D33**). Depuis le Lot B,
l'offre publiée dans `payment.accepts[]` est produite par la **même** fonction
que le corps `402` (porte **D34**), donc le manifeste ne peut pas annoncer un
prix que le serveur refuse.

## 2. Ce qui n'est pas publié, et pourquoi

| Élément non publié | Raison mesurée |
|--------------------|----------------|
| `ard.json` | la spécification « ARD » n'est documentée par aucune source vérifiable. Publier un format inventé n'aide personne. |
| Schéma YAML local de Smithery (`smithery.yaml` « commande de démarrage ») | la seule source officielle consultable le 11/10/2026 documente la publication **par URL** (`smithery mcp publish <url>`) ; le schéma des serveurs locaux n'a pas pu être confirmé. Le fichier du dépôt est donc descriptif, sans clé inventée — et la porte D38 refuse l'apparition de ces clés. |
| Copie statique de la carte MCP | elle survivrait à l'endpoint qu'elle décrit (voir §1). La carte est servie par l'application, et D37 le vérifie. |
| Classe de confiance MCP (`trust_class`, `compliance`, `logging`) | le brouillon impose des sous-champs par classe que ce dépôt ne peut pas prouver : annoncer une classe non tenue serait une fausse déclaration. |
| `nginx-discovery.conf` n'est pas dans `conf.d/` | `location` n'est pas valable en contexte `http` : le fragment doit être inclus dans le bloc `server`. |

L'activation de ces éléments suppose d'abord de vérifier la source du format, puis
d'implémenter la capacité correspondante — jamais l'inverse.

## 3. Vérifier après un déploiement

```bash
for chemin in llms.txt AGENTS.md .well-known/security.txt .well-known/mcp-server manifest health; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "https://kder-ai.com/$chemin")
  echo "$code  $chemin"
done
# Le serveur MCP repond a un message JSON-RPC gratuit (decouverte) :
curl -sS -X POST https://kder-ai.com/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -c 400
# Sans paiement, /validate repond 402 et publie son offre x402 :
curl -sS -X POST https://kder-ai.com/validate -H 'Content-Type: application/json' \
  -d '{"address":{"street":"1 A St","city":"X","state":"CA","zip":"90001"}}' \
  | python -c "import json,sys; print(json.load(sys.stdin)['accepts'][0])"
# Le tableau de bord est protege :
curl -sS -o /dev/null -w '%{http_code}\n' https://kder-ai.com/dashboard
```

Attendu : `200` sur les fichiers, la carte MCP et le manifeste ; une liste
d'outils contenant `validate_address` ; un `402` dont l'offre porte
`maxAmountRequired = "20000"` ; `401` (ou `503` si `DASHBOARD_PASSWORD` est
absent) sur le tableau de bord.

Le contrôle complet de l'offre et du domaine EIP-712 se fait avec
`scripts/test_x402_payment.py` (mode plan, puis `--check-domain`, puis `--pay`).

**Piège mesuré** : `certbot --nginx` réécrit le vhost à chaque obtention ou
renouvellement de certificat, et la ligne `include` du fragment peut disparaître.
Après tout passage de certbot, relancer la boucle `curl` ci-dessus : une réponse
`404` signifie que la publication est tombée, pas que le fichier est absent du
dépôt. Les deux chemins MCP ne dépendent pas de cette ligne — ils passent par
l'application — mais les trois fichiers statiques, si.

## 4. Mesurer la découverte

Publier ne suffit pas : il faut savoir si quelqu'un **trouve**. Deux mécanismes,
tous deux sans donnée personnelle inutile :

- **Journal des refus (porte D30, `app/observability.py`).** Chaque requête
  refusée en `402` écrit une ligne JSONL : horodatage, endpoint, prix,
  `User-Agent`, `Referer`, et l'IP **pseudonymisée** (SHA-256 + sel) par défaut.
  L'adresse validée n'y figure jamais. `DISCOVERY_LOG_RAW_IP=true` met l'IP en
  clair : c'est une mesure d'enquête datée, pas un réglage de confort
  (`PRIVACY.md` §3).
- **Revenu (porte D31, `GET /metrics/revenue`).** Agrégats 24 h / 7 j / 30 j lus
  dans le registre durable, avec `ledger.available` et `ledger.error` : une base
  muette est **visible** dans la réponse au lieu d'un zéro trompeur.

Le rapport quotidien `scripts/daily_report.sh` lit ces deux sources, purge le
journal selon `DISCOVERY_LOG_RETENTION_DAYS` (la conservation annoncée est donc
appliquée) et envoie le résultat. Cron recommandé :

```cron
10 7 * * * /opt/address-validator/scripts/daily_report.sh
```

Le script sort en code `1` lorsque le registre de paiement est indisponible : un
rapport partiel ne doit pas passer pour un rapport normal.

## 5. Ce que la recette vérifie

| Porte | Ce qu'elle mesure |
|-------|-------------------|
| D28 | les fichiers publiés n'annoncent que des routes qui répondent |
| D29 | un paiement accepté est enregistré une fois, sous le hash de sa preuve |
| D30 | une tentative non payée est journalisée, IP pseudonymisée par défaut |
| D31 | `/metrics/revenue` publie un revenu durable et avoue ses pannes |
| D32 | `/dashboard` est autonome, fermé par défaut et protégé par mot de passe |
| D33 | le manifeste porte schémas, exemples, cas d'usage et limites explicites |
| D34 | le `402` est un document `PaymentRequired` x402 v1 et un paiement réglé porte son reçu |
| D35 | la signature EIP-712 est vérifiée hors ligne, avant tout appel réseau |
| D36 | le service n'est rendu qu'après règlement, et tout échec de règlement est un refus |
| D37 | le serveur MCP sert un outil payant et sa carte décrit l'endpoint réel |
| D38 | les fichiers d'annuaire décrivent l'endpoint réel dans un format sourcé |
| D39 | la rétention de 30 jours est appliquée par une purge planifiée |

Les tests correspondants vivent dans `tests/test_discovery.py`,
`tests/test_revenue_dashboard.py`, `tests/test_manifest.py`, `tests/test_x402.py`
et `tests/test_mcp.py`.
