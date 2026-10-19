# Interface humaine — crédits prépayés par Stripe (brief technique)

| | |
|---|---|
| **Destinataire** | Claude (Fzed), pour implémentation |
| **Branche dédiée** | `fonctionnalite/interface-stripe` (rebasée sur `main` = `911667b`) |
| **Auteur du brief** | agent DSH, 19/10/2026 |
| **Périmètre** | achat de crédits de validation par carte bancaire (Stripe Checkout) pour l'**interface humaine**, utilisables avec la clé API d'un compte |
| **Statut** | spécification à implémenter — **aucun secret réel dans ce document** |

> ⚠️ **Sécurité** : ce document ne contient que des **placeholders**. Toute clé
> Stripe réelle vit dans `.env` sur le serveur (ignoré par git : `*.env`), jamais
> dans le dépôt, jamais dans un journal. Utiliser uniquement des clés de **mode
> test** (`sk_test_…`) tant que Kder n'a pas validé le passage en production.

> **Numérotation des décisions** : ce brief utilise **D88**. Les décisions
> **D83 à D87** sont déjà attribuées par les PR ouvertes #41 à #44 et #46
> (comparatif en monnaie de la page, adresse corrigée, RDI, adresse sur une
> ligne, accueil animé) ; ne pas les réutiliser.

---

## 1. Objectif et ce qui existe déjà

Le service a aujourd'hui **deux voies de paiement** :

1. **Agents autonomes** : paiement x402 à l'appel (0,02 USDC), sans compte — D34/D77. **Ne pas y toucher.**
2. **Humains** : abonnement par clé API (`X-API-Key`) — D44/D45/D46 — mais **facturé manuellement**, aucune carte n'est branchée (voir `app/exploitation.py` : « abonnements à facturer, pas encaissés automatiquement »).

Le besoin : permettre à un client connecté d'**acheter un pack de validations par carte** (Stripe Checkout, mode `payment`), puis de consommer ces **crédits** avec sa clé API quand son quota mensuel est épuisé.

### Ce qu'il faut réutiliser, pas réinventer

| Existant | Fichier | À réutiliser |
|---|---|---|
| Session web (cookie `__Host-av_session`) | `app/routes/connexion.py` (D62) | authentification des routes `/compte/*` |
| Protection des formulaires (en-tête `Origin`) | `connexion.origine_etrangere` | tout `POST` de l'interface |
| Page compte + régénération de clé | `app/routes/signup.py` (`GET /compte`, `POST /compte/cle`) | y ajouter la carte « Crédits » |
| Quotas mensuels + consommation | `app/accounts.py` (`PLANS`, `quota_for`, `month_start`, `plan_status`, `consumption`) | source de vérité des quotas |
| Refus de quota (402 `quota_atteint`) | `app/routes/validate.py` (`serve_with_api_key`, motif D82 `quota`) | y brancher la consommation de crédits |
| Tarifs en euros (vérité de facturation) | `app/accounts.py` (`OFFRES`, D57) | cohérence des prix affichés |
| Config typée | `app/config.py` (`Settings`, pydantic-settings) + `.env.example` | variables Stripe |
| DDL idempotent au démarrage | `app/database.py` (`DDL`, `init_db`), miroirs `app/models.py` | création des 2 tables |
| Discipline du dépôt | `DECISIONS.md`, `GATES.md`, `scripts/decisions_check.py`, `scripts/run_tests.py` | D88 + porte + oracle |

---

## 2. Routes API des crédits

Toutes les routes « humaines » sont **localisées** comme l'existant (`/compte`
en français, `/en/compte` en anglais, etc. — voir `i18n.chemin`). Les chemins
ci-dessous sont donnés en français ; enregistrer la version localisée pour chaque
langue si la route rend du HTML.

