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
incorrecte, sur une pièce fragile, c'est un aller-retour, un colis à réexpédier et
un client qui attend — c'est exactement ce que ce service vérifie avant l'envoi.

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

## 3. L'adresse du destinataire — à compléter par Kder

| Champ | Valeur |
|---|---|
| Boutique | Tiitoune Art (boutique Shopify, `tiitouneart.re`, La Réunion) |
| Email du destinataire | **à compléter ici avant envoi** : `________________@____________` |
| Nom du contact (si connu) | `________________` |
| Langue | français |
| Pièce jointe | aucune (le guide est en ligne sur `/guide`, l'email y renvoie) |

L'adresse n'est **pas** écrite dans ce document tant qu'elle n'est pas connue :
inventer ou supposer une adresse revient à envoyer une offre commerciale à
quelqu'un d'autre. La relever sur la boutique (`tiitouneart.re`, page contact) ou
dans le CRM de prospection (`prospection/data/prospects.db`, `prospection/scripts`).

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

- [ ] les 5 liens mesurés (tableau § 1, codes `200/200/401/200/200`) ;
- [ ] un essai réel sur `/validate-form` avec une adresse de test de La Réunion ;
- [ ] l'objet et le pré-en-tête copiés **exactement** (§ 2) ;
- [ ] le bloc `curl` relu : `X-API-Key`, `POST /validate`, aucune clé réelle ;
- [ ] l'adresse du destinataire vérifiée (§ 3), relue deux fois.

## 5. Suivi après envoi

| Échéance | Action | Où le noter | Fait le |
|---|---|---|---|
| **J+0** | Envoyer l'email (§ 2), puis relever le rapport d'appels de la semaine | `suivi_clients.py` | `____/____` |
| **J+3** | Si aucune réponse : relance courte (2 phrases), en répondant au même fil, avec le même lien `/guide` | fil email d'origine | `____/____` |
| **J+7** | Si toujours rien : appel téléphonique ; sinon visite physique (Kder est à La Réunion, la boutique est sur l'île) — un essai `402`/`degraded` s'explique mieux de vive voix | fiche prospect | `____/____` |
| **J+14** | Clôture ou relance finale : si le compte existe (`customers`), lire son usage réel et clore proprement (un « non » daté vaut mieux qu'un fil qui traîne) | `suivi_clients.py --format csv` | `____/____` |

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
