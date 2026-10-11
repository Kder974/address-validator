#!/usr/bin/env python
"""Marque `test_mode` les transactions de recette anterieures au seuil (D40).

Pourquoi ce script existe : le registre `payment_transactions` contient deux
validations payees par l'exploitant lui-meme le 11/10/2026 (06:30 et 06:56 UTC),
comptees par /metrics/revenue comme du revenu client. La colonne `test_mode`
(D40) permet de les distinguer, mais une ligne deja ecrite ne porte
l'information que si quelqu'un la marque : c'est ce script, et rien d'autre. Il
ne devine jamais depuis le montant ni depuis la cle d'adresse du cache.

Usage (sur le VPS, depuis /opt/address-validator, venv du service) :

    .venv/bin/python scripts/migrate_test_mode.py           # simulation
    .venv/bin/python scripts/migrate_test_mode.py --apply    # ecriture

Le seuil vient de TEST_MODE_CUTOFF_UTC (defaut 2026-10-11T12:00:00+00:00) :
toute transaction anterieure est un essai de recette. Sans `--apply`, aucune
ecriture n'est faite : le script annonce ce qu'il ferait, ligne par ligne
(identifiant, preuve tronquee, montant, horodatage), verifiable ensuite en psql.

Sortie (ASCII, une ligne par information) :

    COLONNE test_mode deja presente
    CANDIDATES 2
      #1 0x-demo-tx:177000… 0.02 USDC 2026-10-11 06:30:00
    MIGRATE DRY-RUN candidates=2 cutoff=2026-10-11 12:00:00

Codes de retour : 0 reussi (y compris simulation), 2 argument invalide.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # lance depuis n'importe ou, mais .env = CWD
    sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import engine  # noqa: E402

TABLE = "payment_transactions"
NON_MARQUE = "COALESCE(test_mode, FALSE) = FALSE"


def ensure_column(cible) -> bool:
    """Ajoute `test_mode` si la base ne l'a pas encore. Renvoie True si ajoutee."""
    colonnes = {colonne["name"] for colonne in inspect(cible).get_columns(TABLE)}
    if "test_mode" in colonnes:
        return False
    ddl = (
        f"ALTER TABLE {TABLE} "
        "ADD COLUMN IF NOT EXISTS test_mode BOOLEAN NOT NULL DEFAULT FALSE"
    )
    if cible.dialect.name != "postgresql":
        # SQLite (tests) ne connait pas IF NOT EXISTS ; la colonne manque, donc
        # l'ALTER simple suffit.
        ddl = f"ALTER TABLE {TABLE} ADD COLUMN test_mode BOOLEAN DEFAULT FALSE"
    with cible.begin() as conn:
        conn.execute(text(ddl))
    return True


def main(argv: list[str]) -> int:
    parseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parseur.add_argument("--apply", action="store_true", help="ecrit la marque (sinon simulation)")
    parseur.add_argument(
        "--cutoff",
        default=None,
        help="seuil UTC ISO 8601 (defaut : TEST_MODE_CUTOFF_UTC de la configuration)",
    )
    parseur.add_argument(
        "--limit",
        type=int,
        default=0,
        help="nombre de lignes affichees (0 = toutes)",
    )
    args = parseur.parse_args(argv)

    brut = args.cutoff or get_settings().test_mode_cutoff_utc
    try:
        seuil = datetime.fromisoformat(brut)
    except ValueError as exc:
        print(f"MIGRATE FAIL seuil illisible ({brut!r}) : {exc}", file=sys.stderr)
        return 2
    if seuil.tzinfo is None:
        seuil = seuil.replace(tzinfo=timezone.utc)
    # Le registre stocke un horodatage sans fuseau, cote serveur en UTC : on
    # compare donc a une borne UTC naive, pas a une chaine a fuseau.
    borne = seuil.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")

    print(f"COLONNE test_mode {'ajoutee' if ensure_column(engine) else 'deja presente'}")

    lecture = (
        f"SELECT id, tx_hash, amount, created_at FROM {TABLE} "
        f"WHERE {NON_MARQUE} AND created_at < :borne ORDER BY created_at"
    )
    with engine.connect() as conn:
        candidates = [
            dict(ligne) for ligne in conn.execute(text(lecture), {"borne": borne}).mappings()
        ]

    print(f"CANDIDATES {len(candidates)}")
    visibles = candidates if args.limit <= 0 else candidates[: args.limit]
    for ligne in visibles:
        preuve = str(ligne.get("tx_hash") or "")[:18]
        montant = float(ligne.get("amount") or 0)
        print(f"  #{ligne.get('id')} {preuve}… {montant:.2f} USDC {ligne.get('created_at')}")

    if not args.apply:
        print(f"MIGRATE DRY-RUN candidates={len(candidates)} cutoff={borne}")
        print("Simulation : rien n'a ete ecrit. Relancer avec --apply pour marquer ces lignes.")
        return 0

    ecriture = (
        f"UPDATE {TABLE} SET test_mode = TRUE "
        f"WHERE {NON_MARQUE} AND created_at < :borne"
    )
    with engine.begin() as conn:
        resultat = conn.execute(text(ecriture), {"borne": borne})
    print(f"MIGRATE OK applied={resultat.rowcount} candidates={len(candidates)} cutoff={borne}")

    with engine.connect() as conn:
        marquees = conn.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE test_mode = TRUE")
        ).scalar_one()
        restantes = conn.execute(
            text(f"SELECT COUNT(*) FROM {TABLE} WHERE {NON_MARQUE}")
        ).scalar_one()
    print(f"VERIF marquees={marquees} non_marquees={restantes}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
