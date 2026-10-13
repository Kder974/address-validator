# Checklist d'envoi — Tiitoune Art (premier client, campagne D45-D50)

État au 13/10/2026. Ce document est un **mode opératoire d'envoi**, pas un
argumentaire : il dit quoi vérifier, dans quel ordre, et quoi faire si rien ne
revient. L'application tourne en production sur le VPS (`87.76.197.38`, service
systemd `address-validator`, publié sous `https://kder-ai.com` par Nginx).

Les valeurs citées ici (essai, quota, prix, en-têtes) sont celles du code
(`app/accounts.py`, `app/routes/pricing.py`, `app/routes/validate.py`) : l'email de
bienvenue (`address-validator/EMAIL_BIENVENUE.md`, D49) et le rapport d'appels
(`app/suivi.py`, D50) les lisent à la même source. **Aucune valeur n'est recopiée
de mémoire.**

## 1. Les 5 liens à vérifier avant envoi

| # | Lien | Ce qu'on attend | Mesuré le 13/10/2026 |
|---|---|---|---|
| 1 | https://kder-ai.com/pricing | page publique : offre, prix et plafonds lus dans la configuration | **200** (12 685 o) |
| 2 | https://kder-ai.com/signup | inscription self-service ; la clé s'affiche **une fois** | **200** (6 845 o) |
| 3 | https://kder-ai.com/compte | **401** sans identifiants : l'espace client est privé | **401** (124 o) |
| 4 | https://kder-ai.com/validate-form | essai gratuit, 10 par IP et par 24 h | **200** (7 026 o) |
| 5 | https://kder-ai.com/guide | guide d'intégration (Shopify, WooCommerce, import CSV, appel direct) | **200** (23 049 o) |

Contrôle à relancer **le jour de l'envoi** (les tailles ci-dessus sont celles du
13/10/2026, après déploiement du commit `6997f1d`) :

```powershell
foreach ($u in '/pricing','/signup','/compte','/validate-form','/guide') {
  $r = curl.exe -s -o NUL -w "%{http_code}|%{size_download}|%{time_total}" "https://kder-ai.com$u"
  "{0,-15} {1}" -f $u, $r
}
```

**Revérifié le jour du contrôle** (`curl -s -o NUL -w "%{http_code}|%{size_download}|%{time_total}"`,
et `scripts/envoyer_email_tiitoune.py --verifier-liens`) :

| Lien | Code | Taille | Temps (curl) |
|---|---|---|---|
| `/pricing` | **200** | 12 685 o | 3,16 s |
| `/signup` | **200** | 6 845 o | 1,81 s |
| `/compte` | **401** | 124 o | 8,18 s |
| `/validate-form` | **200** | 7 026 o | 1,64 s |
| `/guide` | **200** | 23 049 o | 2,36 s |

Tailles **identiques** à celles du déploiement `6997f1d` : le service publié est bien
celui décrit ici, et `/compte` exige toujours l'authentification.

Attendu : `200`, `200`, **`401`**, `200`, `200`. Un `200` sur `/compte` serait une
**régression de confidentialité**, pas une bonne nouvelle : la page doit exiger
l'authentification. Et un `404` sur l'une des quatre autres pages signifierait que
le déploiement n'a pas été fait — dans ce cas, ne pas envoyer l'email.

## 2. L'email à envoyer — exemplaire Tiitoune Art (version 2)

Copie fidèle de `address-validator/EMAIL_BIENVENUE.md` § 2. Aucune clé n'y figure :
le texte renvoie à `/signup`, où elle s'affiche une seule fois.

Objet : `Votre accès à la validation d'adresses est prêt — Tiitoune Art`
Pré-en-tête : `Votre clé API, 100 validations offertes, puis 29 €/mois — sans engagement.`

