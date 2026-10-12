# Prospection — mode d'emploi

Ce dossier contient l'infrastructure de prospection de **Address Validator**
(https://kder-ai.com) : identification des prospects, génération des emails
personnalisés, export vers un outil d'envoi, suivi des réponses, analyse des
résultats, et un CRM local pour piloter l'ensemble.

**Ce que ces outils font :** préparer, mesurer, et tenir à jour un fichier de
prospects et un journal d'événements, propre et daté.

**Ce que ces outils ne font pas :** ils n'envoient rien tout seuls, ils ne
scrapent aucun réseau social (LinkedIn est bloqué et l'interdit), et ils
n'inventent jamais une adresse électronique. L'annuaire public des entreprises
donne le nom, le SIREN, la ville et parfois le dirigeant — jamais l'adresse de
contact. Celle-ci se relève sur le site de la boutique ou se saisit à la main.
C'est le prix d'une liste propre.

---

## 1. La stratégie en une page

| Élément | Valeur |
|---|---|
| Cible | e-commerçants français (Shopify, WooCommerce, PrestaShop…) réalisant au moins 100 commandes par mois |
| Secteurs | mode, high-tech, décoration, cosmétiques, sport (voir `config/settings.yaml`) |
| Volume visé | **100 prospects qualifiés par semaine** |
| Argument | 22 % des échecs de livraison viennent d'une adresse inexacte ; un échec coûte 17 à 30 $ ; la validation coûte 0,02 $ |
| Cadence | introduction, puis relance J+3 et J+7 — pas plus |
| Plafond d'envoi | 50 messages par jour au départ (délivrabilité) |
| Mesure | taux d'ouverture, taux de réponse, conversion, revenu, par secteur et par objet (A/B) |

Le raisonnement : une adresse fausse coûte cher au commerçant, et le coût de la
vérification est ridicule à côté. Le message ne vend pas un outil, il pose une
question et propose une démonstration sans engagement.

---

## 2. Arborescence

```
prospection/
├── README.md                      ce mode d'emploi
├── BENCHMARKS.md                  chiffres attendus, outils et prix, points de vigilance
├── requirements.txt               dépendances (PyYAML ; le reste est la bibliothèque standard)
├── .gitignore                     aucune donnée de tiers dans le dépôt (RGPD)
├── config/
│   ├── settings.yaml              LA source de vérité : secteurs, seuils, cadence, RGPD
│   └── prompts/
│       ├── email_intro.txt        gabarit du premier email (avec son objet)
│       ├── email_followup.txt     gabarit de relance
│       ├── linkedin_message.txt   message LinkedIn (sans objet)
│       └── signature.txt          bloc de signature (un seul endroit à corriger)
├── data/
│   ├── prospects.csv              liste vide, en-tête prêt à remplir
│   ├── prospects_exemple.csv      5 prospects fictifs (exemple complet)
│   ├── exemple_reponses.csv       export de réponses fictif, au format d'un outil d'envoi
│   ├── tracking.json              journal lisible de ce que chaque script a fait
│   └── prospects.db               base SQLite locale (hors dépôt : données de tiers)
├── scripts/
│   ├── common.py                  socle partagé : chemins, réglages, base, réseaux, affichage
│   ├── sourcer_prospects.py       identifie des entreprises (annuaire public DINUM/INSEE)
│   ├── import_prospects.py        importe un CSV, valide, déduplique, enrichit
│   ├── generate_emails.py         génère les emails personnalisés + 3 objets (A/B)
│   ├── export_campaign.py         exporte vers Instantly / Lemlist / Mailshake / SMTP
│   ├── envoyer_smtp.py            envoi direct en SMTP, simulation par défaut
│   ├── track_responses.py         suit les réponses, refus et conversions
│   ├── analyze_results.py         analyse par secteur, objet, jour, heure ; rapport HTML
│   └── verifier_prospection.py    oracle : vérifie toute la chaîne (98 contrôles, 104 avec --reseau)
└── reports/                       rapports générés (import, suivi, analyse) — hors dépôt
```

---

## 3. Les 5 étapes, de bout en bout

Tout se lance depuis la racine du dépôt (`address-validator\`), avec le venv du
projet :

```powershell
cd C:\Users\kder_\mon-projet-agent\address-validator
$py = ".\.venv\Scripts\python.exe"
```

```powershell
# 1. SOURCER — 100 entreprises actives et diffusibles (annuaire public, sans clé)
& $py prospection\scripts\sourcer_prospects.py --secteur mode --departement 75 --limite 100

# 2. COMPLÉTER — ajouter site web et adresse de contact (voir §4.2), puis importer
& $py prospection\scripts\import_prospects.py --input prospection\data\sourcing_20261010.csv
& $py prospection\scripts\import_prospects.py --input prospection\data\sourcing_20261010.csv --decouvrir-emails --enrichir

# 3. GÉNÉRER — 50 emails personnalisés, 3 objets A/B
& $py prospection\scripts\generate_emails.py --secteur mode --limit 50

# 4. ENVOYER — installer le fichier dans l'outil (voir §6), ou l'envoyer soi-même
& $py prospection\scripts\export_campaign.py --campagne prospection\data\campagne_20261010.csv --outil instantly

# 5. SUIVRE puis ANALYSER — l'export de l'outil, puis la mesure
& $py prospection\scripts\track_responses.py --input prospection\data\responses_20261017.csv
& $py prospection\scripts\analyze_results.py --jours 30
```

Chaque script affiche ce qu'il fait, écrit un rapport horodaté dans
`prospection\reports\` et **dit ce qu'il a refusé** (motif par motif). Aucun
script n'invente une donnée manquante : il l'annonce.

**Premier essai à blanc** (5 prospects fictifs fournis, aucune donnée réelle) :

```powershell
Remove-Item prospection\data\prospects.db* -Force -ErrorAction SilentlyContinue
& $py prospection\scripts\import_prospects.py --input prospection\data\prospects_exemple.csv
& $py prospection\scripts\generate_emails.py --sortie prospection\data\campagne_exemple.csv
& $py prospection\scripts\analyze_results.py
```

---

## 4. Le détail des scripts

### 4.1 `sourcer_prospects.py` — trouver les entreprises

Source : l'API publique **Recherche d'entreprises** (DINUM/INSEE), sans clé ni
compte. Le script filtre sur les entreprises **actives** et **diffusibles**
(`statut_diffusion = O`) : une entreprise qui a demandé à ne pas figurer n'est
jamais reprise. Il croise les codes NAF du secteur avec les tranches d'effectif
(PME de 10 à 199 salariés), page par page, avec une pause entre deux requêtes.

```powershell
& $py prospection\scripts\sourcer_prospects.py --secteur cosmetiques --departement 974 --limite 50 --dry-run
& $py prospection\scripts\sourcer_prospects.py --secteur mode --departement 75 --naf 47.91B --tranches 11,12
```

Sortie : `data/sourcing_<date>.csv`, avec **exactement** les colonnes de
l'import, plus `ville`, `siren` et le dirigeant (`prenom`, `nom`) quand la source
le publie — utile pour personnaliser l'email. Les colonnes `site_web` et `email`
restent **vides** : l'annuaire ne les publie pas (voir §4.2).

### 4.2 `import_prospects.py` — entrer les prospects

```powershell
& $py prospection\scripts\import_prospects.py --input prospection\data\prospects.csv --secteur mode
& $py prospection\scripts\import_prospects.py --input prospection\data\prospects.csv --dry-run
& $py prospection\scripts\import_prospects.py --input prospection\data\prospects.csv --decouvrir-emails --enrichir
```

| Option | Effet |
|---|---|
| `--input` | CSV source (colonnes : `nom_boutique`, `email` obligatoires) |
| `--secteur` | code de secteur appliqué à toutes les lignes (prioritaire sur la colonne) |
| `--dry-run` | simulation : aucune écriture (base en mémoire si absente) |
| `--enrichir` | visite la page d'accueil pour nommer la plateforme (réseau) |
| `--decouvrir-emails` | lit les pages de contact publiques quand l'email manque (réseau) |
| `--limite`, `--db`, `--rapport` | plafond de lignes, base cible, chemin du rapport JSON |

Ce que l'import garantit : en-tête refusé en bloc s'il ne correspond pas, adresses
jetables rejetées, doublons écartés (même adresse ; ou même domaine **et** même
nom de boutique), statut déjà avancé jamais écrasé, et un rapport JSON qui liste
chaque refus avec son motif. Rejouer le même import ne réécrit rien (idempotence
vérifiée par `verifier_prospection.py`).

### 4.3 `generate_emails.py` — écrire les emails

```powershell
& $py prospection\scripts\generate_emails.py --secteur mode --limit 50
& $py prospection\scripts\generate_emails.py --template email_followup --limit 50 --sortie prospection\data\relance_20261017.csv
& $py prospection\scripts\generate_emails.py --limit 5 --dry-run
```

Le script ne prend que les prospects `nouveau` jamais préparés, détecte le produit
(nom de la boutique, domaine, et la page d'accueil avec `--enrichir`), écrit une
phrase spécifique **uniquement si elle est vérifiable**, rend le gabarit, et
vérifie qu'aucun `{placeholder}` ne subsiste (sinon la ligne est écartée et
comptée). Il produit trois objets d'A/B testing et affecte à chaque adresse une
variante **stable** (indexée sur sha256) : ré-exporter ne redistribue pas les
groupes en cours de test.

Colonnes du fichier : `email, prenom, nom_boutique, objet_1, objet_2, objet_3,
corps_email`, puis les colonnes de fusion (`variante`, `objet_retenu`, `produit`,
`accroche`, `secteur`, `plateforme`, `site_web`, `ville`, `campagne`).

Générer **n'est pas envoyer** : le statut du prospect ne change pas, seul son
marquage de campagne est posé. Un fichier de campagne peut donc être relu,
corrigé ou jeté.

### 4.4 `export_campaign.py` — mettre en forme pour un outil d'envoi

```powershell
& $py prospection\scripts\export_campaign.py --campagne prospection\data\campagne_20261010.csv --outil instantly
& $py prospection\scripts\export_campaign.py --campagne prospection\data\campagne_20261010.csv --outil lemlist
& $py prospection\scripts\export_campaign.py --campagne prospection\data\campagne_20261010.csv --outil mailshake
& $py prospection\scripts\export_campaign.py --campagne prospection\data\campagne_20261010.csv --outil smtp
& $py prospection\scripts\export_campaign.py --campagne prospection\data\campagne_20261010.csv --outil generique
```

Pour chaque outil, dans `data/export_<outil>_<date>/` :

1. `campagne_<outil>.csv` — le fichier d'import, avec les en-têtes attendus par l'outil ;
2. `sequence.json` — la séquence complète : introduction J+0, relances J+3 et J+7
   (cadence lue dans `settings.yaml`), objets et corps rendus ;
3. `instructions_<outil>.md` — la procédure d'import, écran par écran.

Les relances sont rendues **au moment de l'export** depuis
`config/prompts/email_followup.txt` : corriger le gabarit de relance puis
ré-exporter suffit, sans regénérer la campagne.

### 4.5 `envoyer_smtp.py` — envoyer soi-même (simulation par défaut)

```powershell
# 1. simulation : montre exactement ce qui partirait, sans connexion
& $py prospection\scripts\envoyer_smtp.py --campagne prospection\data\campagne_20261010.csv

# 2. envoi réel : identifiants dans l'environnement de la session, jamais dans un fichier
$env:PROSPECTION_SMTP_HOST = "smtp.exemple.fr"
$env:PROSPECTION_SMTP_PORT = "587"
$env:PROSPECTION_SMTP_USER = "contact@kder-ai.com"
$env:PROSPECTION_SMTP_PASSWORD = "..."
& $py prospection\scripts\envoyer_smtp.py --campagne prospection\data\campagne_20261010.csv --confirmer --limite 20
```

Garde-fous, tous vérifiés par l'oracle : refus de démarrer sans les trois
variables d'environnement ; refus d'un expéditeur sur un domaine grand public
(gmail, orange…), sauf `--accepter-domaine-public` ; plafond quotidien de
`settings.yaml` ; pause entre deux messages ; jamais deux fois le même prospect ;
en-têtes `Message-ID`, `Reply-To` et `List-Unsubscribe` posés ; mise à jour du CRM
après chaque envoi réussi (`statut=envoye`, horodatage, événement `intro`).

`--relance` n'écrit qu'aux prospects déjà contactés **sans réponse**.

### 4.6 `track_responses.py` — suivre les réponses

```powershell
# export de l'outil d'envoi (Instantly, Lemlist, Mailshake, export maison)
& $py prospection\scripts\track_responses.py --input prospection\data\responses_20261017.csv

# suivi manuel (une réponse arrivée dans la boîte mail)
& $py prospection\scripts\track_responses.py --marquer contact@ma-boutique-montres.fr:repondu
& $py prospection\scripts\track_responses.py --marquer hello@techgadget.fr:converti --revenu 0.50
& $py prospection\scripts\track_responses.py --marquer hello@sport-loisirs.fr:envoye --etape relance_1

# lecture seule
& $py prospection\scripts\track_responses.py --synthese
```

Le script traduit le vocabulaire des outils (`sent`, `opened`, `replied`,
`bounced`, `unsubscribed`…), **fait progresser** les statuts sans jamais reculer
(importer un vieil export après un récent n'efface rien), horodate les étapes, et
consigne chaque fait dans le journal. Un rebond dur devient un refus définitif :
l'adresse n'existe plus, elle ne sera plus jamais écrite. Réimporter le même
export ne change plus rien et ne double pas le revenu déclaré.

Codes de retour : `0` réussi, `1` rien appliqué (fichier illisible ou aucune
adresse reconnue), `2` usage invalide.

### 4.7 `analyze_results.py` — mesurer et décider

```powershell
& $py prospection\scripts\analyze_results.py                 # tout l'historique + rapport HTML
& $py prospection\scripts\analyze_results.py --jours 30       # 30 derniers jours
& $py prospection\scripts\analyze_results.py --ascii --json prospection\reports\analyse.json
```

Sortie : un résumé ASCII (secteurs, objets, variantes, plateformes, jours) et un
rapport HTML **autonome** (`reports/analyse_<date>.html`) — aucune ressource
externe, il s'ouvre hors ligne. L'analyse croise par secteur, objet, variante,
plateforme, jour de semaine et heure, puis compare les objets deux à deux par un
**test à deux proportions** (statistique z, seuil 1,96). Sous 20 envois par
variante, elle **refuse de conclure** et l'écrit : désigner un « meilleur objet »
sur huit envois ferait changer de gabarit pour rien.

### 4.8 `verifier_prospection.py` — prouver que ça marche

```powershell
& $py prospection\scripts\verifier_prospection.py            # tout, hors réseau (98 contrôles)
& $py prospection\scripts\verifier_prospection.py --reseau    # + sourcing public (104 contrôles)
```

L'oracle fabrique une base temporaire, importe les 5 prospects d'exemple, génère
la campagne, vérifie l'idempotence de chaque étape, exporte vers les 5 formats,
applique l'export de réponses fictif, analyse, puis interroge les routes du CRM
(mot de passe absent, mauvais, bon, base manquante comprise). Il n'écrit rien
dans le dépôt et rend `VERIF PROSPECTION OK n/n` (code 0) ou la liste des échecs
(code 1). C'est la commande à lancer après toute modification de ce dossier.

Si le dossier est copié **hors du dépôt**, les routes du CRM ne sont plus
joignables (elles vivent dans `app/`) : l'oracle l'annonce au lieu de planter, et
sa dernière ligne porte la mention —
`VERIF PROSPECTION OK 84/84 (ignoré : routes du CRM /prospection (app/ absent))`.
Aucun contrôle n'est jamais compté comme réussi sans avoir été exécuté.

---

## 5. Le CRM `/prospection`

L'API du projet sert une page de suivi, protégée par le **même couple**
`DASHBOARD_USER` / `DASHBOARD_PASSWORD` que `/dashboard` :

```powershell
# sur le poste, avec un mot de passe dans l'environnement de la session
$env:DASHBOARD_PASSWORD = "un-mot-de-passe"
& $py -m uvicorn app.main:app --port 8000
# puis http://127.0.0.1:8000/prospection
```

| Route | Rôle |
|---|---|
| `GET /prospection` | la page : statistiques, filtres, tableau, marquage |
| `GET /prospection/api/prospects` | liste filtrée (JSON) — `?secteur=&statut=&q=&depuis=&limite=` |
| `GET /prospection/api/stats` | compteurs et taux (JSON) |
| `POST /prospection/api/prospects/{id}/statut` | change un statut (`{"statut": "repondu"}`) |
| `GET /prospection/export.csv` | export CSV de la sélection courante |

Trois règles, héritées de `/dashboard` (D32) : **fermé par défaut** (`503` si
`DASHBOARD_PASSWORD` est vide), **autonome** (ni CDN, ni police distante, ni
script tiers — la CSP borne le script à ce même service), **honnête** (un taux
sans dénominateur s'affiche « — », jamais 0 %).

La base lue est `PROSPECTION_DB` (`prospection/data/prospects.db` par défaut). Ce
dossier n'est **pas** déployé sur le VPS : en production, `/prospection` répond
`503` en donnant la commande à lancer sur le poste. C'est volontaire — le CRM
manipule des coordonnées de tiers et n'a rien à faire sur un serveur public.

Marquer un statut depuis le CRM écrit aussi le fait correspondant dans le journal
(`reponse`, `conversion`, `refus`, `desinscription`), pour que l'analyse voie la
même réalité que le tableau.

---

## 6. Envoyer : avec quoi ?

Prix relevés le **12/10/2026** (à revérifier avant de souscrire) :

| Outil | Prix indicatif | Ce qui compte pour ce dossier |
|---|---|---|
| **`envoyer_smtp.py`** (fourni ici) | 0 € d'abonnement | simulation par défaut, plafond quotidien, `List-Unsubscribe`, CRM mis à jour ; demande un SMTP authentifié (Brevo, OVH, Google Workspace…) |
| **Instantly.ai** | Growth 47 $/mois ; offres groupées à partir d'environ 85 $/mois | comptes d'envoi et **warmup illimités**, rotation, A/B testing d'objet — le plus simple pour démarrer froid |
| **Lemlist** | Email 55 $/mois (engagement annuel) ou 69 $/mois ; Multichannel 87–109 $/mois | séquences multicanal (email + LinkedIn), warmup inclus, base de leads en option |
| **Mailshake** | Starter 25–29 $/mois (1 adresse, 1 500 envois/mois) ; Email Outreach 45–49 $/mois | warmup illimité et A/B testing dès l'entrée de gamme, très sobre |
| **Brevo** (ex-Sendinblue) | offre gratuite (300 emails/jour) ; offre payante à partir de 5 000 emails/mois | un **SMTP** propre pour `envoyer_smtp.py`, sans outil de séquence |

Trois conseils de fond : n'écris pas depuis une adresse grand public (le script
refuse), publie SPF/DKIM/DMARC sur le domaine d'envoi, et monte en volume
progressivement (10 à 20 messages par jour la première semaine, pas 500 le
premier jour). Le plafond `envois_max_par_jour` de `settings.yaml` est là pour ça.

---

## 7. RGPD et conformité

### Ce que dit le cadre

La CNIL distingue deux situations (page « La prospection commerciale par courrier
électronique », mise à jour le 10 juin 2026) :

* **particuliers (B2C)** : **consentement préalable** obligatoire (libre,
  spécifique, éclairé, univoque) ;
* **professionnels (B2B)** : pas de consentement préalable, mais la personne doit
  être **informée** et pouvoir **s'opposer** simplement. C'est le cas de cette
  campagne : des adresses professionnelles publiées par des commerces.

Textes : article **L34-5 du CPCE**, articles **6**, **7** et **21** du RGPD.
Chaque sollicitation doit permettre d'identifier l'émetteur et d'exprimer un
refus par un moyen simple.

### Ce que le dossier fait déjà

| Exigence | Comment c'est tenu |
|---|---|
| Émetteur identifiable | signature (`config/prompts/signature.txt`) dans chaque message |
| Refus simple et gratuit | phrase de désinscription ajoutée automatiquement en bas de chaque email (`settings.yaml` → `emails.mention_desinscription`) + en-tête `List-Unsubscribe` (RFC 8058) |
| Refus définitif | statuts `refuse` et `desinscrit` : exclus de toute campagne par construction, jamais réécrits par un import |
| Minimisation | annuaire public **diffusible** + pages de contact publiques ; aucune donnée sensible, aucun achat de fichier |
| Pas de données dans le dépôt | `prospection/.gitignore` : **tout** `data/` est exclu (liste blanche, base et journaux compris), sauf les 4 pièces d'exemple ; les rapports générés aussi |
| Information des personnes | base légale déclarée dans `settings.yaml` (`rgpd.base_legale`), sources listées |
| Conservation limitée | `rgpd.retention_annees: 3`, **appliquée** par `track_responses.py --purger-avant AAAA-MM-JJ --confirmer` |

```powershell
# appliquer la conservation de 3 ans (dernier contact antérieur à la date donnée)
& $py prospection\scripts\track_responses.py --purger-avant 2023-10-10          # liste seule
& $py prospection\scripts\track_responses.py --purger-avant 2023-10-10 --confirmer
```

### Ce qui reste à la charge de Kder

* **Répondre aux droits** (accès, effacement, opposition) dans le délai d'un
  mois : le CRM permet de retrouver une fiche par son adresse, `--marquer
  email:desinscrit` clôt un refus, `--purger-avant` efface des données.
* **Tenir un registre des traitements** minimal (un tableau suffit) : finalité,
  base légale, catégories de données, destinataires (l'outil d'envoi), durée.
* **Vérifier les mentions** : information sur le site, politique de
  confidentialité à jour, contrat de sous-traitance avec l'outil d'envoi.
* **Ne jamais** réimporter un fichier acheté, et ne pas fusionner cette base avec
  un fichier client : la finalité n'est pas la même.

### Sanctions encourues

Le RGPD permet à la CNIL des amendes administratives allant **jusqu'à 20 millions
d'euros ou 4 % du chiffre d'affaires annuel mondial** (le montant le plus élevé
étant retenu), et le CPCE prévoit en outre des **sanctions pénales** pour la
prospection électronique irrégulière. La CNIL sanctionne régulièrement des
campagnes de prospection : ce n'est pas un risque théorique.

---

## 8. Bonnes pratiques

**Délivrabilité (avant le contenu)**

* Publier **SPF, DKIM et DMARC** sur le domaine d'envoi avant le premier message.
  Sans cela, le message part directement en indésirables, quelle que soit sa qualité.
* Écrire en **texte brut**, sans pièce jointe, sans lien raccourci, sans image de
  suivi. Un email de prospection qui ressemble à une infolettre est traité comme tel.
* **Monter en volume** : 10 à 20 messages par jour la première semaine. Un domaine
  neuf qui envoie 200 messages d'un coup est un domaine brûlé.
* Surveiller le taux de **rebonds** : au-delà de 2 %, la liste est à revoir avant
  d'écrire à d'autres prospects. Un rebond dur devient un refus définitif
  (`track_responses.py`), donc jamais de deuxième envoi vers une adresse morte.

**Contenu**

* Une seule question par email. Le but du premier message est une réponse, pas une vente.
* Les chiffres cités doivent être vrais : 22 % d'échecs liés à l'adresse, 17 à 30 $
  par échec, 0,02 $ par validation (cf. `/manifest` de l'API). Pas d'arrondi flatteur.
* Signer, et proposer une démonstration **sans engagement** : c'est ce qui
  transforme une question en échange.
* Deux relances maximum (J+3, J+7). Au-delà, le rendement s'effondre et le risque
  de plainte monte.

**Suivi**

* Importer l'export de l'outil **chaque semaine**, pas à la fin du mois : les taux
  et l'A/B testing n'existent que si le journal est tenu.
* Une réponse dans la boîte mail se marque **le jour même** (`--marquer`), sinon
  elle est perdue dans l'analyse.
* Décider sur des effectifs : l'analyse refuse de conclure sous 20 envois par variante.
  Dix réponses valent mieux que mille impressions.

---

## 9. Erreurs à éviter

| Erreur | Pourquoi c'est grave |
|---|---|
| Acheter une liste d'adresses | vente de données, CNIL, et la base achetée appartient au vendeur, pas à toi |
| Écrire à `info@` pour 500 boutiques d'un coup | taux de réponse ridicule, plaintes, et le domaine en paie le prix |
| Laisser un `{placeholder}` dans un email | l'automate se voit immédiatement (le script écarte ces lignes, mais un gabarit mal édité reste possible) |
| Promettre un chiffre non démontrable | un commerçant qui vérifie perd confiance, et l'argument tombe |
| Relancer un refus ou un désinscrit | les scripts l'interdisent par construction — ne pas contourner avec un export « nettoyé » |
| Envoyer 300 emails le premier jour | le domaine part en listes noires ; se chauffer progressivement |
| Garder les données « au cas où » | la conservation de 3 ans est une limite, pas un conseil : `--purger-avant` |
| Mélanger prospection et exploitation | d'où le dossier séparé et la base locale : les données de prospection ne montent pas sur le VPS |

---

## 10. Vérifier, dépanner

```powershell
# la vérification complète (à lancer après toute modification de ce dossier)
& $py prospection\scripts\verifier_prospection.py            # 98 contrôles, hors réseau
& $py prospection\scripts\verifier_prospection.py --reseau    # + sourcing public
```

| Symptôme | Cause probable | Geste |
|---|---|---|
| `GENERATION FAIL aucun prospect éligible` | tous les prospects sont déjà préparés ou terminaux | `--reprendre` pour ré-exporter, sinon importer de nouvelles fiches |
| `SUIVI FAIL … adresse invalide` | faute de frappe dans `--marquer` | corriger la saisie ; les adresses inconnues ne sont jamais devinées |
| `ENVOI FAIL identifiants SMTP absents` | variables `PROSPECTION_SMTP_*` non posées dans la session | les poser (§4.5), puis relancer |
| `/prospection` répond `503` | `DASHBOARD_PASSWORD` vide, ou `PROSPECTION_DB` absent | poser le mot de passe, créer la base par un import |
| `/prospection` répond `401` | mauvais couple identifiant/mot de passe | c'est le même que `/dashboard` |
| `IMPORT FAIL colonnes obligatoires absentes` | en-tête du CSV non conforme | l'erreur affiche l'en-tête lue : la comparer à `data/prospects_exemple.csv` |
| Rapports HTML illisibles | page ouverte par un outil qui retire le CSS | ouvrir dans un navigateur : la page est autonome, sans ressource externe |

Les rapports de chaque passage sont dans `prospection\reports\` :
`import_<horodatage>.json`, `suivi_<date>.md`, `analyse_<date>.html`. Ils sont
hors dépôt (données de prospection) mais restent lisibles localement — c'est ce
qui permet de comprendre ce qui s'est passé trois semaines plus tard.

L'état courant se lit aussi à l'œil nu dans `data/tracking.json` : chaque script y
laisse une ligne (date, action, chiffres), y compris après un `rm` accidentel de
la base.





