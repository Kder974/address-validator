# Exemple de campagne — 5 prospects fictifs

Produit par `generate_emails.py` à partir de `data/prospects_exemple.csv`. Ces
cinq boutiques n'existent pas, aucune adresse n'est réelle et aucun email n'a été
envoyé : c'est la démonstration du rendu des gabarits, relue ligne par ligne.

Pour reproduire ce fichier :

```powershell
Remove-Item prospection\data\prospects.db* -Force
& $py prospection\scripts\import_prospects.py --input prospection\data\prospects_exemple.csv
& $py prospection\scripts\generate_emails.py --sortie prospection\data\campagne_exemple.csv
```

## Beauté Naturelle — contact@beaute-naturelle.fr

- secteur : `cosmetiques` · plateforme : `PrestaShop` · variante d'objet retenue : 2
- produit détecté : produits de beauté
- accroche spécifique : J'ai noté que votre boutique tourne sous PrestaShop.

**Trois objets proposés (A/B testing)**

1. Question sur vos livraisons Beauté Naturelle
2. Problème d'adresses chez Beauté Naturelle ?
3. Sophie, une question rapide sur Beauté Naturelle

**Objet retenu pour ce prospect** : Problème d'adresses chez Beauté Naturelle ?

```text
Bonjour Sophie,

J'ai vu que votre boutique Beauté Naturelle vend des produits de beauté.

J'ai noté que votre boutique tourne sous PrestaShop.

J'ai une question rapide : est-ce que vous perdez parfois des ventes parce qu'un client a saisi une adresse que le transporteur n'arrive pas à livrer ?

Je demande parce que je développe un outil qui vérifie les adresses avant l'expédition. Ça coûte 0,02 $ par validation, et ça peut vous faire économiser 17 à 30 $ par livraison échouée.

Si vous voulez, je peux vous montrer un exemple sur votre propre site, sans engagement. Ça vous dit ?

Kder Daoud
Fondateur, Address Validator
https://kder-ai.com
contact@kder-ai.com

Si vous ne souhaitez pas être recontacté, répondez simplement « stop » : je vous retire de ma liste immédiatement.
```

## Déco Maison — info@deco-maison.fr

- secteur : `decoration` · plateforme : `Shopify` · variante d'objet retenue : 3
- produit détecté : articles de décoration et de maison
- accroche spécifique : En parcourant votre site, j'ai vu que vous travaillez surtout sur décoration. J'ai aussi noté que votre boutique tourne sous Shopify.

**Trois objets proposés (A/B testing)**

1. Question sur vos livraisons Déco Maison
2. Problème d'adresses chez Déco Maison ?
3. Pierre, une question rapide sur Déco Maison

**Objet retenu pour ce prospect** : Pierre, une question rapide sur Déco Maison

```text
Bonjour Pierre,

J'ai vu que votre boutique Déco Maison vend des articles de décoration et de maison.

En parcourant votre site, j'ai vu que vous travaillez surtout sur décoration. J'ai aussi noté que votre boutique tourne sous Shopify.

J'ai une question rapide : est-ce que vous perdez parfois des ventes parce qu'un client a saisi une adresse que le transporteur n'arrive pas à livrer ?

Je demande parce que je développe un outil qui vérifie les adresses avant l'expédition. Ça coûte 0,02 $ par validation, et ça peut vous faire économiser 17 à 30 $ par livraison échouée.

Si vous voulez, je peux vous montrer un exemple sur votre propre site, sans engagement. Ça vous dit ?

Kder Daoud
Fondateur, Address Validator
https://kder-ai.com
contact@kder-ai.com

Si vous ne souhaitez pas être recontacté, répondez simplement « stop » : je vous retire de ma liste immédiatement.
```

## TechGadget — hello@techgadget.fr

- secteur : `high-tech` · plateforme : `WooCommerce` · variante d'objet retenue : 1
- produit détecté : produits high-tech
- accroche spécifique : J'ai noté que votre boutique tourne sous WooCommerce.

**Trois objets proposés (A/B testing)**