```text
Bonjour,

Merci pour votre intérêt : votre compte Tiitoune Art est ouvert. Une adresse
incorrecte, c'est un aller-retour, un colis à réexpédier et une cliente qui
attend — c'est exactement ce que ce service vérifie avant l'envoi.

VOTRE CLÉ API
Créez votre compte (ou retrouvez-le) sur https://kder-ai.com/signup : la clé
s'affiche une seule fois, au moment de l'inscription. Gardez-la dans un
gestionnaire de mots de passe ou dans la configuration de votre boutique — jamais
dans un fichier partagé. Perdue ? Elle se régénère en un clic depuis
https://kder-ai.com/compte, et l'ancienne cesse de fonctionner immédiatement.

CE QUE COMPREND VOTRE OFFRE
14 jours et 100 validations offertes, sans carte bancaire. Ensuite : Starter
29 €/mois (2 000 validations) ou Pro 79 €/mois (10 000), selon votre volume. Aucun
prélèvement automatique : rien n'est prélevé sans votre accord écrit.

COMMENT L'ENVOYER
La clé va dans l'en-tête X-API-Key de POST https://kder-ai.com/validate :

  curl -sS https://kder-ai.com/validate \
    -H "X-API-Key: LA_CLE_AFFICHEE_A_L_INSCRIPTION" \
    -H "content-type: application/json" \
    -d '{"address":{"street":"1 Rue de Paris","city":"Saint-Denis",
         "state":"RE","zip":"97400","country":"RE"}}'

CE QUE VOUS VERREZ DE VOTRE CONSOMMATION
Votre espace (https://kder-ai.com/compte) affiche les validations servies, les
adresses invalides détectées et une estimation d'économies avec sa formule. À
chaque appel, X-Quota-Remaining dit ce qu'il vous reste ; le quota est mensuel et
repart le 1er du mois.

AVANT D'INTÉGRER
Testez une adresse sans compte sur https://kder-ai.com/validate-form, puis suivez
le guide d'intégration (Shopify, WooCommerce, import CSV, appel direct) :
https://kder-ai.com/guide. Il n'y a aucune application Shopify à installer :
l'appel vient de votre service.

CE QUE NOUS GARDONS
Aucune adresse en base : seulement l'empreinte SHA-256 de l'adresse (transmise sous
le nom cache_key dans chaque réponse) et l'issue. Pour faire effacer une entrée, il
suffit de nous envoyer le cache_key.

Une question, un blocage ? Répondez à cet email : il arrive directement chez moi.

Bien à vous,
Kder — Address Validator
https://kder-ai.com
```

## 3. L'adresse du destinataire — relevée sur la boutique (13/10/2026)

