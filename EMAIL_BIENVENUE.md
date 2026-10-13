# Email de bienvenue — client de l'API (abonnement par clé API)

Le message d'accueil envoyé à une boutique qui vient de créer son compte. Il existe
ici sous deux formes : le **gabarit** à charger dans n8n (variables de fusion entre
doubles accolades, remplacées à l'envoi) et un **exemplaire rempli** pour le premier
client, Tiitoune Art (`tiitouneart.re`, boutique Shopify à La Réunion).

Trois règles ont dicté le texte :

1. **Aucune clé réelle dans un document**, même privé : une clé s'affiche une fois,
   à l'inscription, et se régénère depuis `/compte`. Un email qui la contiendrait
   ferait de la boîte du destinataire un endroit où elle survit.
2. **Ce que l'email promet est ce que le code applique** : les jours d'essai, le
   quota, les prix et les en-têtes sont ceux de `app/accounts.py`,
   `app/routes/pricing.py` et `app/routes/validate.py` (vérifié par la porte D49).
   Un email qui annonce une offre que le service refuse coûte la confiance du
   premier client.
3. **Aucune promesse que nous ne tenons pas** : pas de témoignage inventé, pas
   d'urgence fabriquée, pas de « facturation automatique » (elle n'existe pas), pas
   de résultats garantis. Et la phrase qui compte pour une boutique : un résultat
   `degraded` **ne dit rien** de l'adresse.

## 1. Le gabarit (n8n / envoi manuel)

Objet : `{{prenom}}, votre accès à la validation d'adresses est prêt`
Pré-en-tête : `Votre clé API, 100 validations offertes, puis 29 €/mois — sans engagement.`

```text
Bonjour {{prenom}},

Votre compte {{boutique}} est ouvert depuis le {{date}}. Voici ce qu'il contient,
et comment le mettre en service aujourd'hui.

VOTRE CLÉ API
Elle s'affiche une seule fois, au moment de l'inscription. Si la fenêtre est
fermée, régénérez-la depuis votre espace client ({{lien_compte}}, avec votre email
et votre mot de passe) : nous ne conservons qu'une empreinte, donc personne — pas
même nous — ne peut relire une clé existante.

CE QUE COMPREND VOTRE OFFRE
{{jours_essai}} jours et {{quota}} validations offertes, sans carte bancaire.
Ensuite : Starter 29 €/mois (2 000 validations) ou Pro 79 €/mois (10 000), selon
votre volume. Aucun prélèvement automatique : le passage à un plan payant se fait
sur simple demande, et rien n'est prélevé sans votre accord écrit.

COMMENT L'ENVOYER
Votre clé se place dans l'en-tête X-API-Key de POST /validate :

  curl -sS https://kder-ai.com/validate \
    -H "X-API-Key: {{cle_api}}" \
    -H "content-type: application/json" \
    -d '{"address":{"street":"1 Rue de Paris","city":"Saint-Denis",
         "state":"RE","zip":"97400","country":"RE"}}'

La réponse contient l'adresse standardisée, la livrabilité, un indice de confiance
et un champ degraded. Si degraded vaut true, le fournisseur d'adresses n'a pas
répondu : la réponse ne dit rien de votre adresse — réessayez plus tard, elle est
re-vérifiée après quelques minutes.

CE QUE VOUS VERREZ DE VOTRE CONSOMMATION
Votre espace ({{lien_compte}}) affiche les validations servies, les adresses
invalides détectées et une estimation d'économies avec sa formule. À chaque appel,
l'en-tête X-Quota-Remaining dit ce qu'il vous reste. Le quota est mensuel
(calendrier UTC) : il repart le 1er du mois.

AVANT D'INTÉGRER
Testez une adresse sans compte sur https://kder-ai.com/validate-form, puis suivez
le guide d'intégration (Shopify, WooCommerce, import CSV, appel direct) :
{{lien_guide}}. Il n'y a aucune application Shopify à installer : l'appel vient de
votre service, ce qui vous laisse régler votre propre seuil de confiance.

CE QUE NOUS GARDONS
Aucune adresse n'est conservée en base. Nous gardons l'empreinte SHA-256 de
l'adresse et l'issue (valide ou non) ; cette empreinte vous est transmise dans
chaque réponse sous le nom cache_key, et elle suffit pour demander un effacement.

Une question, un blocage ? Répondez à cet email : il arrive directement chez moi.

Bien à vous,
Kder — Address Validator
https://kder-ai.com
```

## 2. Exemplaire prêt à envoyer — Tiitoune Art

Sans accolades : les valeurs de l'offre d'essai y sont écrites en clair, et la clé
n'y figure **pas** — le texte renvoie à l'inscription, où elle s'affiche une fois.

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

## 3. Variables de fusion

| Variable | Valeur à l'envoi | Source |
|----------|------------------|--------|
| `{{prenom}}` | prénom du destinataire | saisie commerciale |
| `{{boutique}}` | `shop_name` du compte (« Tiitoune Art ») | `customers.shop_name` |
| `{{date}}` | date d'ouverture du compte | `customers.created_at` |
| `{{jours_essai}}` | `14` | `accounts.JOURS_ESSAI` (`app/config.py: trial_days`) |
| `{{quota}}` | `100` | `accounts.VALIDATIONS_ESSAI` (plan `trial`) |
| `{{cle_api}}` | **jamais une clé** : la phrase qui renvoie à `/signup` ou `/compte` | — |
| `{{lien_compte}}` | `https://kder-ai.com/compte` | route `/compte` (D44) |
| `{{lien_guide}}` | `https://kder-ai.com/guide` | route `/guide` (D48) |

## 4. Ce que cet email ne fait pas

- **Il n'envoie aucune clé.** Une clé dans un email est une clé qui survit dans une
  boîte, un dossier, une sauvegarde — et que nous ne pouvons pas relire pour la
  reprendre.
- **Il n'annonce pas l'encaissement à l'appel** comme s'il s'agissait de l'offre
  d'une boutique : une boutique s'abonne (`X-API-Key`). Le paiement à l'appel (x402,
  0,02 USDC) est la voie des **agents**, décrite dans le guide, pas dans cet email.
- **Il ne promet aucun résultat.** Ni « 100 % de livraisons réussies », ni garantie
  de retour : le service détecte une adresse invalide, il ne fait pas arriver le
  colis.
- **Il ne fabrique pas d'urgence** (ni « offre valable 24 h », ni faux compteur) et
  n'invente aucun témoignage : le même mot d'ordre que `/pricing`, où les
  témoignages d'exemple ne sont pas publiés par défaut (D43).
- **Il dit la limite du résultat dégradé** : `degraded: true` ne veut pas dire
  « adresse invalide », et un client qui confondrait les deux retirerait une
  commande valide.
