#!/usr/bin/env python
"""Purge du journal de découverte — rétention annoncée = rétention appliquée.

Ce script est l'exécuteur de la conservation annoncée dans `PRIVACY.md` §3 : il
supprime les entrées plus vieilles que `DISCOVERY_LOG_RETENTION_DAYS` (30 jours
par défaut). Il est prévu pour être lancé par une unité systemd
(`address-validator-retention.timer`, D39) ou par cron, **sans dépendance à
l'application** : ni base de données, ni réseau, ni serveur démarré.

Usage :

    .venv\\Scripts\\python.exe scripts\\prune_discovery_log.py

Sortie (une ligne, exploitable par un timer) :

    PRUNE OK <conservées> entrée(s) conservée(s), <supprimées> supprimée(s), ...

Codes de retour : 0 purge appliquée ; 2 aucun journal configuré — ce n'est pas
une erreur (rien à purger), mais cela doit être **visible** plutôt que silencieux.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import observability  # noqa: E402
from app.config import get_settings  # noqa: E402


def main(argv: list[str]) -> int:
    settings = get_settings()
    path = settings.discovery_log_path
    retention = settings.discovery_log_retention_days

    if not path:
        print("PRUNE SKIP aucun journal configure (DISCOVERY_LOG_PATH vide)")
        return 2

    file = Path(path)
    # Compte **brut** des lignes non vides : une ligne illisible (JSON casse,
    # BOM, ecriture interrompue) est retiree par la purge et doit apparaitre dans
    # le rapport. Compter les seules lignes relisibles masquerait ces pertes —
    # c'est exactement le defaut mesure au premier essai de ce script.
    raw = 0
    if file.is_file():
        raw = sum(
            1
            for line in file.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        )
    kept = observability.prune(file, retention)
    print(
        f"PRUNE OK {kept} entree(s) conservee(s), {max(raw - kept, 0)} supprimee(s), "
        f"retention {retention} j, fichier {path}"
    )
    if raw - kept > 0:
        print(
            "PRUNE NOTE les lignes supprimees comprennent d'eventuelles lignes "
            "illisibles (non datables, donc non conservables sous une retention datee)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