| Champ | Valeur |
|---|---|
| Boutique | Tiitoune Art (boutique Shopify, `tiitouneart.re`, La Réunion) |
| Email du destinataire | **`contact@tiitouneart.re`** — relevé et vérifié le 13/10/2026 sur trois sources (§ 3.1) ; adresse secondaire connue : `tiitouneart@gmail.com` (CGV, art. 18-19) |
| Nom du contact (si connu) | non publié : les mentions légales nomment l'exploitant « Dimassi Said Zaitoune » (prénom d'usage inconnu) — l'email s'ouvre donc par « Bonjour » |
| Langue | français |
| Pièce jointe | aucune (le guide est en ligne sur `/guide`, l'email y renvoie) |

L'adresse est écrite ici parce qu'elle a été **relevée** sur la boutique (page contact
et mentions légales) et déclarée par Shopify — puis enregistrée dans le CRM de
prospection, jamais supposée : inventer une adresse reviendrait à envoyer une offre
commerciale à quelqu'un d'autre.

### 3.1 D'où vient l'adresse (relevé du 13/10/2026)

| Élément | Valeur | Où elle a été lue |
|---|---|---|
| Adresse principale | `contact@tiitouneart.re` | page « Nous Contacter » (`https://www.tiitouneart.re/pages/nous-contacter`, réponse `200`) et mentions légales |
| Déclaration par la boutique | `customerServiceEmail: contact@tiitouneart.re` | JSON Shopify de la page contact |
| Adresse secondaire | `tiitouneart@gmail.com` | CGV, articles 18 et 19 |
| Formulaire de contact | `https://www.tiitouneart.re/pages/nous-contacter` | page publique (`200`) |
| Téléphone | aucun numéro public | mentions légales et page contact |
| Plateforme | **Shopify** (308 occurrences de `shopify`) | pages publiques (`cdn.shopify.com`, `/policies/legal-notice`) |
| Exploitant | Dimassi Said Zaitoune — SIRET 881 449 318 00016 — Saint-Denis 97400 | mentions légales |
| Réseaux | Instagram `tii_toune.art` · Facebook `hennartbyzed` · Snapchat `tiitouneart` | pages publiques |
| Fiche CRM | `prospects.db`, prospect **id 6**, `source = site-officiel`, `secteur = cosmetiques`, `statut = nouveau` | `prospection/scripts/import_prospects.py` (rapport `prospection/reports/import_20261013-094557.json`) |

Ce que la fiche CRM ne contient pas, faute de source publiée : le prénom et le nom du
contact (`prenom_absent`), et le fait que `contact@` est une adresse de rôle
(`role_generique` : acceptée, mais elle répond moins souvent qu'une boîte nominative).
L'email s'ouvre donc par « Bonjour », sans prénom inventé.

## 4. Règle d'envoi — à lire avant de cliquer

> **Ne pas envoyer avant d'avoir vérifié les 5 liens soi-même.**

Trois raisons, toutes vérifiables :

1. les liens remplacent le document : l'email ne contient **aucune** capture ni
   aucun PDF — si une page est `404`, le client clique dans le vide ;
2. l'email promet des valeurs (`14 jours`, `100 validations`, `29 €`, `79 €`,
   `X-API-Key`, `X-Quota-Remaining`) qui viennent du **code déployé** : un service
   qui n'a pas été redémarré depuis le déploiement annonce une offre qu'il ne
   sert pas ;
3. une adresse d'essai sans compte (`/validate-form`) est proposée : elle doit
   réellement répondre, sinon le premier contact est un échec technique.

Contrôle minimal, dans l'ordre, le jour J :

- [x] les 5 liens mesurés le 13/10/2026 (tableau § 1, codes `200/200/401/200/200`) ;
- [ ] un essai réel sur `/validate-form` avec une adresse de test de La Réunion ;
- [x] l'objet et le pré-en-tête copiés **exactement** (§ 2) — le script les relit dans ce document (§ 5.1) ;
- [ ] le bloc `curl` relu : `X-API-Key`, `POST /validate`, aucune clé réelle ;
- [x] l'adresse du destinataire vérifiée (§ 3.1, trois sources), relue deux fois ;
- [ ] un essai d'envoi vers soi-même (`--test`) avant l'envoi réel (`--send`) ;

## 5. Suivi après envoi

| Échéance | Action | Où le noter | Fait le |
|---|---|---|---|
| **J+0** | Envoyer l'email (§ 2), puis relever le rapport d'appels de la semaine | `suivi_clients.py` | `____/____` |
| **J+3** | Si aucune réponse : relance courte (2 phrases), en répondant au même fil, avec le même lien `/guide` | fil email d'origine | `____/____` |
| **J+7** | Si toujours rien : appel téléphonique ; sinon visite physique (Kder est à La Réunion, la boutique est sur l'île) — un essai `402`/`degraded` s'explique mieux de vive voix | fiche prospect | `____/____` |
| **J+14** | Clôture ou relance finale : si le compte existe (`customers`), lire son usage réel et clore proprement (un « non » daté vaut mieux qu'un fil qui traîne) | `suivi_clients.py --format csv` | `____/____` |

### 5.1 Le script d'envoi (le seul chemin vers « envoyer »)

| Étape | Commande |
|---|---|
| Simulation (aucune connexion, défaut) | `address-validator\.venv\Scripts\python.exe address-validator\scripts\envoyer_email_tiitoune.py` |
| Contrôle des 5 liens | `… envoyer_email_tiitoune.py --dry-run --verifier-liens` |
| Essai vers soi-même | `… envoyer_email_tiitoune.py --test --vers <adresse-de-test>` |
| Envoi réel | `… envoyer_email_tiitoune.py --send` |

Le script relit le § 2 de ce document (objet, pré-en-tête, corps) : il ne recopie rien,
donc une correction faite ici change le message envoyé. Il lit ses identifiants dans
l'environnement (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`SMTP_FROM_NAME` ; facultatifs `SMTP_FROM`, `SMTP_REPLY_TO`) : aucun secret n'est écrit
dans le dépôt, et le mot de passe n'est jamais affiché. Il refuse un expéditeur grand
public (gmail/orange/…) sauf `--accepter-domaine-public`, refuse de renvoyer à un
prospect déjà contacté sauf `--renvoyer`, et après un envoi réussi il met le CRM à jour
(`statut = envoye`, `date_envoi`, événement `intro`) — c'est cet horodatage, et non un
souvenir, qui déclenche la relance J+3.

La mesure du suivi, côté service, est le script du suivi commercial (D50) — il lit
`customers` et `customer_validations` et dit **qui** appeler, **pourquoi**, dans
**quel ordre d'urgence** :

```powershell
address-validator\.venv\Scripts\python.exe address-validator\scripts\suivi_clients.py
address-validator\.venv\Scripts\python.exe address-validator\scripts\suivi_clients.py --format csv --sortie suivi.csv
```

## 6. Points de vigilance

- **Pas de prélèvement automatique** : il n'existe pas dans le code. Le passage à
  Starter ou Pro se demande par écrit ; ne jamais laisser entendre le contraire
  dans une relance.
- **Le paiement à l'appel (x402, 0,02 USDC) s'adresse aux agents**, pas à Tiitoune
  Art : une boutique s'abonne par `X-API-Key`. Ne pas mélanger les deux voies dans
  une conversation commerciale.
- **`degraded: true` ne dit rien de l'adresse** (fournisseur injoignable). C'est la
  phrase à répéter si le client rapporte un résultat inattendu.
- **Aucune clé dans un email, un SMS ou une capture** : elle s'affiche une fois à
  l'inscription et se régénère depuis `/compte`.
- **Le compte est le premier test du suivi** : dès que Tiitoune Art s'inscrit, le
  compte apparaît dans `suivi_clients.py` ; c'est ce rapport, et non un souvenir,
  qui décide de la relance à J+3 et de la clôture à J+14.
