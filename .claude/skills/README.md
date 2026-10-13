# Skills Claude Code du projet

Un skill partagé par Fzed. Il est rangé dans `.claude/skills/`, donc Claude Code
le charge tout seul quand tu ouvres ce dossier : rien à copier.

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