| Méthode | Chemin | Auth | Rôle |
|---|---|---|---|
| `GET` | `/compte` | session (cookie D62) | page compte : ajouter une carte « Crédits » (solde, historique court, boutons d'achat) |
| `GET` | `/compte/credits` | session | **JSON** : solde + historique détaillé (le gabarit peut l'appeler, et Claude peut tester facilement) |
| `POST` | `/compte/credits/acheter` | session + `Origin` | crée une **session Stripe Checkout** pour un `pack_id` |
| `GET` | `/compte/credits/succes?session_id=…` | session | page de retour Stripe (succès) — **ne crédite rien** |
| `GET` | `/compte/credits/annule` | session | page de retour Stripe (annulation) |
| `POST` | `/stripe/webhook` | **signature Stripe** | source de vérité unique des achats/remboursements |
| `GET` | `/pricing` (+ 5 autres langues) | public | afficher les packs (prix = `accounts.PACKS`) |

### Contrats détaillés

#### `POST /compte/credits/acheter`

Corps accepté : formulaire HTML (`application/x-www-form-urlencoded`) **ou** JSON
(`application/json`), toujours `{"pack_id": "pack_5"}` / `pack_id=pack_5`.

- **Succès formulaire** : `303 See Other` vers `checkout_url` (redirection du navigateur vers Stripe).
- **Succès JSON** : `200` :
  ```json
  {
    "pack_id": "pack_5",
    "validations": 5,
    "amount_cents": 149,
    "currency": "eur",
    "checkout_url": "https://checkout.stripe.com/c/pay/cs_test_VOTRE_SESSION",
    "session_id": "cs_test_VOTRE_SESSION"
  }
  ```
- **Erreurs** (JSON `{"error": "…", "message": "…"}`) :
  - `400 pack_inconnu` — `pack_id` absent, inconnu ou pack désactivé ;
  - `401 non_connecte` — pas de session valable ;
  - `403 origine` — `Origin`/`Referer` étranger (formulaire) ;
  - `429 trop_de_tentatives` — même protection que les autres formulaires ;
  - `503 stripe_indisponible` — Stripe injoignable ou prix non configuré (fail-closed).

#### `GET /compte/credits`

```json
{
  "customer_id": 42,
  "plan": "starter",
  "plan_restant": 1987,
  "credits": 5,
  "currency": "validations",
  "transactions": [
    {"date": "2026-10-19T09:00:00Z", "delta": 5, "reason": "achat", "pack_id": "pack_5", "amount_cents": 149},
    {"date": "2026-10-19T09:12:00Z", "delta": -1, "reason": "validation"}
  ]
}
```

#### `POST /stripe/webhook`

- En-tête obligatoire `Stripe-Signature`, vérifié avec `STRIPE_WEBHOOK_SECRET`
  (constante de temps, jamais de comparaison naïve).
- Événements gérés a minima : `checkout.session.completed`,
  `payment_intent.succeeded` (si utilisé), `charge.refunded`.
- **Idempotent** : un événement rejoué (Stripe réessaie) renvoie `200` sans
  recréditer. La base porte l'unicité (`stripe_event_id`, `payment_intent_id`).
- Répondre vite (`200`) après commit ; une erreur base → `5xx` pour que Stripe
  réessaie. Un `GET` sur cette route ne fait rien (`405`).

---

## 3. Format des données

### Catalogue des packs — source de vérité côté serveur

Ajouter dans `app/accounts.py` (même logique que `OFFRES` : la page lit le code) :

```python
#: Packs de crédits prépayés (D88). Prix en **centimes d'euro** (entiers : pas de
#: flottant sur de l'argent). `stripe_price_id` seul fait foi chez Stripe.
PACKS = (
    {"id": "pack_5",   "validations": 5,   "prix_eur_cents": 149,  "devise": "eur"},
    {"id": "pack_25",  "validations": 25,  "prix_eur_cents": 590,  "devise": "eur"},
    {"id": "pack_100", "validations": 100, "prix_eur_cents": 1990, "devise": "eur"},
)
#: Mapping id de pack → identifiant de prix Stripe (env, placeholders en mode test).
PRIX_STRIPE = {
    "pack_5": "price_TEST_VOTRE_PACK_5",
    "pack_25": "price_TEST_VOTRE_PACK_25",
    "pack_100": "price_TEST_VOTRE_PACK_100",
}
```

Les **montants proposés ci-dessus sont à valider par Kder** : c'est une décision
commerciale (positionnement entre l'abonnement à ~0,015 €/validation et le x402 à
0,02 $/appel). Le brief impose seulement : **entiers en centimes**, `pack_id`
whitelisté, prix jamais fourni par le client.

### Exemples de requêtes

```bash
# Formulaire (navigateur) :
curl -i -X POST https://kder-ai.com/compte/credits/acheter \
  -H "Cookie: __Host-av_session=VOTRE_SESSION" \
  -d 'pack_id=pack_5'

# JSON (tests, Claude) :
curl -X POST https://kder-ai.com/compte/credits/acheter \
  -H "Cookie: __Host-av_session=VOTRE_SESSION" \
  -H "Content-Type: application/json" \
  -d '{"pack_id": "pack_5"}'
```

### Paramètres serveur de la session Stripe Checkout

```python
params = {
    "mode": "payment",
    "line_items": [{"price": PRIX_STRIPE[pack["id"]], "quantity": 1}],
    "customer_email": client.email,          # pré-remplissage, pas une identité de paiement
    "client_reference_id": str(client.id),
    "metadata": {
        "customer_id": str(client.id),
        "pack_id": pack["id"],
        "validations": str(pack["validations"]),
    },
    "success_url": f"{base}/compte/credits/succes?session_id={{CHECKOUT_SESSION_ID}}",
    "cancel_url": f"{base}/compte/credits/annule",
    "locale": i18n.LANGUE_STRIPE.get(lang, "auto"),
}
```

> Le montant **encaissé** est celui du prix Stripe ; la métadonnée sert au
> contrôle de cohérence côté webhook (`amount_total == prix_eur_cents`), pas à
> faire confiance au client.

### Événement webhook utile (extrait minimal)

```json
{
  "id": "evt_TEST_VOTRE_EVENEMENT",
  "type": "checkout.session.completed",
  "data": {
    "object": {
      "id": "cs_test_VOTRE_SESSION",
      "payment_intent": "pi_TEST_VOTRE_INTENT",
      "payment_status": "paid",
      "amount_total": 149,
      "currency": "eur",
      "client_reference_id": "42",
      "metadata": {"customer_id": "42", "pack_id": "pack_5", "validations": "5"}
    }
  }
}
```

---

## 4. Logique de quota et de crédits

### Ordre de service d'un appel `POST /validate` avec `X-API-Key`

1. **Quota mensuel du plan** (existant, D45) : s'il reste des validations ce mois
   calendaire UTC (`accounts.plan_status(...)["restant"] > 0`), servir et
   enregistrer la consommation comme aujourd'hui (`customer_validations`).
2. **Sinon, solde de crédits > 0** : débiter **1 crédit** et servir.
3. **Sinon** : `402` avec l'erreur `quota_atteint` (comportement actuel), motif
   D82 `quota` inchangé.

Cas à trancher avec Kder (proposé ci-dessous, à confirmer) : un compte dont
l'**essai est expiré** peut-il utiliser ses crédits ? Proposition : **oui** —
les crédits sont un droit payé, indépendant de la date d'essai ; la réponse
HTTP reste `200` et la consommation est tracée.

### Débit atomique (argent obligé)

La consommation d'un crédit ne peut pas être un `if balance > 0` non atomique :

```sql
UPDATE credit_balance
   SET balance = balance - 1, updated_at = CURRENT_TIMESTAMP
 WHERE customer_id = :customer_id AND balance >= 1
RETURNING balance;
```

- `rowcount == 1` → crédit réservé, l'appel est servi ; insérer une ligne
  `credit_transactions(delta = -1, reason = 'validation')`.
- `rowcount == 0` → plus de crédits → refus `quota_atteint`.
- Si le service échoue **après** la réservation (5xx interne), compenser par
  `credit_transactions(delta = +1, reason = 'correction')` dans le même chemin
  d'erreur — jamais de crédit perdu sur panne serveur.

### Achat (webhook seulement)

1. Vérifier la signature (`Stripe-Signature` + `STRIPE_WEBHOOK_SECRET`).
2. Whitelister `type` ; ignorer en `200` tout autre événement.
3. Retrouver le pack par `metadata.pack_id` — **jamais** par le montant.
4. Vérifier `currency == "eur"` et `amount_total == pack["prix_eur_cents"]` ;
   incohérence → **ne rien créditer**, logguer sans secret, répondre `200`
   (événement traité : Stripe ne doit pas réessayer un événement invalide de
   façon permanente) ou `400` selon la politique retenue — proposer `200` +
   alerte exploitation.
5. Insérer la transaction d'achat avec `stripe_event_id` et
   `payment_intent_id` (UNIQUE) puis mettre à jour `credit_balance` ; les deux
   dans **une seule transaction SQL**. Un rejeu = conflit d'unicité → `200`
   sans double crédit.

### Remboursement

- `charge.refunded` → transaction négative (`reason = 'remboursement'`,
  `amount_cents` remboursé, `stripe_event_id` UNIQUE).
- Si le crédit a déjà été consommé, le solde peut devenir **négatif** (dette du
  compte). Proposition : autoriser le négatif et l'afficher clairement sur
  `/compte` ; à confirmer avec Kder.

### Règles affichées (doivent être vraies dans le code)

- Crédits **sans expiration**, non transférables, non remboursables une fois
  consommés (sauf geste commercial manuel via transaction `correction`).
- Le solde de crédits est **distinct** du « restant du mois » du plan ; les deux
  sont affichés séparément, sans jamais les additionner dans un même compteur.
- L'essai gratuit du formulaire (10/IP/jour) et le x402 sont **inchangés** par
  les crédits.

---

## 5. Tables PostgreSQL

À ajouter dans `app/database.py` (chaîne `DDL`, style existant : `SERIAL`,
`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, tout est idempotent
et rejoué au démarrage par `init_db`) **et** en miroir SQLAlchemy dans
`app/models.py` (relation vers `Customer`, mêmes types).

```sql
-- D88 : crédits prépayés (Stripe). Deux tables : le solde courant (lecture
-- rapide, verrouillage d'une ligne) et le grand livre (jamais réécrit : tout
-- mouvement y laisse une ligne, y compris corrections et remboursements).
CREATE TABLE IF NOT EXISTS credit_balance (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- Le négatif est possible après un remboursement d'un crédit déjà consommé :
-- c'est une dette du compte, jamais masquée.

CREATE TABLE IF NOT EXISTS credit_transactions (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    delta INTEGER NOT NULL CHECK (delta <> 0),
    reason VARCHAR(20) NOT NULL,             -- achat | validation | remboursement | correction
    pack_id VARCHAR(20),
    amount_cents INTEGER,                    -- ce qui a été payé (achat/remboursement)
    currency VARCHAR(3) NOT NULL DEFAULT 'eur',
    stripe_event_id VARCHAR(64) UNIQUE,      -- idempotence webhook
    stripe_session_id VARCHAR(64),
    payment_intent_id VARCHAR(64) UNIQUE,    -- idempotence paiement
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_customer
    ON credit_transactions(customer_id);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_created
    ON credit_transactions(created_at);
```

Notes d'implémentation :

- Bien vérifier en base le `CHECK`/`UNIQUE` : c'est la protection réelle contre
  le double crédit, pas un test applicatif.
- `credit_balance` doit pouvoir être créé à la volée au premier achat
  (`INSERT … ON CONFLICT (customer_id) DO UPDATE`), et lu à `0` s'il n'existe pas.
- Ne pas exposer `stripe_event_id` / `payment_intent_id` dans l'API publique.
- Le DDL est rejoué à chaque démarrage : ne jamais y mettre de `DROP` ni de
  `UPDATE` destructif.

---

## 6. Configuration et secrets

Ajouter dans `app/config.py` (`Settings`) :

```python
#: Paiement par carte (D88). Clés Stripe : voir .env.example, placeholders seuls.
stripe_secret_key: str = ""
stripe_publishable_key: str = ""
stripe_webhook_secret: str = ""
stripe_api_base: str = "https://api.stripe.com"
```

Dans `.env.example` (placeholders uniquement, **jamais de valeur réelle**) :

```dotenv
# Paiement par carte — crédits (D88). Mode test de Stripe obligatoire au début.
STRIPE_SECRET_KEY=sk_test_VOTRE_CLE
STRIPE_PUBLISHABLE_KEY=pk_test_VOTRE_CLE
STRIPE_WEBHOOK_SECRET=whsec_TEST_VOTRE_SECRET

# Identifiants de prix Stripe des packs (créés dans le dashboard Stripe, mode test).
STRIPE_PRICE_PACK_5=price_TEST_VOTRE_PACK_5
STRIPE_PRICE_PACK_25=price_TEST_VOTRE_PACK_25
STRIPE_PRICE_PACK_100=price_TEST_VOTRE_PACK_100
```

Règles :

- `.env` est ignoré (`*.env`), vérifiable par `git check-ignore -v .env` ;
- **aucune clé réelle** dans le dépôt ni dans les tests ; les tests Stripe
  utilisent un faux transport HTTP (mocks) et les placeholders ci-dessus ;
- route d'achat **désactivée proprement** si `STRIPE_SECRET_KEY` ou un prix
  manque : `503 stripe_indisponible`, pas de crash ni de paiement à moitié créé ;
- ne jamais journaliser la clé secrète ni le corps complet d'un webhook (il peut
  contenir e-mail et données de facturation).

---

## 7. Fichiers à créer / modifier (carte du chantier)

| Fichier | Action |
|---|---|
| `app/accounts.py` | `PACKS`, `PRIX_STRIPE`, fonctions `consommer_credit`, `crediter_achat`, `solde_credits`, `historique_credits` |
| `app/routes/credits.py` (nouveau) | routes `/compte/credits*` + `POST /stripe/webhook` (ou webhook dans un module dédié) |
| `app/routes/signup.py` | brancher la carte crédits dans la page `/compte` (ou déléguer au nouveau module) |
| `app/routes/validate.py` | ordre quota → crédits → refus dans `serve_with_api_key` |
| `app/config.py`, `.env.example` | variables Stripe (placeholders) |
| `app/database.py`, `app/models.py` | `credit_balance`, `credit_transactions` (DDL + miroirs, style D82) |
| `app/templates/compte/<langue>.html` (+ `app/pilotage_page.py` si besoin) | affichage solde/historique/boutons, textes dans les six langues |
| `app/templates/pricing/<langue>.html`, `app/routes/pricing.py` | packs sur `/pricing` (prix issus de `accounts.PACKS`) |
| `tests/test_credits.py` (nouveau) | voir §8 |
| `scripts/decisions_check.py` | oracle `d88` + `range(1, 89)` |
| `DECISIONS.md`, `GATES.md` | ligne **D88**, porte `D88` avec `CHECK`/`EXPECT` |
| `README.md` | section paiement par carte (si le brief le prévoit) |

---

## 8. Tests et discipline du dépôt (non négociable)

Avant de pousser, Claude doit :

1. **Tests** (`tests/test_credits.py`) au minimum :
   - achat : webhook `checkout.session.completed` crédite 5, crée la ligne
     `achat`, met `credit_balance` à 5 ;
   - **rejeu du même événement** → toujours 5, pas de double crédit ;
   - **montant falsifié** (`amount_total` ≠ prix du pack) → aucun crédit, réponse
     gérée, aucune exception non maîtrisée ;
   - **signature invalide/absente** → `400`, aucun crédit ;
   - `pack_id` inconnu → `400`, aucune session Stripe créée ;
   - quota épuisé → 1 crédit consommé, `200` ; sans crédit → `402 quota_atteint` ;
   - crédits et quota du plan **ne se mélangent pas** dans les compteurs affichés ;
   - page `/compte` des six langues affiche solde + historique (test i18n) ;
   - packs sur `/pricing` = `accounts.PACKS` (test « le prix montré est le prix
     appliqué ») ;
   - **aucune clé réelle** dans le code/tests (test de recherche de motif
     `sk_live_`/`sk_test_` dans les fichiers versionnés).
2. **Oracle** `d88` dans `scripts/decisions_check.py` : vérifie les routes
   déclarées, les deux tables dans le DDL, les placeholders dans `.env.example`,
   l'ordre quota→crédits, l'idempotence, et la présence de `D88` dans
   `DECISIONS.md`.
3. **Porte** `D88` dans `GATES.md` (`CHECK` + `EXPECT`), puis :
   - `scripts/run_tests.py` → pytest + gate vert ;
   - `scripts/decisions_check.py --all` → **88/88** ;
   - `gate-check.mjs --approve GATES.md` → `ALL MET`.
4. **Style de commit** : `address-validator: <résumé> (D88) - <détail>`.
5. **Branche** : tout reste sur `fonctionnalite/interface-stripe` ; **ne pas
   fusionner dans `main` sans l'accord explicite de Kder** — PR attendue, revue
   par Kder après recette.
6. **Docs vraies** : tout prix/route promis doit exister et répondre ; si un
   pack est affiché, il doit être achetable (sinon ne pas l'afficher).

---

## 9. Définition de terminé (checklist)

- [ ] `credit_balance` et `credit_transactions` créées par `init_db`, miroirs
      SQLAlchemy présents, rejeu du DDL idempotent.
- [ ] `POST /compte/credits/acheter` crée une vraie session **Stripe test**
      (ou `503` propre si non configuré) et redirige le navigateur.
- [ ] `POST /stripe/webhook` signature vérifiée, idempotent, crédite une seule
      fois, montant recalculé côté serveur.
- [ ] Avec clé API : quota plan → crédit → `402 quota_atteint`, sans double
      débit en concurrence.
- [ ] `/compte` (6 langues) affiche le solde et l'historique ; `/pricing`
      affiche les packs aux prix de `accounts.PACKS`.
- [ ] Aucun secret réel commité ; `.env.example` en placeholders.
- [ ] `pytest` vert, `decisions_check --all` 88/88, `GATES` ALL MET.
- [ ] Compte rendu (README/`DEPLOIEMENT_VPS.md` si déploiement) : ce qui est
      montré est ce qui est appliqué.

---

## 10. Questions ouvertes — à valider par Kder avant de coder

1. **Prix des packs** et TVA : les montants proposés (1,49 € / 5,90 € / 19,90 €
   TTC) sont-ils les bons ? Faut-il Stripe Tax (TVA UE calculée à la caisse) ?
2. **Crédits et essai expiré** : un compte dont l'essai est terminé peut-il
   utiliser ses crédits (proposé : oui) ?
3. **Remboursement d'un crédit déjà consommé** : solde négatif autorisé
   (proposé : oui, affiché), ou blocage manuel ?
4. **Expiration** : crédits sans date d'expiration (proposé), ou 12 mois ?
5. **Abonnement par carte récurrent** (Stripe Billing) : ce chantier le
   prépare-t-il, ou reste-t-il hors périmètre ?
6. **Stripe live** : passage en production uniquement sur accord explicite de
   Kder, après recette en mode test.

---

## 11. Références utiles dans le dépôt

- `app/accounts.py` (D44/D45/D57) — comptes, quotas, tarifs.
- `app/routes/signup.py` — page `/compte` et clé API.
- `app/routes/validate.py` — chemin `serve_with_api_key` (quota, 402).
- `app/routes/connexion.py` — sessions, cookie `__Host-av_session`, `origine_etrangere`.
- `app/database.py` — DDL idempotent, `ALTER TABLE … IF NOT EXISTS` (D82).
- `app/models.py` — miroirs SQLAlchemy.
- `DECISIONS.md` D40 (recette ≠ revenu), D44-D46 (comptes, quotas, clé API),
  D57 (tarifs = vérité), D71 (tableau de bord), D82 (motifs de refus).
- `GATES.md` + `scripts/decisions_check.py` — porte et oracle de la future D88.
