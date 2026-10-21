# Veille agentique — payer 0 € pour voir venir la vague agentique

> Objectif : savoir chaque matin, en 5 minutes, si les agents qui découvrent
> l'API **paient** ou seulement **sondent**, et ce qui a changé dans l'écosystème
> x402. Coût : **0 €** — aucune API payante, aucun SaaS, aucun mot de passe
> supplémentaire.

| | |
|---|---|
| Script | `scripts/veille_agentique.py` |
| Rapport | `docs/VEILLE-AGENTIQUE-DERNIER.md` en local ; `/var/log/address-validator/veille.md` sur le VPS |
| État | `data/veille_agentique_etat.json` en local ; `/var/lib/address-validator/veille_agentique.json` sur le VPS |
| Déclencheur | timer systemd `address-validator-veille.timer`, **07:10 UTC** |
| Email | `--email` si `SMTP_*` est présent dans `.env` (Brevo déjà configuré) |
| Données personnelles | aucune : le journal de découverte est déjà pseudonymisé |

---

## 1. Ce que la veille regarde

### A. Revenu réel (interne, source de vérité)
- `GET /metrics/revenue` via `revenue_stats` (même fonction que l'API, pas une
  copie) : paiements réels, cumul, fenêtres 24 h / 7 j, payeurs distincts,
  date du dernier paiement.
- Règle : `real_*` exclut les paiements marqués `test_mode` ; un essai de
  recette n'est pas du revenu.

### B. Agents et conversion (interne)
- Journal JSONL `DISCOVERY_LOG_PATH` : refus 402, classes d'appelants, agents
  distincts, endpoints, fenêtre `--depuis` (24 h par défaut).
- Journal Nginx `combined` (`--acces`) : `/validate` et `/mcp` par statut.
  C'est la mesure de la conversion : un 402 sur `/validate` est une tentative
  non payée, un 200 est un paiement accepté.

### C. Écosystème (externe, gratuit)
- **GitHub releases** via les flux Atom publics (`/<repo>/releases.atom`,
  sans jeton ni quota) : `coinbase/x402`, `coinbase/agentkit`,
  `x402-index/x402-discovery-index`, `google-agentic-commerce/AP2` ;
- **x402 Discovery Index** : nombre d'API annoncées (README public) ;
- **Bazaar** : **endpoint officiel de validation CDP**
  (`POST /platform/v2/x402/validate`, gratuit) — il répond ce que le catalogue
  voit : `valid`, `index.active`, `lastCrawledAt`, checks requis en échec. Le
  catalogue `/discovery/resources`, lui, ne pagine pas (100 entrées sur ~34 000) :
  s'y fier donne un faux « absent » ;
- **On-chain** : **solde USDC** de la wallet `payTo` via `eth_call balanceOf`
  (un seul appel, repli sur des nœuds publics Base : `BASE_RPC_URL`,
  `base-rpc.publicnode.com`, `base.drpc.org`). La variation de solde entre deux
  passages est l'alerte ; le revenu détaillé vient du registre.

### D. Alertes (uniquement les changements)
- nouveau paiement réel (cumul qui augmente) ;
- dernière date de paiement qui change ;
- nouvelle release d'un dépôt suivi ;
- nouvelle classe d'appelant dans le journal ;
- transfert USDC on-chain ;
- sources indisponibles (écrites, jamais masquées).

---

## 2. Installation sur le VPS (10 minutes, 0 €)

```bash
# 1. Copier le script et les units (après un deploiement normal du depot)
sudo install -o root -g root -m 755 /opt/address-validator/scripts/veille_agentique.py /opt/address-validator/scripts/veille_agentique.py
sudo install -o root -g root -m 644 /opt/address-validator/address-validator-veille.service /etc/systemd/system/
sudo install -o root -g root -m 644 /opt/address-validator/address-validator-veille.timer /etc/systemd/system/
sudo mkdir -p /var/lib/address-validator /var/log/address-validator
sudo systemctl daemon-reload
sudo systemctl enable --now address-validator-veille.timer

# 2. Premier passage à la main (écrit le rapport, envoie l'email si SMTP_* est là)
sudo systemctl start address-validator-veille.service
sudo journalctl -u address-validator-veille -n 40 --no-pager
sudo cat /var/log/address-validator/veille.md
```

Vérifier que le timer est planifié :

```bash
systemctl list-timers address-validator-veille.timer
```

### Email
Le service charge `/opt/address-validator/.env` qui contient déjà
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` (Brevo).
Le destinataire est `VEILLE_MAIL_TO` s'il est défini, sinon `CONTACT_EMAIL`,
sinon l'expéditeur. Pour changer le destinataire :

```bash
sudo systemctl edit address-validator-veille.service
# [Service]
# Environment=VEILLE_MAIL_TO=mon-adresse@exemple.fr
```

Aucune clé n'est écrite dans le dépôt ; le script n'imprime jamais les secrets.

---

## 3. En local (Windows)

```powershell
cd address-validator
# Sources internes uniquement (pas de réseau, pas d'email)
.\.venv\Scripts\python.exe scripts\veille_agentique.py --sans-reseau --sans-acces `
  --sortie docs\VEILLE-AGENTIQUE-DERNIER.md

# Tout, avec le journal Nginx si disponible
.\.venv\Scripts\python.exe scripts\veille_agentique.py --depuis 7d

# JSON brut (pour un script ou un test)
.\.venv\Scripts\python.exe scripts\veille_agentique.py --json --sans-reseau
```

