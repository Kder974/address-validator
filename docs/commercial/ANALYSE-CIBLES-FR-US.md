# Analyse des cibles — France métropolitaine et US cross-border

> Décision validée le 20/10/2026 par Kder : on cible **la France
> métropolitaine** et les **États-Unis en angle cross-border**, avec les
> **agences et 3PL** comme canal. **Les DOM-TOM sont hors périmètre** pour
> l'instant (ni prospection, ni argumentaire dédié).

| | |
|---|---|
| Décision | France métropolitaine + US cross-border + agences/3PL |
| Hors périmètre | DOM-TOM (suspendus), marché US « pur » (concurrence directe Smarty/Google) |
| Budget sourcing | **0 €** au départ : scraping web et sources publiques (pas d'outil payant) |
| Rythme | 100 prospects qualifiés/semaine, 50 messages/jour maximum ([BENCHMARKS.md](../../prospection/BENCHMARKS.md)) |
| Offre | Starter 29 €/mois pour 2 000 validations ; Pro 79 €/mois pour 10 000 ; à l'appel 0,02 $/validation pour les agents |

---

## 1. Pourquoi ces deux marchés

### Le problème est chiffré et universel
D'après Shippo : **~2,1 % des colis e-commerce ont un problème d'adresse** ;
une correction UPS/FedEx coûte **23,50 à 24 $ par colis** ; 30 % des échecs
viennent d'une adresse incomplète et 28 % d'une adresse inexistante ; la
validation réduit ces problèmes de **48 %**
([Shippo](https://goshippo.com/blog/real-cost-address-errors-in-shipping)).
Notre propre argumentaire, utilisé dans `prospection/settings.yaml` : 22 % des
échecs sont liés à l'adresse et un échec coûte **17 à 30 $**.

### Notre avantage produit, pays par pays
- **France** : la **BAN** (Base Adresse Nationale) valide gratuitement notre
  couverture, et nous en tirons une adresse corrigée, un score et un géocodage.
  Le marché parle français, la prospection est déjà outillée (annuaire INSEE), et
  la concurrence (Google Address Validation, Loqate, PostGrid) est chère ou
  orientée US-only.
- **US cross-border** : nous validons **US + FR dans la même API**. Une marque US
  qui livre en France a besoin des deux : l'adresse US du client (DPV + RDI
  résidentiel/commercial) *et* l'adresse française. SmartyStreets ne fait que
  l'US ; Google fait les deux mais cher et sans logique de livraison.
- **Agences/3PL** : une intégration = des dizaines de boutiques. Elles
  revendent déjà de la logistique et ont le problème d'adresse à chaque colis.

### Ce qu'on ne fait pas
- **DOM-TOM** : plus de requête `La Réunion` dans les emails de prospection ;
  la niche BAN outre-mer reste techniquement servie par l'API, mais elle n'est
  pas un axe d'acquisition pour l'instant.
- **US-only** : on ne va pas frontalement contre SmartyStreets / Google / Shippo
  sur leur cœur de marché. L'angle US est **la livraison vers la France** et les
  **partenaires**.

---

## 2. Cibles France métropolitaine

### 2.1 Quatre secteurs prioritaires (configurés dans `settings.yaml`)

| Secteur | Pourquoi il nous veut | Exemples à qualifier | Codes NAF (sourcing INSEE) |
|---|---|---|---|
| **Mode / Prêt-à-porter** | Panier élevé, retours fréquents, clients internationaux, adresses d'appartement | Sézane, Le Slip Français, Asphalte, Faguo, Atelier Particulier | 47.91B, 47.71Z, 47.72A |
| **Cosmétiques / Beauté** | Abonnements, produits fragiles, livraison à domicile | Typology, Respire, Oh My Cream | 47.91B, 47.75Z |
| **Haute technologie / Gadgets** | Volume, SAV coûteux, expéditions suivies | LDLC, Materiel.net, Fnac | 47.91B, 47.41Z, 47.43Z |
| **Maison / Décoration** | Livraison sur rendez-vous, objet volumineux, adresse exacte critique | Maisons du Monde, Westwing | 47.91B, 47.53Z, 47.59B |

### 2.2 Profil recherché (ICP)
- **Taille** : PME, **≥ 100 commandes/mois**, jusqu'à ~10 000 commandes/mois.
- **Plateforme** : Shopify, WooCommerce, PrestaShop (détection url/HTML dans
  `settings.yaml`).
- **France métropolitaine** : siège/entrepôt en métropole ; l'international est
  un bonus (clients US à valider, argument supplémentaire).
- **Douleur vérifiable** : page livraison/retours publique, mention
  d'envoi international, avis clients évoquant des colis perdus ; sinon,
  boutique bien établie (≥ 3 ans) avec catalogue réel.
- **Contact** : une adresse publiée sur le site (page contact → `mailto:` ou
  texte), jamais devinée.

### 2.3 Offre à proposer
Proposer un **essai sans engagement sur leurs propres adresses** puis le forfait
mensuel : « jusqu'à 2 000 validations pour 29 €/mois », au lieu de 0,02 $/appel.
Le calcul à mettre dans le premier message : une boutique à 1 000 commandes/mois
subit ~21 colis à problème (~500 $/mois de coût évité) → l'offre coûte ~30 €.

---

## 3. Cibles US cross-border

### 3.1 Trois sous-segments

| Sous-segment | Pourquoi | Comment les identifier |
|---|---|---|
| **Marques DTC US livrant en France/UE** | Elles reçoivent des adresses FR mal saisies ; nous validons FR et US | mention « ships worldwide » / « France » dans la livraison, site multilingue, checkout international |
| **Marques US avec boutique FR** (.fr, pages en français) | Leur client FR saisit une adresse FR ; elles ont besoin de BAN | traductions FR, devise EUR, page « Livraison en France » |
| **3PL / agences US tournées vers l'Europe** | Elles ont le problème à chaque expédition ; une intégration = plusieurs boutiques | annuaires d'agences, partenaires Shopify Plus, fulfilment centers |

Exemples à qualifier (pas des cibles validées avant contrôle) : Allbirds,
Glossier, Brooklinen, Caraway, Our Place, Ridge, Away, Huel, Death Wish Coffee.

### 3.2 Argument spécifique US
> « Vos clients français saisissent mal leurs adresses ; les transporteurs
> échouent ou facturent la correction. Nous validons **US AND FR** addresses
> dans une seule API, avec la même réponse JSON. »

Le prix reste l'argument de simplicité : 0,02 $/validation ; offre mensuelle
Starter/Pro pour les volumes.

### 3.3 Concurrence et positionnement
| Concurrent | Couverture | Notre différence |
|---|---|---|
| SmartyStreets | US uniquement | FR incluse |
| Google Address Validation | US + international, prix à la requête plus élevé | moins cher, réponse orientée livraison |
| Loqate / PostGrid | international, contrats entreprise | self-service, sans compte, API simple |
| Shippo (validation) | US/thématique logistique | là aussi nous couvrons FR nativement |

---

## 4. Agences et 3PL — le canal à effet de levier

| Type | Pourquoi ils achètent | Exemples à cibler (France + US) |
|---|---|---|
| **Agences Shopify/WooCommerce** | Intègrent l'API chez leurs clients, facturent l'intégration | agences Shopify Plus, partenaires WooCommerce |
| **3PL / fulfilment** | Validation à l'entrée de chaque commande, volume | ShipBob, ShipMonk, 3PL européens |
| **Transporteurs / courtiers** | Réduire les adresses invalides en amont | la Poste/Colissimo (partenaires), DHL, UPS |
| **SaaS checkout / livraison** | Intégration unique, diffusion large | éditeurs d'autocomplete, apps de livraison |

Un partenariat vaut dix boutiques : c'est le canal qui mérite le plus de temps
après les 100 premiers prospects directs.

---

## 5. Méthode de Sourcing — scraping web (budget 0 €)

Pas d'outil payant pour l'instant (pas de BuiltWith, Storeleads, Apollo,
Hunter). On utilise des **sources publiques** et le **moteur `Agent Web`**
(`prospection/scripts/rechercher_prospects.py`, préparé sans l'exécuter) :

### 5.1 Chaîne de collecte
1. **Requêtes de moteur** : `site:myshopify.com [mot-clé] France`,
   `site:myshopify.com "livraison en France"`, ou en anglais
   `site:myshopify.com "ships to France"` (cf. `requetes_web` de chaque secteur).
2. **Résultats** : lecture de la page publique du moteur (DuckDuckGo HTML par
   défaut ; Google possible en option, avec la limite de ses robots.txt), puis
   extraction et normalisation des URLs (`myshopify.com` inclus).
3. **Déduplication** : un domaine = un prospect ; exclusion des annuaires,
   réseaux sociaux, marketplaces et domaines non publics.
4. **Visite mesurée** : page d'accueil puis pages contact publiques, **une
   requête par page**, `robots.txt` respecté, User-Agent identifié, pause de
   2 s entre sites, taille de page bornée.
5. **Emails publiés seulement** : liens `mailto:` **et** textes contenant `@`,
   filtrés par `valider_email` (pas de reconstruction, pas de trouvaille
   inventée) ; sinon le prospect reste « à compléter », il n'est pas écarté.
6. **Sortie** : CSV au format `import_prospects.py` (colonnes
   `nom_boutique, site_web, email, secteur, plateforme, pays, source, notes`),
   prêt pour l'import, la base locale et la génération d'emails.

### 5.2 Règles et limites (à dire, pas à cacher)
- **Légalité** : on ne lit que des pages publiques, sans login, sans formulaire,
  sans contournement ; les réseaux sociaux sont exclus (LinkedIn l'interdit) ;
- **robots.txt** et débit poli (pas de rafale, délai de 2–3 s, identification
  `AddressValidator-prospection/1.0`) ;
- **Google** peut renvoyer un captcha ou un consentement : le script doit le
  dire et basculer sur DuckDuckGo/Bing ; le scraping massif de Google n'est pas
  un plan viable à long terme ;
- **Taux de succès attendu** : ~30–50 % des boutiques n'exposent pas d'email en
  clair → elles restent « à compléter » ; on ne les contacte pas sans adresse ;
- **RGPD/CNIL France** : intérêt légitime B2B, information, désinscription
  immédiate, conservation 3 ans ; **CAN-SPAM (US)** : opt-out dans chaque email,
  pas d'objet trompeur, identité et adresse réelles ;
- **Pas de données personnelles scrapées** : on relève uniquement le contact
  professionnel publié par la boutique.

### 5.3 Sources publiques complémentaires (gratuites)
- **France** : annuaire des entreprises (DINUM/INSEE) déjà branché par
  `sourcer_prospects.py`, pages « où nous trouver », FEVAD (membres),
  ECF (fédération e-commerce), annuaires de créateurs, magazines e-commerce
  (LSA, E-Commerce Nation), Instagram/TikTok publics (uniquement pour le nom et
  le site, pas pour l'email).
- **US** : pages « retailers » de marques DTC, annuaires d'agences Shopify
  (répertoire Shopify Partners), listes publiques de marques DTC (2PM, Modern
  Retail, NRF), annuaires de 3PL, pages « worldwide shipping ».

### 5.4 Qualité > volume
Chaque prospect passe par la grille de qualification :
**volume estimé** (catalogue/avis/réseaux), **plateforme détectée**,
**international vérifié**, **email trouvé sur le site**, **secteur connu**.
Un lot de 100 prospects dont 40 ont un email propre vaut mieux que 500 sans
contact : les rebonds coûtent la réputation du domaine.

---

## 6. Grille de priorisation

Score sur 100 = douleur (40) + volume (25) + facilité de contact (20) +
couverture produit (15).

| Critère | 0 | 1 | 2 | 3 | Poids |
|---|---|---|---|---|---|
| Douleur adresse | aucune mention | page livraison/retours | avis colis perdus | international + volume | ×4 |
| Volume estimé | < 100 cmds/mois | 100–500 | 500–2 000 | > 2 000 | ×3 |
| Contact | aucun email | formulaire seul | email générique | email nominatif | ×2 |
| Couverture | US-only ou FR-only | FR métropole | US + FR | US + FR + agence | ×1,5 |

Les fiches **< 40** ne sont pas contactées ; **40–65** en nurture ; **> 65**
dans la campagne de la semaine.

---

## 7. Plan d'exécution proposé

| Phase | Quoi | Cible | Mesure |
|---|---|---|---|
| **Phase 0 (fait)** | Analyse, config secteurs FR + US, script Agent Web prêt, emails FR/US | — | ce document |
| **Phase 1 (semaine 1-2)** | Lancer l'Agent Web France (4 secteurs) + compléter les emails, importer dans le CRM | **100 prospects FR** avec email vérifié | taux email trouvé, sectorisation |
| **Phase 2 (semaine 2)** | Identifier 20 agences/3PL FR + US, message partenaire | 20 comptes | entretiens |
| **Phase 3 (semaine 3-4)** | Agent Web US cross-border (requêtes EN + FR), emails en anglais | **50 prospects US** | réponses, essais |
| **Phase 4** | Campagne email (50/j max) + relances J+3/J+7, A/B objets | 150 contacts | taux d'ouverture/réponse, 1er client |

### KPIs
- prospects **avec email publié** par lot (objectif ≥ 40 %),
- taux de réponse (cible 1–5 %, [BENCHMARKS.md](../../prospection/BENCHMARKS.md)),
- taux de boutiques qui essaient l'API sur leurs adresses,
- conversion en Starter/Pro, puis MRR.

---

## 8. Décisions à prendre (rappel)
1. Priorité d'exécution : **Phase 1 France** puis Phase 3 US, ou l'inverse ?
2. Nombre d'envois/jour au démarrage (10 ou 50) et domaine d'envoi (Brevo actuel ?).
3. Périmètre agences : France seulement d'abord, ou FR + US simultanément ?
4. Faut-il brancher `email_us.txt` sur `generate_emails.py` (sélection par
   `langue_email` du secteur) dans un second temps ? (aujourd'hui : templates de
   référence dans `docs/commercial/prospection/`).
