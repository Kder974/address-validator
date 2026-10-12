# Benchmarks, économie du canal et prochaines étapes

Ce document rassemble ce qu'on peut **attendre** d'une campagne de ce type, ce
qu'elle coûte, et l'ordre des décisions qui suivent. Les fourchettes données sont
des ordres de grandeur publiés par les éditeurs d'outils et observés en
prospection B2B froide : elles servent à **détecter une anomalie**, pas à
planifier un chiffre d'affaires. Seuls les chiffres mesurés par
`analyze_results.py` font foi.

---

## 1. Ce qu'on peut attendre

| Indicateur | Fourchette usuelle en froid B2B | Ce qu'une valeur hors fourchette signifie |
|---|---|---|
| Délivrabilité (messages reçus) | > 90 % | en dessous : SPF/DKIM/DMARC à vérifier, volume à baisser |
| Taux d'ouverture | 20 à 40 % | **sous 20 %** : la délivrabilité est en cause, pas le corps du message |
| Taux de réponse | 1 à 5 % (souvent 1 à 3 %) | **sous 0,5 %** : objet ou ciblage à revoir ; **au-dessus de 10 %** : vérifier que le suivi est complet avant de se réjouir |
| Réponses → rendez-vous | 20 à 40 % | une réponse sans suite est un problème de proposition, pas de volume |
| Rebonds durs | < 2 % | au-delà : la liste est de mauvaise qualité, il faut purger avant d'envoyer |
| Désinscriptions | < 1 % | au-delà : le ciblage est trop large, arrêter et resserrer |

Le script d'analyse applique une règle stricte : **moins de 20 envois par
variante, aucune conclusion.** Il faut donc environ 60 messages (3 variantes × 20)
avant que l'A/B testing d'objet veuille dire quelque chose, et plus de 200 envois
avant que les écarts de secteur deviennent exploitables.

### Volume attendu, semaine par semaine

| Étape | Envois | Ce qu'on regarde |
|---|---|---|
| Semaine 1 | 10 par jour | délivrabilité, rebonds, ton des premières réponses |
| Semaines 2-3 | 30 à 50 par jour | taux d'ouverture et de réponse par objet (A/B) |
| À partir de la semaine 4 | 50 par jour max | secteurs les plus réactifs, premiers clients |

---

## 2. L'arithmétique du modèle (à regarder en face)

Hypothèses prudentes : 400 messages par mois, 2 % de réponses, un tiers des
réponses converties.

| Grandeur | Valeur | Commentaire |
|---|---|---|
| Messages envoyés / mois | 400 | ≈ 20 par jour ouvré |
| Réponses | ~8 | taux de réponse 2 % |
| Clients | ~2 à 3 | un tiers des réponses |
| Revenu par client (0,02 $/validation) | 20 $/mois | s'il fait 1 000 validations par mois |
| **Revenu attendu** | **~40 à 60 $/mois** | pour ~2 à 5 heures de travail par semaine |
| Coût des outils | 0 à 50 $/mois | 0 $ avec `envoyer_smtp.py`, ~47 $ avec Instantly |

Deux conclusions, et elles comptent plus que le reste de ce document :

1. **Le prix à la requête ne porte pas le canal** tel quel. Ce n'est pas un
   problème de prospection, c'est un problème de tarif : à 0,02 $, il faut des
   clients qui valident des milliers d'adresses par mois, ou une offre qui se
   facture autrement.
2. **L'argument à vendre n'est pas le prix, c'est l'échec évité.** Un commerçant
   qui subit 10 échecs de livraison par mois perd 170 à 300 $. Une offre à 30 ou
   50 $/mois pour 2 000 validations est défendable ; 0,02 $ la requête ne dit rien
   à personne.

Conséquence pratique : dans le message, proposer **une offre mensuelle simple**
(par exemple « jusqu'à 2 000 validations, X USDC par mois ») plutôt qu'un tarif
à la requête. Le gabarit actuel cite le prix par validation parce que c'est
l'offre publiée par l'API : le jour où un pack existe dans `/manifest`, c'est le
pack qu'il faut citer.

---

## 3. Outils : quand payer

| Situation | Ce qui suffit |
|---|---|
| Moins de 100 messages/mois, un seul domaine | `envoyer_smtp.py` + un SMTP (Brevo gratuit) — 0 € |
| 100 à 500 messages/mois | un outil avec warmup et rotation (Mailshake Starter ~25 $, Instantly Growth 47 $) |
| Plusieurs domaines, plusieurs envois quotidiens | Instantly (comptes illimités) ou Lemlist Multichannel si LinkedIn compte |

Détail des prix et des fonctions : `README.md` §6.

---

## 4. Points de vigilance

* **Délivrabilité avant tout le reste.** Un taux d'ouverture sous 20 % se corrige
  sur le domaine (SPF, DKIM, DMARC, warmup, volume), jamais dans le corps du message.
* **RGPD** : information et droit d'opposition en B2B, consentement en B2C,
  conservation 3 ans appliquée (`--purger-avant`), refus honorés le jour même.
  Voir `README.md` §7.
* **Qualité de la donnée** : l'annuaire ne donne pas d'adresse électronique. Une
  liste « complétée » à la va-vite produit des rebonds, et les rebonds coûtent la
  réputation du domaine.
* **Temps réel** : compter 2 à 5 heures par semaine (sourcing, relecture des
  emails générés, suivi des réponses). Sous-estimer ce temps est l'erreur la plus
  fréquente de ce type de mise en place.
* **Un seul objectif par message.** Le premier email cherche une réponse, pas une
  signature.

---

## 5. Prochaines étapes recommandées

1. **Faire un essai réel de 10 envois** avec `data/campagne_exemple.csv` (5
   prospects fictifs ne suffisent pas) : sourcer 10 boutiques réelles, compléter
   les adresses, générer, envoyer depuis `contact@kder-ai.com`.
2. **Publier SPF, DKIM et DMARC** sur `kder-ai.com` si ce n'est pas déjà fait,
   et vérifier avec un test de délivrabilité avant d'élargir.
3. **Mesurer 30 jours** à 50 envois par jour maximum, en important l'export de
   l'outil chaque semaine (`track_responses.py`), puis `analyze_results.py`.
4. **Décider le modèle commercial** à partir de ces chiffres : pack mensuel
   (2 000 validations) plutôt que prix à la requête, et mise à jour du message en
   conséquence.
5. **Industrialiser le sourcing** : une commande par semaine
   (`sourcer_prospects.py --secteur X --departement Y --limite 100`), et un tiers
   du temps consacré à la complétion des adresses.
6. **Payer un outil** seulement au-delà de 100 messages par mois, et seulement
   pour le warmup et la rotation.
