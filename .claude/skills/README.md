# Skills Claude Code du projet

Deux skills partagés par Fzed. Ils sont rangés dans `.claude/skills/`, donc Claude Code
les charge tout seul quand tu ouvres ce dossier : rien à copier.

## graphify : la carte du projet

Il transforme le dépôt en graphe de connaissances : qui appelle quoi, quels fichiers
vont ensemble, où sont les nœuds centraux. Il produit `graphify-out/` (page HTML
interactive, `graph.json`, rapport `GRAPH_REPORT.md`). Ce dossier est ignoré par git.

- **À installer une fois** (le skill appelle le paquet Python `graphifyy`, avec deux y) :
  `uv tool install graphifyy`, ou `py -m pip install graphifyy`.
- **Utilisation** dans Claude Code : `/graphify .` pour construire la carte, puis
  `/graphify query "où est vérifié le paiement x402 ?"`. Après des changements,
  `/graphify . --update` ne refait que les fichiers modifiés.
- **Version** : 0.9.25, variante Windows (`graphify install --platform windows`).
  Pour passer à une version plus récente : `graphify install --platform windows`
  installe le skill pour toutes tes sessions ; supprime alors ce dossier-ci pour ne pas
  l'avoir en double.
- **Origine et licence** : https://github.com/Graphify-Labs/graphify, Apache 2.0 (texte
  complet dans `graphify/LICENSE`). Les fichiers sont copiés sans modification.

## tour-de-controle : planifier, déléguer, vérifier

Pour les grosses tâches en plusieurs lots. Le modèle principal découpe le travail et
écrit les critères de fini **avant** de lancer quoi que ce soit. Il confie chaque lot à un
sous-agent du bon niveau (Haiku pour le mécanique, Sonnet par défaut, Opus quand il faut
du jugement), puis vérifie chaque livrable en cherchant à le refuser.

- **Utilisation** : dis « tour de contrôle » ou « orchestre cette tâche » sur une tâche
  qui se découpe en lots, par exemple brancher la Base Adresse Nationale pour les
  adresses françaises, ou passer `/pricing`, `/guide` et `/signup` au dessin de la
  nouvelle page d'accueil.
- **À éviter** sur une petite tâche : l'orchestration coûterait plus cher que le travail.
- **Origine** : skill écrit par Fzed (version 1.2, juin 2026). Dans cette copie, seule la
  phrase de la description qui renvoyait vers ses autres skills a été retirée.
