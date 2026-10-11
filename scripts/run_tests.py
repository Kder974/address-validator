#!/usr/bin/env python
"""Oracle unique des portes pytest du ledger (GATES.md).

Deux pièges rendaient la porte pytest non décisive :

1. `EXPECT: passed` : pytest écrit « 1 failed, 42 passed » — le mot « passed »
   figure donc encore dans une suite rouge.
2. La sortie humaine n'est pas exploitable : **pytest 8.3.3 lancé avec la sortie
   redirigée et `-q` n'imprime aucun récapitulatif** (`2 passed in 0.3s` absent
   de stdout, stderr vide, `PYTEST_ADDOPTS` vide — vérifié le 10/10/2026). Un
   oracle qui compte les mots de cette ligne compte du vide.

Ce script s'appuie donc sur une source machine : `--junitxml` (XML standard, lu
avec `xml.etree` de la stdlib) + le code de sortie. Il n'écrit `GATE OK pytest`
que si quatre conditions sont réunies : code de sortie 0, zéro `failure`, zéro
`error`, et au moins un test exécuté. Tout doute — rapport absent ou illisible —
est un échec, pas un succès.

    .venv\\Scripts\\python.exe scripts\\run_tests.py                     # tout
    .venv\\Scripts\\python.exe scripts\\run_tests.py tests\\test_cache.py # un module

Sortie : le rapport pytest intégral (humain), puis une ligne de verdict
`GATE OK pytest: <n> passed, 0 failed, <n> skipped` (code 0)
`GATE FAIL pytest: <n> passed, <n> failing, ...` (code 1).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OK = "GATE OK pytest"
KO = "GATE FAIL pytest"


def _totals(report: Path) -> dict[str, int]:
    """Lit les totaux du rapport JUnit XML. Lève si le rapport est inexploitable."""
    root = ET.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        raise ValueError("aucun <testsuite> dans le rapport")

    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
    return totals


def main(argv: list[str]) -> int:
    args = list(argv)
    if "-q" not in args and "-v" not in args:
        args = ["-q", "--tb=short", *args]

    handle, name = tempfile.mkstemp(prefix="pytest-junit-", suffix=".xml")
    os.close(handle)
    report = Path(name)
    if not any(arg.startswith("--junitxml") for arg in args):
        args.append(f"--junitxml={report}")

    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        sys.stdout.write(output if output.endswith("\n") else output + "\n")

        try:
            totals = _totals(report)
        except Exception as exc:  # rapport absent, tronqué, illisible : échec fermé
            print(f"{KO}: rapport junit illisible ({exc}) exit={proc.returncode}")
            return 1

        passed = totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]

        if (
            proc.returncode == 0
            and totals["failures"] == 0
            and totals["errors"] == 0
            and totals["tests"] > 0
        ):
            print(
                f"{OK}: {passed} passed, 0 failed, {totals['skipped']} skipped "
                f"({totals['tests']} collectés, exit=0)"
            )
            return 0

        print(
            f"{KO}: {passed} passed, {totals['failures'] + totals['errors']} failing, "
            f"{totals['skipped']} skipped, {totals['tests']} collectés, exit={proc.returncode}"
        )
        return 1
    finally:
        report.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