1. Question sur vos livraisons TechGadget
2. Problème d'adresses chez TechGadget ?
3. Marie, une question rapide sur TechGadget

**Objet retenu pour ce prospect** : Question sur vos livraisons TechGadget

```text
Bonjour Marie,

J'ai vu que votre boutique TechGadget vend des produits high-tech.

J'ai noté que votre boutique tourne sous WooCommerce.

J'ai une question rapide : est-ce que vous perdez parfois des ventes parce qu'un client a saisi une adresse que le transporteur n'arrive pas à livrer ?

Je demande parce que je développe un outil qui vérifie les adresses avant l'expédition. Ça coûte 0,02 $ par validation, et ça peut vous faire économiser 17 à 30 $ par livraison échouée.

Si vous voulez, je peux vous montrer un exemple sur votre propre site, sans engagement. Ça vous dit ?

Kder Daoud
Fondateur, Address Validator
https://kder-ai.com
contact@kder-ai.com

Si vous ne souhaitez pas être recontacté, répondez simplement « stop » : je vous retire de ma liste immédiatement.
```

## Ma Boutique de Montres — contact@ma-boutique-montres.fr

- secteur : `mode` · plateforme : `Shopify` · variante d'objet retenue : 1
- produit détecté : montres
- accroche spécifique : J'ai noté que votre boutique tourne sous Shopify.

**Trois objets proposés (A/B testing)**

1. Question sur vos livraisons Ma Boutique de Montres
2. Problème d'adresses chez Ma Boutique de Montres ?
3. Jean, une question rapide sur Ma Boutique de Montres

**Objet retenu pour ce prospect** : Question sur vos livraisons Ma Boutique de Montres

```text
Bonjour Jean,

J'ai vu que votre boutique Ma Boutique de Montres vend des montres.

J'ai noté que votre boutique tourne sous Shopify.

J'ai une question rapide : est-ce que vous perdez parfois des ventes parce qu'un client a saisi une adresse que le transporteur n'arrive pas à livrer ?

Je demande parce que je développe un outil qui vérifie les adresses avant l'expédition. Ça coûte 0,02 $ par validation, et ça peut vous faire économiser 17 à 30 $ par livraison échouée.

Si vous voulez, je peux vous montrer un exemple sur votre propre site, sans engagement. Ça vous dit ?

Kder Daoud
Fondateur, Address Validator
https://kder-ai.com
contact@kder-ai.com

Si vous ne souhaitez pas être recontacté, répondez simplement « stop » : je vous retire de ma liste immédiatement.
```

## Sport & Loisirs — hello@sport-loisirs.fr

- secteur : `sport` · plateforme : `Shopify` · variante d'objet retenue : 3
- produit détecté : articles de sport
- accroche spécifique : En parcourant votre site, j'ai vu que vous travaillez surtout sur sport. J'ai aussi noté que votre boutique tourne sous Shopify.

**Trois objets proposés (A/B testing)**

1. Question sur vos livraisons Sport & Loisirs
2. Problème d'adresses chez Sport & Loisirs ?
3. Lucas, une question rapide sur Sport & Loisirs

**Objet retenu pour ce prospect** : Lucas, une question rapide sur Sport & Loisirs

```text
Bonjour Lucas,

J'ai vu que votre boutique Sport & Loisirs vend des articles de sport.

En parcourant votre site, j'ai vu que vous travaillez surtout sur sport. J'ai aussi noté que votre boutique tourne sous Shopify.

J'ai une question rapide : est-ce que vous perdez parfois des ventes parce qu'un client a saisi une adresse que le transporteur n'arrive pas à livrer ?

Je demande parce que je développe un outil qui vérifie les adresses avant l'expédition. Ça coûte 0,02 $ par validation, et ça peut vous faire économiser 17 à 30 $ par livraison échouée.

Si vous voulez, je peux vous montrer un exemple sur votre propre site, sans engagement. Ça vous dit ?

Kder Daoud
Fondateur, Address Validator
https://kder-ai.com
contact@kder-ai.com

Si vous ne souhaitez pas être recontacté, répondez simplement « stop » : je vous retire de ma liste immédiatement.
```

