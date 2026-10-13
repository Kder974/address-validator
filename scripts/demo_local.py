#!/usr/bin/env python3
"""Démonstration locale : le vrai serveur, une base SQLite jetable.

À quoi ça sert. La production tourne sur PostgreSQL (`DATABASE_URL`) et
`app/database.py` crée son moteur avec `connect_args={"connect_timeout": 2}`,
propre à psycopg2 : l'application ne peut donc pas démarrer sur SQLite telle
quelle — et c'est très bien ainsi. Mais il est utile de **voir** les pages
publiques et l'espace client sur son poste, sans installer PostgreSQL.

    .venv\\Scripts\\python.exe scripts\\demo_local.py .\\demo.sqlite 8002
    # puis : http://127.0.0.1:8002/pricing  (et /signup, /compte, /validate-form)

Ce que ce script remplace : **une seule chose**, la fabrique de sessions des
comptes clients (`app.routes.signup.SessionLocal`), par une session SQLite dont
les tables sont créées depuis les modèles ORM (elles n'ont aucun type
PostgreSQL). Tout le reste est le code de production : mêmes routes, même rendu,
même plafond d'IP, même CSP. Les tables d'adresses (`JSONB`) ne sont pas créées :
`init_db()` échoue donc au démarrage, l'avertissement est **attendu** et
n'empêche rien (le démarrage est non bloquant, D16).

Ce fichier n'est déployé nulle part : ce n'est pas du code de service, c'est un
harnais de démonstration. Pour mesurer le service réel, c'est
`scripts/e2e_check.py` sur le VPS qui fait foi.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Lancé depuis `scripts/`, le dossier racine n'est pas dans sys.path : on l'ajoute
# pour pouvoir importer le paquet `app` (même amorçage que `e2e_check.py`).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import Customer, CustomerValidation  # noqa: E402
from app.routes import signup as signup_route  # noqa: E402


def main(argv: list[str]) -> int:
    base = Path(argv[0] if argv else "demo.sqlite").resolve()
    port = int(argv[1]) if len(argv) > 1 else 8002

    moteur = create_engine(f"sqlite:///{base}")
    Customer.__table__.create(moteur, checkfirst=True)
    CustomerValidation.__table__.create(moteur, checkfirst=True)
    signup_route.SessionLocal = sessionmaker(bind=moteur)

    # Import après le branchement : la route doit voir la base de démonstration.
    from app.main import app

    print(f"demo: base={base} url=http://127.0.0.1:{port}/pricing", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