Le rapport local est ignoré par git : ce sont des mesures, pas du contenu de
dépôt.

---

## 4. Budget

| Poste | Coût |
|---|---:|
| Script, timer, email | **0 €** |
| Flux GitHub Atom (releases) | 0 € |
| Nœud Base public (`mainnet.base.org` + repli publicnode/drpc) | 0 € |
| Journal de découverte et Nginx | déjà payés avec le VPS |
| Total mensuel | **0 €** |

Aucun service payant n'est nécessaire. Si un jour un annuaire (ex. x402-index)
facture un listing, la décision ne se prendra qu'avec un revenu réel ≥ 50
USDC/mois — sinon on reste sur les annuaires gratuits.

---

## 5. Audit de conversion initial (21/10/2026)

Constat tiré des journaux de production sur ~10 jours :

| Mesure | Valeur |
|---|---:|
| Refus `402` sur `/validate` | 1 051 |
| Paiements acceptés (`200`) | 47 (dont recette) |
| **Paiements réels encaissés** | **4** (0,08 USDC, 2 payeurs) |
| Dernier paiement réel | 19/10 21:32 UTC |
| Revenu 24 h / 7 j | 0 / 0 |
| Appels `POST /mcp` | 9 923 `200` (découverte gratuite), 2 310 `202` |
| Robots distincts (top UA) | ~12 familles : `CarbonMonitor`, `mako-pulse-prober`, `Agent402`, `x402-census-probe`, `AgentRankProbe`, `lumiere-paycheck`, `x402-router-crawler`… |

### Chantiers de conversion (0 €, à mener après la mise en route de la veille)
1. **MCP** : 9 923 appels gratuits et aucun paiement. Vérifier le résultat
   `tools/call` non payé (`isError` + offre), le tester avec un vrai client MCP,
   et rendre l'exemple de paiement copiable dans `/agents` et la carte MCP.
2. **Fichiers de découverte** : logs `404` sur `/.well-known/agent-card.json`
   (80), `/.well-known/agent.json` (80), `/.well-known/oauth-protected-resource`
   (61). `/.well-known/x402` existe déjà (D95). N'ajouter que les specs
   officielles (A2A agent card, OAuth PRM) ou rediriger vers le manifest —
   jamais un fichier qui promet une intégration inexistante.
3. **Annuaires** : rester sur les listings gratuits (MCP registry, awesome-lists) ;
   ne pas payer de listing avant revenu.
   **Constat du 21/10 corrigé** : nous **sommes déjà dans l'index Bazaar** —
   l'endpoint officiel de validation répond `valid: true`, `index.active: true`,
   dernier crawl **19/10 13:19** (paiement SettledScout). Le faux « absent »
   venait du catalogue public, qui ne pagine pas. Action faite : phrase
   « Use when… » dans la description (règle v2 : ≤ 500 caractères, dire quand
   utiliser l'endpoint). L'ajout de `discoverable` a été retiré : il faisait
   échouer la validation officielle (`(root).input: discoverable is required`,
   la même erreur apparaissant sur une entrée pourtant déjà listée), alors que
   l'état `valid: true` est conservé sans lui. La veille alerte désormais si
   `index.active` passe à faux ou si la validation échoue.
4. **Preuve de valeur** : publier un exemple « agent qui paie » (curl x402 v1/v2
   et client MCP) dans la documentation ; c'est gratuit et c'est ce que les
   crawlers ne trouvent pas aujourd'hui.

---

## 6. Routine

- **Chaque matin** : lire le digest (5 min) ; si alerte paiement/release, ouvrir
  le lien.
- **Chaque lundi** : comparer les 7 derniers digests : nouveaux agents, conversion
  `/validate`, appels MCP → paiement, revenu cumulé.
- **Chaque mois** : décider d'un canal gratuit à ajouter (annuaire, exemple,
  intégration framework) et mesurer son effet.

## 7. Limites assumées
- GitHub : flux Atom public, sans quota ; un dépôt renommé ou supprimé est écrit
  « indisponible ».
- Les nœuds Base publics peuvent limiter : le rapport écrit « on-chain
  indisponible » au lieu d'un faux zéro, et réessaie sur un nœud de repli.
- Bazaar n'est pas garanti public : l'erreur est écrite, pas masquée.
- Le digest ne remplace pas un vrai APM payant : il mesure le seul funnel qui
  compte (découverte → 402 → paiement → revenu) à coût nul.
